"""Skill 注册表 + 第三方插件扫描加载器。

架构：
- 全局单例 SkillRegistry，按 (node_type, skill_name) 索引 Skill 子类
- 内置 Skill 在模块导入时显式注册（@register 装饰器或 register() 调用）
- 第三方 Skill 通过扫描 backend/skills/ 目录自动加载
- 节点运行时调用 get_skill(node_type, skill_name) 加载对应实现

第三方 Skill 开发约定：
1. 在 backend/skills/ 目录下创建 .py 文件（如 my_copywrite.py）
2. 文件内定义 Skill 子类，声明 node_type + name + display_name
3. 在文件末尾用 @register 装饰器注册（或调用 SkillRegistry.instance().register(MySkill)）
4. 重启后端即可生效，无需修改 app/ 内任何代码

示例第三方 Skill：

    # backend/skills/my_copywrite.py
    from app.core.skill_base import Skill
    from app.core.skill_registry import register

    @register
    class PoeticCopywriteSkill(Skill):
        node_type = "copywrite"
        name = "poetic"
        display_name = "诗意文案"
        description = "诗意化的文案风格，适合文艺类内容"

        async def execute(self, inputs):
            # 自定义实现
            return {"title": "...", "content": "...", "tags": [...]}
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import sys
from pathlib import Path
from typing import Any

from app.core.skill_base import Skill

logger = logging.getLogger(__name__)


KERNEL_SKILL_MODULES = (
    "app.skills.file_ops.file_ops",
    "app.core.prompt_skills",
    "app.skills.analyze.vl_analyze",
    "app.skills.analyze.comment_insights",
    "app.skills.produce.card_compose",
    "app.skills.evolve.skill_create",
    "app.engine.collab.tools",
)


def _load_capability_skill_modules() -> tuple[str, ...]:
    """从 capability.toml 读取能力 Skill 模块路径；toml 不存在则走硬编码兼容。"""
    try:
        from app.core.capability_loader import load_capability_skills
        cap_skills = load_capability_skills()
        if cap_skills:
            return tuple(cap_skills)
    except Exception as e:
        logger.warning(f"[skill_registry] capability.toml load failed, using fallback: {e}")
    return (
        "app.skills.copywrite.builder",
        "capabilities.content_search.skills.trending_search",
        "capabilities.content_search.skills.xhs_search",
        "capabilities.content_search.skills.spider_sync",
        "capabilities.content_search.skills.url_collect",
        "capabilities.image_gen.skills.card_image_gen",
        "capabilities.video.skills.video_gen",
        "capabilities.video.skills.video_compose",
        "capabilities.video.skills.auto_short_video",
        "capabilities.video.skills.smart_cut",
        "capabilities.video.skills.talking_head_overlay",
        "capabilities.video.skills.video_studio",
        "capabilities.video.skills.talking_head_demo",
        "capabilities.video.skills.multi_image_video",
        "capabilities.video.skills.meme_render",
        "capabilities.video.skills.tts2",
        "capabilities.messaging.skills.wechat_send_text",
        "capabilities.messaging.skills.wechat_send_file",
        "capabilities.messaging.skills.feishu_file_ops",
        "capabilities.messaging.skills.feishu_wiki_doc",
        "capabilities.messaging.skills.feishu_bitable",
        "capabilities.messaging.skills.email_send",
    )


CAPABILITY_SKILL_MODULES = _load_capability_skill_modules()
BUILTIN_SKILL_MODULES = KERNEL_SKILL_MODULES + CAPABILITY_SKILL_MODULES


def ensure_builtin_skills_registered() -> None:
    """确保内置 Skill 模块已导入并完成 @register。"""
    for module_name in BUILTIN_SKILL_MODULES:
        importlib.import_module(module_name)
    from app.core.prompt_skills import scan_and_register_prompt_skills, scan_and_register_user_prompt_skills
    scan_and_register_prompt_skills()
    scan_and_register_user_prompt_skills()


class SkillRegistry:
    """全局 Skill 注册表（单例）。

    索引结构：
    - _skills[node_type][skill_name] = SkillClass  （系统级，owner_user_id=""）
    - _user_skills[user_id][node_type][skill_name] = SkillClass  （用户级，自进化产生）

    查询优先级：用户级 > 系统级（用户自进化 skill 可覆盖同名系统 skill 行为）。
    """

    def __init__(self) -> None:
        self._skills: dict[str, dict[str, type[Skill]]] = {}
        self._user_skills: dict[str, dict[str, dict[str, type[Skill]]]] = {}
        self._third_party_scanned: bool = False

    @classmethod
    def instance(cls) -> SkillRegistry:
        global _GLOBAL_REGISTRY
        if _GLOBAL_REGISTRY is None:
            _GLOBAL_REGISTRY = SkillRegistry()
        return _GLOBAL_REGISTRY

    def register(self, skill_cls: type[Skill]) -> type[Skill]:
        if not isinstance(skill_cls, type) or not issubclass(skill_cls, Skill):
            raise TypeError(f"register() expects Skill subclass, got {skill_cls!r}")
        node_type = skill_cls.node_type
        skill_name = skill_cls.name
        if not node_type or not skill_name:
            raise ValueError(
                f"Skill {skill_cls.__name__} must declare non-empty "
                f"node_type and name (got node_type={node_type!r}, name={skill_name!r})"
            )

        owner = getattr(skill_cls, "owner_user_id", "") or ""

        if owner:
            self._user_skills.setdefault(owner, {})
            self._user_skills[owner].setdefault(node_type, {})
            existing = self._user_skills[owner][node_type].get(skill_name)
            if existing is not None and existing is not skill_cls:
                logger.debug(
                    f"[SkillRegistry] override existing user skill: "
                    f"user={owner} {node_type}.{skill_name} {existing.__name__} -> {skill_cls.__name__}"
                )
            self._user_skills[owner][node_type][skill_name] = skill_cls
            logger.debug(
                f"[SkillRegistry] registered user skill: user={owner} {node_type}.{skill_name} "
                f"-> {skill_cls.__name__}"
            )
        else:
            self._skills.setdefault(node_type, {})
            existing = self._skills[node_type].get(skill_name)
            if existing is not None and existing is not skill_cls:
                logger.debug(
                    f"[SkillRegistry] override existing skill: "
                    f"{node_type}.{skill_name} {existing.__name__} -> {skill_cls.__name__}"
                )
            self._skills[node_type][skill_name] = skill_cls
            logger.debug(
                f"[SkillRegistry] registered: {node_type}.{skill_name} "
                f"-> {skill_cls.__name__}"
            )
        return skill_cls

    def get(self, node_type: str, skill_name: str, user_id: str = "") -> type[Skill] | None:
        if user_id:
            user_skill = self._user_skills.get(user_id, {}).get(node_type, {}).get(skill_name)
            if user_skill is not None:
                return user_skill
        return self._skills.get(node_type, {}).get(skill_name)

    def get_default(self, node_type: str) -> type[Skill] | None:
        skills_map = self._skills.get(node_type)
        if not skills_map:
            return None
        return next(iter(skills_map.values()))

    def list_by_node(self, node_type: str, user_id: str = "") -> list[dict[str, Any]]:
        skills_map = dict(self._skills.get(node_type, {}))
        if user_id:
            user_map = self._user_skills.get(user_id, {}).get(node_type, {})
            skills_map.update(user_map)
        return [cls.metadata() for cls in skills_map.values()]

    def list_all(self, user_id: str = "") -> dict[str, list[dict[str, Any]]]:
        result: dict[str, list[dict[str, Any]]] = {}
        for node_type, skills_map in self._skills.items():
            merged = dict(skills_map)
            if user_id:
                user_map = self._user_skills.get(user_id, {}).get(node_type, {})
                merged.update(user_map)
            result[node_type] = [cls.metadata() for cls in merged.values()]
        if user_id:
            for node_type, skills_map in self._user_skills.get(user_id, {}).items():
                if node_type not in result:
                    result[node_type] = [cls.metadata() for cls in skills_map.values()]
        return result

    def find_by_name(self, name: str, user_id: str = "") -> list[type[Skill]]:
        matches: list[type[Skill]] = []
        if user_id:
            for skills_map in self._user_skills.get(user_id, {}).values():
                skill_cls = skills_map.get(name)
                if skill_cls is not None:
                    matches.append(skill_cls)
        for skills_map in self._skills.values():
            skill_cls = skills_map.get(name)
            if skill_cls is not None:
                matches.append(skill_cls)
        return matches

    def find_by_description(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        import jieba

        query_words = set(jieba.cut(query)) - {" ", "\n", "的", "了", "是", "在", "和", "我", "要", "把", "被", "让", "用"}
        candidates: list[dict[str, Any]] = []

        for node_type, skills_map in self._skills.items():
            for skill_cls in skills_map.values():
                desc = getattr(skill_cls, "description", "") or ""
                name = getattr(skill_cls, "name", "") or ""
                display_name = getattr(skill_cls, "display_name", "") or ""
                trigger_words = getattr(skill_cls, "trigger_words", []) or []

                desc_words = set(jieba.cut(desc + " " + display_name + " " + " ".join(trigger_words)))
                desc_words -= {" ", "\n", "的", "了", "是", "在", "和", "我", "要", "把", "被", "让", "用"}

                if not query_words or not desc_words:
                    continue

                intersection = query_words & desc_words
                union = query_words | desc_words
                score = len(intersection) / len(union) if union else 0.0

                name_match = 1.0 if any(w in name for w in query_words if len(w) > 1) else 0.0
                score = max(score, name_match * 0.8)

                if score > 0:
                    candidates.append({
                        "skill": skill_cls,
                        "name": name,
                        "node_type": node_type,
                        "description": desc[:100],
                        "score": round(score, 3),
                    })

        candidates.sort(key=lambda x: x["score"], reverse=True)
        return candidates[:top_k]

    def all_skill_classes(self) -> list[type[Skill]]:
        seen: dict[str, type[Skill]] = {}
        for skills_map in self._skills.values():
            for name, skill_cls in skills_map.items():
                seen.setdefault(name, skill_cls)
        return list(seen.values())

    def all_trigger_words_for_non_local_platforms(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for skills_map in self._skills.values():
            for skill_cls in skills_map.values():
                platform = getattr(skill_cls, "platform", "") or ""
                if platform and platform != "local":
                    for tw in getattr(skill_cls, "trigger_words", []) or []:
                        if tw and tw not in result:
                            result[tw] = platform
        return result

    def scan_third_party(self, force: bool = False) -> int:
        if self._third_party_scanned and not force:
            return 0

        backend_root = Path(__file__).resolve().parent.parent.parent
        skills_dir = backend_root / "skills"

        if not skills_dir.exists():
            logger.debug(f"[SkillRegistry] third-party skills dir not found: {skills_dir}")
            self._third_party_scanned = True
            return 0

        logger.debug(f"[SkillRegistry] scanning third-party skills: {skills_dir}")
        loaded = 0
        for entry in sorted(skills_dir.iterdir()):
            if entry.name.startswith("__"):
                continue
            if entry.is_file() and not entry.name.endswith(".py"):
                continue
            if entry.is_dir() and not (entry / "__init__.py").exists():
                continue

            module_name = self._load_module(entry)
            if module_name:
                loaded += 1

        self._third_party_scanned = True
        logger.debug(f"[SkillRegistry] third-party scan done, loaded {loaded} module(s)")
        return loaded

    def _load_module(self, path: Path) -> str | None:
        if path.is_file():
            module_name = f"_third_party_skill_{path.stem}_file"
            file_path = path
        else:
            module_name = f"_third_party_skill_{path.name}_pkg"
            file_path = path / "__init__.py"

        try:
            spec = importlib.util.spec_from_file_location(module_name, file_path)
            if spec is None or spec.loader is None:
                logger.warning(f"[SkillRegistry] cannot load module: {file_path}")
                return None
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
            logger.debug(f"[SkillRegistry] loaded third-party module: {module_name}")
            return module_name
        except Exception as e:
            logger.exception(
                f"[SkillRegistry] failed to load third-party skill {file_path}: {e}"
            )
            return None

    def reset(self) -> None:
        self._skills.clear()
        self._user_skills.clear()
        self._third_party_scanned = False

    def unregister(self, node_type: str, skill_name: str, user_id: str = "") -> bool:
        if user_id:
            user_map = self._user_skills.get(user_id, {})
            skills_map = user_map.get(node_type)
            if skills_map is None or skill_name not in skills_map:
                return False
            del skills_map[skill_name]
            if not skills_map:
                del user_map[node_type]
            if not user_map:
                del self._user_skills[user_id]
            logger.debug(f"[SkillRegistry] unregistered user skill: user={user_id} {node_type}.{skill_name}")
            return True

        skills_map = self._skills.get(node_type)
        if skills_map is None or skill_name not in skills_map:
            return False
        skill_cls = skills_map[skill_name]
        module_name = getattr(skill_cls, "__module__", "")
        _hot_removable = (
            module_name.startswith("_third_party_skill_")
            or module_name.startswith("app.core.prompt_skills.")
        )
        if not _hot_removable:
            logger.warning(f"[SkillRegistry] cannot unregister builtin skill: {node_type}.{skill_name}")
            return False
        del skills_map[skill_name]
        if not skills_map:
            del self._skills[node_type]
        logger.debug(f"[SkillRegistry] unregistered: {node_type}.{skill_name}")
        return True


_GLOBAL_REGISTRY: SkillRegistry | None = None


def register(skill_cls: type[Skill]) -> type[Skill]:
    return SkillRegistry.instance().register(skill_cls)


def get_skill_class(node_type: str, skill_name: str | None, user_id: str = "") -> type[Skill] | None:
    registry = _ensure_registry_loaded()

    if skill_name:
        return registry.get(node_type, skill_name, user_id=user_id)
    return registry.get_default(node_type)


def get_skill_class_by_name(name: str, user_id: str = "") -> type[Skill] | None:
    """按 name 查 Skill 子类。

    支持限定名 `node_type.skill_name`（如 `analyze.comment_insights`）：
    不同 node_type 下允许同名 Skill，限定名可精确命中，避免裸名歧义。
    """
    registry = _ensure_registry_loaded()
    if "." in name:
        node_type, _, skill_name = name.partition(".")
        skill_cls = registry.get(node_type, skill_name, user_id=user_id)
        if skill_cls is None:
            logger.warning(f"[SkillRegistry] unknown qualified skill: {name}")
        return skill_cls
    matches = registry.find_by_name(name, user_id=user_id)
    if not matches:
        return None
    if len(matches) > 1:
        names = ", ".join(f"{cls.node_type}.{cls.name}" for cls in matches)
        raise ValueError(f"ambiguous skill name '{name}': {names}")
    return matches[0]


def _ensure_registry_loaded() -> SkillRegistry:
    ensure_builtin_skills_registered()
    registry = SkillRegistry.instance()
    registry.scan_third_party()
    return registry