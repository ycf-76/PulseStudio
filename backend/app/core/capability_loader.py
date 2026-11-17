"""capability.toml 声明式加载器。

读 capabilities/capability.toml →
  1. 按能力包注册 Skill 模块
  2. 按能力包注册 ContentSource
  3. 调用能力包的 init() 函数（如有）
  4. 返回加载摘要
  5. 查询能力 runtime 声明（五期-B：声明即拓扑）

设计：
- toml 不存在 → 走原逻辑（兼容）
- 某个能力 enabled=false → 跳过
- 某个能力 init 失败 → 不影响其他能力
- runtime 字段：inprocess（默认）= 进程内，service = 走外部 HTTP 服务
"""

from __future__ import annotations

import importlib
import logging
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

_TOML_PATH = Path(__file__).resolve().parent.parent.parent / "capabilities" / "capability.toml"

_SERVICE_URL_FIELDS: dict[str, str] = {
    "grid": "grid_service_url",
    "browser": "browser_service_url",
    "media": "media_service_url",
    "spider": "spider_service_url",
}


def _parse_toml(path: Path) -> dict[str, Any]:
    """解析 TOML 文件，返回 {cap_name: {enabled, skills, sources, ...}}。"""
    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib
        except ImportError:
            raise ImportError(
                "capability.toml 需要 tomllib (Python 3.11+) 或 tomli 包。"
                "请运行: pip install tomli"
            )

    with open(path, "rb") as f:
        data = tomllib.load(f)
    return data


def load_capability_skills() -> list[str]:
    """从 capability.toml 读取所有 enabled 能力包的 Skill 模块路径。

    Returns:
        模块路径列表，如 ["capabilities.content_search.skills.trending_search", ...]
    """
    if not _TOML_PATH.exists():
        logger.warning(f"[capability_loader] toml not found: {_TOML_PATH}, returning empty")
        return []

    try:
        data = _parse_toml(_TOML_PATH)
    except Exception as e:
        logger.error(f"[capability_loader] parse toml failed: {e}")
        return []

    skills: list[str] = []
    for cap_name, cap_cfg in data.items():
        if not cap_cfg.get("enabled", True):
            logger.debug(f"[capability_loader] skip disabled capability: {cap_name}")
            continue
        cap_skills = cap_cfg.get("skills", [])
        skills.extend(cap_skills)
        logger.debug(f"[capability_loader] {cap_name}: {len(cap_skills)} skills")

    logger.info(f"[capability_loader] loaded {len(skills)} capability skills from toml")
    return skills


async def init_capabilities() -> dict[str, Any]:
    """按 capability.toml 初始化所有 enabled 能力包。

    做三件事：
    1. 注册 Skill 模块（import 即注册，@register 装饰器自动生效）
    2. 注册 ContentSource（调 init_sources）
    3. 调用能力包的 init() 函数（如有）

    Returns:
        摘要 dict: {cap_name: {"enabled": bool, "skills": int, "status": str}}
    """
    if not _TOML_PATH.exists():
        logger.warning(f"[capability_loader] toml not found: {_TOML_PATH}, skipping")
        return {}

    try:
        data = _parse_toml(_TOML_PATH)
    except Exception as e:
        logger.error(f"[capability_loader] parse toml failed: {e}")
        return {}

    summary: dict[str, Any] = {}

    for cap_name, cap_cfg in data.items():
        if not cap_cfg.get("enabled", True):
            summary[cap_name] = {"enabled": False, "skills": 0, "status": "disabled"}
            continue

        cap_skills = cap_cfg.get("skills", [])
        loaded_skills = 0

        for module_path in cap_skills:
            try:
                importlib.import_module(module_path)
                loaded_skills += 1
            except Exception as e:
                logger.warning(
                    f"[capability_loader] {cap_name}: import skill {module_path} failed: {e}"
                )

        init_fn = cap_cfg.get("init")
        init_ok = None
        if init_fn:
            try:
                mod_path, fn_name = init_fn.rsplit(":", 1)
                mod = importlib.import_module(mod_path)
                fn = getattr(mod, fn_name)
                await fn()
                init_ok = True
            except Exception as e:
                logger.warning(f"[capability_loader] {cap_name}: init failed: {e}")
                init_ok = False

        summary[cap_name] = {
            "enabled": True,
            "skills": loaded_skills,
            "status": "ok" if init_ok is None or init_ok else "init_failed",
        }

    enabled_count = sum(1 for v in summary.values() if v.get("enabled"))
    total_skills = sum(v.get("skills", 0) for v in summary.values())
    logger.info(
        f"[capability_loader] capabilities initialized: "
        f"{enabled_count} enabled, {total_skills} skills loaded"
    )
    return summary


