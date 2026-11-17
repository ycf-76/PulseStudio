"""
螃蟹伴侣插件默认配置契约测试

测试: 插件默认配置与前端便签模块的约定一致
文档依据: docs/开发指南.md（插件配置走 plugin_configs.config_json，schema 只暴露可视化字段）
"""

import importlib.util
import json
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent.parent / "plugins" / "builtin" / "crab_companion"


def _load_plugin_class():
    """按文件路径加载插件类，避开 plugins 目录的包结构限制"""
    spec = importlib.util.spec_from_file_location(
        "crab_companion_plugin", PLUGIN_DIR / "crab_companion.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.CrabCompanionPlugin


def _load_manifest():
    return json.loads((PLUGIN_DIR / "plugin.json").read_text(encoding="utf-8"))


def test_default_config_contains_crab_notes_block():
    """
    测试: 默认配置包含 crab_notes
    场景: 插件首次加载，用户还没写过任何便签
    预期: crab_notes 里有 settings 与空 notes 列表，前端读到即可直接用
    """
    plugin = _load_plugin_class()()
    cfg = plugin.get_config()

    assert "crab_notes" in cfg
    block = cfg["crab_notes"]
    assert isinstance(block["notes"], list)
    assert block["notes"] == []

    settings = block["settings"]
    for key in ("notes_enabled", "quiet_hours", "global_cooldown_sec", "max_per_session"):
        assert key in settings, f"缺少设置项 {key}"
    assert settings["notes_enabled"] is True


def test_existing_fields_unchanged():
    """
    测试: 原有配置字段不被改动
    场景: 升级到 1.1.0 后老用户仍能读到螃蟹大小与气泡开关
    预期: crab_size / speech_enabled 默认值保持原样
    """
    plugin = _load_plugin_class()()
    cfg = plugin.get_config()

    assert cfg["crab_size"] == 48
    assert cfg["speech_enabled"] is True


def test_update_config_merges_without_dropping_notes():
    """
    测试: 增量更新配置不会丢掉便签数据
    场景: 用户在插件设置页只改了 crab_size
    预期: 只覆盖传入的键，crab_notes 保持原值
    """
    plugin = _load_plugin_class()()
    plugin.update_config({"crab_size": 64})

    cfg = plugin.get_config()
    assert cfg["crab_size"] == 64
    assert "crab_notes" in cfg


def test_manifest_does_not_expose_notes_in_schema():
    """
    测试: config_schema 不暴露 crab_notes
    场景: 插件设置页用 DynamicForm 按 schema 渲染
    预期: schema 里没有 crab_notes，避免它被渲染成只读 JSON 预览
    """
    manifest = _load_manifest()
    props = manifest["config_schema"]["properties"]

    assert "crab_notes" not in props
    assert "crab_size" in props
    assert "speech_enabled" in props


def test_manifest_version_and_capability():
    """
    测试: 清单声明便签能力
    场景: 前端按 capabilities 判断是否挂载便签面板
    预期: capabilities 含 note_reminder，版本为 1.1.0
    """
    manifest = _load_manifest()

    assert "note_reminder" in manifest["capabilities"]
    assert manifest["version"] == "1.1.0"
