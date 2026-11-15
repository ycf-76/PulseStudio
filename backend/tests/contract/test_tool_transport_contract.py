"""二期契约测试：全量走协议（docs/内核-能力分层架构方案.md §4 二期）。

锁定内容：
1. 内核纯净度（验收标准的自动化）：session.py grep 不到业务工具名
2. InProcessTransport 行为等价：unknown tool / TypeError / 非 dict 返回
3. 钩子链：skill_call 协议解包（pre_clarify/pre_execute）、业务钩子注入
4. CREATION_TYPE_SKILLS 数据搬家后解析链路完好
5. SkillRouter 过滤开关两模式

全部离线 mock。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.engine.protocol.dispatch_hooks import DispatchContext, SkillCallUnwrapHook, unwrap_skill_call
from app.engine.protocol.transport import InProcessTransport


# ---------- 1. 内核纯净度 ----------

def test_session_kernel_has_no_business_tool_names():
    """验收标准自动化：内核文件里不得出现业务工具名（防业务回潮）。"""
    src = Path("app/engine/reactor/session.py").read_text(encoding="utf-8")
    forbidden = [
        "multi_image_video", "auto_short_video", "card_design",
        "xhs_note_creator", "publish_checklist", "image_asset_ids",
    ]
    leaked = [n for n in forbidden if n in src]
    assert not leaked, f"业务名渗入内核: {leaked}"


def test_business_hook_lives_in_agents_layer():
    """业务注入钩子必须在能力侧文件，且内核不 import 它的具名实现。"""
    src = Path("app/agents/dispatch_hooks.py").read_text(encoding="utf-8")
    assert "multi_image_video" in src
    session_src = Path("app/engine/reactor/session.py").read_text(encoding="utf-8")
    assert "CreativeImageContextHook" not in session_src


# ---------- 2. InProcessTransport 行为等价 ----------

class _OkPrimitive:
    name = "ok_tool"
    required_permissions = frozenset()

    async def execute(self, **kwargs):
        return {"ok": True, "echo": kwargs}


class _BadArgsPrimitive:
    name = "bad_args"
    required_permissions = frozenset()

    async def execute(self, required_param):
        return {"ok": True}


class _NonDictPrimitive:
    name = "non_dict"
    required_permissions = frozenset()

    async def execute(self, **kwargs):
        return "plain string"


class _SleepPrimitive:
    name = "sleeper"
    required_permissions = frozenset()

    async def execute(self, **kwargs):
        await asyncio.sleep(5)
        return {"ok": True}


import asyncio


def test_transport_unknown_tool():
    t = InProcessTransport({"ok_tool": _OkPrimitive()})
    r = asyncio.run(t.call("nope", {}))
    assert r["ok"] is False
    assert "unknown tool" in r["error"]
    assert "ok_tool" in r["available_tools"]


def test_transport_type_error_converted():
    t = InProcessTransport({"bad_args": _BadArgsPrimitive()})
    r = asyncio.run(t.call("bad_args", {"wrong": 1}))
    assert r["ok"] is False
    assert "invalid arguments" in r["error"]


def test_transport_non_dict_wrapped():
    t = InProcessTransport({"non_dict": _NonDictPrimitive()})
    r = asyncio.run(t.call("non_dict", {}))
    assert r == {"ok": True, "value": "plain string"}


def test_transport_timeout():
    t = InProcessTransport({"sleeper": _SleepPrimitive()}, timeout_seconds=0.05)
    r = asyncio.run(t.call("sleeper", {}))
    assert r["ok"] is False
    assert "timed out" in r["error"]


def test_transport_metadata():
    t = InProcessTransport({"ok_tool": _OkPrimitive()})
    handle = t.get_tool("ok_tool")
    assert handle is not None and handle.name == "ok_tool"
    assert t.get_tool("nope") is None
    assert len(t.list_tools()) == 1


# ---------- 3. 钩子链 ----------

def _ctx(**kw):
    return DispatchContext(run_context=kw.get("run_context", {}), workflow_id=kw.get("wf", "wf1"), turn_id=None)


@pytest.mark.asyncio
async def test_skill_call_unwrap_pre_clarify():
    hook = SkillCallUnwrapHook()
    name, args = await hook.pre_clarify("skill_call", {"skill": "copywriting", "arguments": {"topic": "咖啡"}}, _ctx())
    assert name == "copywriting"
    assert args == {"topic": "咖啡"}


@pytest.mark.asyncio
async def test_skill_call_unwrap_passthrough():
    hook = SkillCallUnwrapHook()
    name, args = await hook.pre_clarify("bash", {"command": "ls"}, _ctx())
    assert name == "bash"


@pytest.mark.asyncio
async def test_skill_call_context_passthrough_pre_execute():
    hook = SkillCallUnwrapHook()
    args = {"skill": "copywriting", "arguments": {"topic": "咖啡"}}
    _, out = await hook.pre_execute("skill_call", args, _ctx(wf="wf9", run_context={"user_id": "u1"}))
    assert out["_user_id"] == "u1"
    assert out["_workflow_id"] == "wf9"


def test_unwrap_pure_function():
    assert unwrap_skill_call("skill_call", {"skill": "x", "arguments": {"a": 1}}) == ("x", {"a": 1})
    assert unwrap_skill_call("bash", {"c": 1}) == ("bash", {"c": 1})


# ---------- 4. CREATION_TYPE_SKILLS 搬家后链路 ----------

def test_creation_type_map_importable_and_resolvable():
    from app.skills.creation_type_map import CREATION_TYPE_SKILLS
    from app.agents.registry import _resolve_creation_type_skills

    assert set(CREATION_TYPE_SKILLS) == {
        "image_text", "voiceover", "short_video", "ai_edit", "long_article", "live_clip",
    }
    resolved = _resolve_creation_type_skills("image_text")
    assert "file_read" in resolved  # ALWAYS_LOAD_SKILLS 兜底
    assert "copywriting" in resolved
    # registry.py 里不再有内联字典定义
    registry_src = Path("app/agents/registry.py").read_text(encoding="utf-8")
    assert "CREATION_TYPE_SKILLS: dict[str, dict[str, list[str]]] = {" not in registry_src


# ---------- 5. SkillRouter 两模式 ----------

class _FakeSkill:
    def __init__(self, name: str, priority: str, triggers: list[str]):
        self.name = name
        self.display_name = name
        self.description = "d"
        self.trigger_words = triggers
        self.skill_priority = priority
        self.node_type = "produce"


def test_skill_router_priority_filter_on():
    from app.tools.skill_router import SkillRouter

    skills = [
        _FakeSkill("hot_1", "hot", []),
        _FakeSkill("hot_2", "hot", []),
        _FakeSkill("warm_1", "warm", []),
        _FakeSkill("cold_1", "cold", ["玄词"]),
    ]
    router = SkillRouter(max_tools=50, enforce_priority=True)
    result = router.select_skills(skills, "普通消息")
    names = {getattr(s, "name") for s in result.selected_skills}
    assert "hot_1" in names and "hot_2" in names
    assert "warm_1" not in names and "cold_1" not in names
    assert result.total_selected == 2

    # 触发词命中后 warm 类别与 cold 技能进入
    result2 = router.select_skills(skills, "来点玄词内容")
    names2 = {getattr(s, "name") for s in result2.selected_skills}
    assert "cold_1" in names2


def test_skill_router_default_off_keeps_full_injection():
    from app.tools.skill_router import SkillRouter

    skills = [_FakeSkill("hot_1", "hot", []), _FakeSkill("cold_1", "cold", [])]
    router = SkillRouter()
    result = router.select_skills(skills, "普通消息")
    assert result.total_selected == 2
    assert result.selected_skills == skills