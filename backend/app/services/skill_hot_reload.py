"""技能热加载（四期）：skills 目录变更后无需重启即可生效。

reload_once() 是幂等重扫（测试与手动端点共用）：
  1. prompt .md 重扫 + 覆盖注册（SkillRegistry.register 对同名 override）
  2. 清理已删除的 .md 对应的残留注册
  3. 第三方 .py 技能 force 重扫
  4. agents.registry 的实例缓存失效

注意：不能用 SkillRegistry.reset()——builtin 技能靠 import 时 @register
注册，模块已缓存不会重跑，reset 后 ensure_builtin 救不回来。

start() 起 watchfiles 守护线程监听 skills 目录；
配置 Settings.skill_hot_reload=false（默认）时完全不启动，行为与四期前一致。
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_watcher_thread: threading.Thread | None = None
_stop_event = threading.Event()
_lock = threading.Lock()


def _skills_dirs() -> list[Path]:
    from app.core import prompt_skills

    d = prompt_skills._SKILLS_DIR
    ud = prompt_skills._USER_SKILLS_DIR
    dirs = [Path(d)] if d and Path(d).exists() else []
    if ud and Path(ud).exists():
        dirs.append(Path(ud))
    return dirs


def reload_once() -> dict[str, int]:
    """幂等重扫。返回统计。"""
    with _lock:
        from app.agents import registry as agent_registry
        from app.core import prompt_skills
        from app.core.skill_registry import SkillRegistry

        registry = SkillRegistry.instance()
        before = len(registry.all_skill_classes())

        # 1) prompt .md 重扫（同名覆盖注册）
        prompt_skills._scanned = False
        fresh = prompt_skills.scan_and_register_prompt_skills()
        fresh_names = {(s.node_type, s.name) for s in fresh}

        # 1b) 用户 skill .md 重扫
        fresh_user = prompt_skills.scan_and_register_user_prompt_skills()
        fresh_user_names = {(s.node_type, s.name, getattr(s, "owner_user_id", "")) for s in fresh_user}

        # 2) 清理已删除 .md 的残留注册（prompt 来源的技能：module 前缀可识别）
        removed = 0
        for node_type in list(registry._skills.keys()):
            for skill_name in list(registry._skills[node_type].keys()):
                cls = registry._skills[node_type][skill_name]
                module = getattr(cls, "__module__", "")
                if module.startswith("app.core.prompt_skills."):
                    if (node_type, skill_name) not in fresh_names:
                        if registry.unregister(node_type, skill_name):
                            removed += 1

        # 2b) 清理用户级残留注册
        for uid in list(registry._user_skills.keys()):
            for node_type in list(registry._user_skills[uid].keys()):
                for skill_name in list(registry._user_skills[uid][node_type].keys()):
                    if (node_type, skill_name, uid) not in fresh_user_names:
                        if registry.unregister(node_type, skill_name, user_id=uid):
                            removed += 1

        # 3) 第三方 .py force 重扫
        third_party = registry.scan_third_party(force=True)

        # 4) agents 侧实例缓存失效
        agent_registry._skills_cache = None

        after = len(registry.all_skill_classes())
        stats = {
            "before": before,
            "after": after,
            "prompt_md": len(fresh),
            "removed_stale": removed,
            "third_party": third_party,
        }
        logger.info(f"[skill-hot-reload] reloaded: {stats}")
        return stats


def start(interval_debounce: float = 1.0) -> bool:
    """起 watchfiles 守护线程。已启动返回 False。"""
    global _watcher_thread
    if _watcher_thread is not None and _watcher_thread.is_alive():
        return False

    dirs = _skills_dirs()
    if not dirs:
        logger.warning("[skill-hot-reload] skills dir not found, watcher not started")
        return False

    from watchfiles import watch

    _stop_event.clear()

    def _loop() -> None:
        try:
            for _changes in watch(*dirs, stop_event=_stop_event, debounce=int(interval_debounce * 1000)):
                try:
                    reload_once()
                except Exception:
                    logger.exception("[skill-hot-reload] reload failed on change")
        except Exception:
            logger.exception("[skill-hot-reload] watcher crashed")

    _watcher_thread = threading.Thread(target=_loop, name="skill-hot-reload", daemon=True)
    _watcher_thread.start()
    logger.info(f"[skill-hot-reload] watching {dirs}")
    return True


def stop() -> None:
    _stop_event.set()