# ─── 五期-B：声明即拓扑 ────────────────────────────────


def _read_toml_data() -> dict[str, Any]:
    """解析 toml 文件，失败返回空 dict。"""
    if not _TOML_PATH.exists():
        return {}
    try:
        return _parse_toml(_TOML_PATH)
    except Exception as e:
        logger.error(f"[capability_loader] parse toml failed: {e}")
        return {}


def get_capability_runtime(cap_name: str) -> Literal["inprocess", "service"]:
    """读取能力包的 runtime 声明。

    Returns:
        "inprocess"（默认）或 "service"。
        toml 不存在 / 能力包未声明 / 无 runtime 字段 → "inprocess"。
    """
    data = _read_toml_data()
    cap_cfg = data.get(cap_name)
    if not isinstance(cap_cfg, dict):
        return "inprocess"
    runtime = cap_cfg.get("runtime", "inprocess")
    if runtime not in ("inprocess", "service"):
        logger.warning(
            f"[capability_loader] {cap_name}: unknown runtime={runtime!r}, falling back to inprocess"
        )
        return "inprocess"
    return runtime


def get_capability_service_url(cap_name: str) -> str:
    """获取能力包的有效服务 URL（声明即拓扑的唯一入口）。

    逻辑：
    1. 读 toml 的 runtime 字段
    2. runtime == "inprocess" → 返回 ""（进程内模式）
    3. runtime == "service" → 从 Settings 读对应 *_service_url 字段
       - URL 非空 → 返回 URL
       - URL 为空 → 告警 + 返回 ""（回退进程内）
    4. 能力包不在 toml → 兼容旧逻辑，从 Settings 读（向后兼容）

    Args:
        cap_name: 能力包名，如 "grid" / "browser" / "media" / "spider"

    Returns:
        服务 URL 字符串，空 = 进程内模式。
    """
    data = _read_toml_data()
    cap_cfg = data.get(cap_name)

    if not isinstance(cap_cfg, dict):
        url_field = _SERVICE_URL_FIELDS.get(cap_name)
        if url_field:
            from app.config import get_settings

            return (getattr(get_settings(), url_field, "") or "").strip()
        return ""

    runtime = cap_cfg.get("runtime", "inprocess")
    if runtime == "inprocess":
        return ""

    if runtime == "service":
        url_field = _SERVICE_URL_FIELDS.get(cap_name)
        if not url_field:
            logger.warning(
                f"[capability_loader] {cap_name}: runtime=service but no service_url mapping, "
                f"falling back to inprocess"
            )
            return ""
        from app.config import get_settings

        url = (getattr(get_settings(), url_field, "") or "").strip()
        if not url:
            logger.warning(
                f"[capability_loader] {cap_name}: runtime=service but {url_field} is empty, "
                f"falling back to inprocess"
            )
            return ""
        return url

    logger.warning(
        f"[capability_loader] {cap_name}: unknown runtime={runtime!r}, falling back to inprocess"
    )
    return ""


def get_capability_routing_table() -> dict[str, dict[str, str]]:
    """返回当前 toml 声明的完整路由表（快照，供契约测试与运维诊断）。

    Returns:
        {cap_name: {"runtime": "inprocess"|"service", "service_url": str, "status": str}}
        status: "inprocess" | "service" | "service_no_url"（声明 service 但 URL 为空）
    """
    data = _read_toml_data()
    table: dict[str, dict[str, str]] = {}

    for cap_name in _SERVICE_URL_FIELDS:
        runtime = get_capability_runtime(cap_name)
        service_url = get_capability_service_url(cap_name)

        if runtime == "inprocess":
            status = "inprocess"
        elif service_url:
            status = "service"
        else:
            status = "service_no_url"

        table[cap_name] = {
            "runtime": runtime,
            "service_url": service_url,
            "status": status,
        }

    return table
