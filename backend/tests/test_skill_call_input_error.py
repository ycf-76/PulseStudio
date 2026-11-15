"""skill_call 参数校验失败的结构化报错与 skill_doc 置顶回归测试。

背景（2026-10-29）：trending_search 连续多轮失败，报错原文是 Pydantic 的
`input_value={'_context': WorkflowCont... at 0x...}` —— LLM 读成「框架链路 bug」
而放弃重试。本测试锁定两点：
1. 校验失败必须返回可执行的修复指令（缺哪个字段、该补什么），且不泄漏对象地址；
2. skill_doc 必须位于返回值第一个键，否则 observation 尾部截断会把它切掉。
"""

from unittest.mock import patch

import pytest
from pydantic import BaseModel, Field

from app.engine.reactor.primitives import SkillCallPrimitive
from app.engine.reactor.session import ReactorSession


class _FakeSchema(BaseModel):
    keyword: str = Field(..., description="Search keyword")
    limit: int = Field(default=10, description="返回条数上限")


class _OptionalSchema(BaseModel):
    topic: str = Field(default="", description="选题主题")
    platform: str = Field(default="", description="目标平台")


class _FakeSkill:
    name = "fake_required_skill"
    description = "测试用必填参数技能"
    usage_hint = "按 keyword 搜索"
    input_schema = _FakeSchema
    executed = 0

    async def execute(self, inputs):
        _FakeSkill.executed += 1
        _FakeSchema.model_validate(inputs)
        return {"ok": True}


class _OptionalSkill:
    name = "fake_optional_skill"
    description = "测试用全可选参数技能"
    input_schema = _OptionalSchema
    executed = 0

    async def execute(self, inputs):
        _OptionalSkill.executed += 1
        return {"ok": True}


class _BoomSkill:
    name = "boom_skill"
    description = "测试用异常技能"
    input_schema = None

    def execute(self, inputs):
        raise RuntimeError("upstream died while holding <object at 0x0000021CE194C410>")


async def _call_primitive(skill_cls, arguments):
    with patch("app.core.skill_registry.get_skill_class_by_name", return_value=skill_cls):
        with patch("app.engine.factory.get_deepseek_llm", return_value=None):
            return await SkillCallPrimitive().execute(
                skill=skill_cls.name,
                arguments=arguments,
                _user_id="u1",
                _workflow_id="w1",
            )


@pytest.mark.asyncio
async def test_empty_arguments_returns_probe_not_error():
    """空 arguments → 空探针：只回参数说明书，不执行技能、不算失败。"""
    result = await _call_primitive(_FakeSkill, {})

    assert result["ok"] is True
    assert result["probe"] is True
    assert result["skill"] == "fake_required_skill"
    assert [p["name"] for p in result["required_params"]] == ["keyword"]
    assert "未执行" in result["note"]
    assert _FakeSkill.executed == 0, "空探针不应真正执行技能"


@pytest.mark.asyncio
async def test_missing_required_field_returns_actionable_error():
    """传了错字段（topic）→ 明确指出缺 keyword，并给出可照抄的重试模板。"""
    result = await _call_primitive(_FakeSkill, {"topic": "AI"})

    assert result["ok"] is False
    assert result["missing_fields"] == ["keyword"]
    assert result["received_fields"] == ["topic"]
    assert result["retryable"] is True

    expected = {p["name"]: p for p in result["expected_params"]}
    assert expected["keyword"]["required"] is True
    assert expected["limit"]["required"] is False

    assert "missing required field(s): keyword" in result["error"]
    assert '"keyword"' in result["error"]
    assert "you passed: topic" in result["error"]


@pytest.mark.asyncio
async def test_all_optional_schema_with_empty_args_still_executes():
    """全可选 schema 的空参数是合法调用 → 必须真执行，不能被探针拦下。"""
    result = await _call_primitive(_OptionalSkill, {})

    assert result["ok"] is True
    assert "probe" not in result
    assert _OptionalSkill.executed == 1


@pytest.mark.asyncio
async def test_error_text_leaks_no_object_repr():
    """报错文本不得出现内存地址或 _context（否则模型误判为框架故障）。"""
    result = await _call_primitive(_FakeSkill, {"topic": "AI"})

    text = str(result)
    assert "0x" not in text
    assert "input_value" not in text
    assert "_context" not in text
    assert "pydantic.dev" not in text


@pytest.mark.asyncio
async def test_generic_exception_memory_address_sanitized():
    """非校验异常同样要抹掉对象地址，保留异常类型与信息。"""
    result = await _call_primitive(_BoomSkill, {})

    assert result["ok"] is False
    assert "RuntimeError" in result["error"]
    assert "upstream died" in result["error"]
    assert "0x0000021CE194C410" not in result["error"]


def _make_session(skills):
    session = ReactorSession.__new__(ReactorSession)
    session._skills = skills
    session._skill_doc_sent = set()
    return session


def test_skill_doc_is_first_key():
    """skill_doc 必须是第一个键：observation 尾部截断时它必须活下来。"""
    session = _make_session([_FakeSkill()])
    args = {"skill": "fake_required_skill", "arguments": {}}

    result = session._attach_skill_doc(
        "skill_call", args, {"ok": False, "error": "missing required field(s): keyword"}
    )

    assert list(result.keys())[0] == "skill_doc"
    assert result["error"] == "missing required field(s): keyword"
    assert "keyword" in result["skill_doc"]


def test_skill_doc_attached_once_per_skill():
    """同一技能只附一次 skill_doc，避免上下文重复膨胀。"""
    session = _make_session([_FakeSkill()])
    args = {"skill": "fake_required_skill", "arguments": {}}

    first = session._attach_skill_doc("skill_call", args, {"ok": False})
    second = session._attach_skill_doc("skill_call", args, {"ok": False})

    assert "skill_doc" in first
    assert "skill_doc" not in second


def test_skill_doc_not_injected_for_plain_tool():
    """非 skill_call 的 primitive 调用不注入 skill_doc。"""
    session = _make_session([_FakeSkill()])

    result = session._attach_skill_doc("file_read", {"path": "a"}, {"ok": True})

    assert "skill_doc" not in result
    assert session._skill_doc_sent == set()


def test_skill_doc_skipped_for_unknown_skill():
    """未注册技能 doc 为空 → 不注入，也不污染哨兵集合。"""
    session = _make_session([_FakeSkill()])

    result = session._attach_skill_doc("skill_call", {"skill": "ghost_skill"}, {"ok": False})

    assert "skill_doc" not in result
    assert session._skill_doc_sent == set()
