"""work_context_prompt 注入契约（2026-11-04 修复回归锁定）。

背景（app.log 17:17:04 实锤）：chat_agent 把前端传来的 work_context 格式化成
work_context_prompt 放进 run context，但 ReactorSession.run 的两条消息构建
路径都没消费这个 key——
  - context_manager 分支（主会话必走）只提取 personality/profile，
    作品上下文被静默丢弃 → agent 翻工作区文件拿旧卡片文案当分析对象；
  - _build_initial_messages 分支对 >800 字的 context 值截成 200 字残句。

契约：无论走哪条分支，work_context_prompt 必须完整出现在 LLM 收到的
messages 里，作为独立 system 消息（不走 JSON 截断）。

全部离线 mock，不碰真机 LLM。
"""
from __future__ import annotations

import pytest

from app.engine.governance.context_manager import ContextManager
from app.engine.reactor.session import ReactorSession


_WORK_PROMPT = (
    "【重要】当前用户正在查看以下作品，你必须基于此作品的实际内容来回答问题：\n"
    "=== 作品信息 ===\n"
    "标题: custom 第一眼你想到的意思是什么？🐙\n"
    "正文内容（必须以此为准）:\n"
    + "英语单词记忆方法测试正文。" * 60  # >800 字，验证不被 JSON 摘要截断
)


class _FakeBus:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def publish(self, workflow_id, event_type, payload, turn_id=None):
        self.calls.append({"event_type": event_type, "payload": payload})
        return "evt_fake"


class _CaptureLLM:
    """记录每次 stream_chat 收到的 messages，返回固定 final。"""

    model_name = "fake-capture"

    def __init__(self) -> None:
        self.seen: list[list[dict]] = []

    async def stream_chat(self, messages):
        self.seen.append([dict(m) for m in messages])
        yield {"content": '{"thought": "直接产出终稿", "final": true, "output": {"_message": "OK"}}'}
        yield {"content": "", "is_final": True, "token_usage": 1}


def _assert_work_prompt_reached_llm(llm: _CaptureLLM) -> None:
    assert llm.seen, "LLM 至少应被调用一次"
    first_call = llm.seen[0]
    system_contents = [
        m["content"] for m in first_call if m.get("role") == "system"
    ]
    assert any(
        "当前用户正在查看以下作品" in c for c in system_contents
    ), f"work_context_prompt 应作为 system 消息完整到达 LLM，实际 system 消息: {system_contents}"
    # 完整性：>800 字的正文不允许被 200 字 JSON 摘要截断
    assert any(
        _WORK_PROMPT[-40:] in c for c in system_contents
    ), "work_context_prompt 尾部内容缺失——被截断了"


@pytest.mark.asyncio
async def test_work_context_reaches_llm_via_context_manager():
    """主会话路径（ContextManager 分支）：work_context_prompt 必须注入。"""
    bus = _FakeBus()
    llm = _CaptureLLM()
    session = ReactorSession(llm=llm, workflow_id="wf_wc1", sse_bus=bus)
    cm = ContextManager(session_id="wf_wc1")
    cm.set_system_prompt(session.system_prompt)

    output = await session.run(
        "帮找分析一下我的图文为什么没有火",
        context={"work_context_prompt": _WORK_PROMPT},
        context_manager=cm,
    )
    assert output.get("ended_reason") == "completed"
    _assert_work_prompt_reached_llm(llm)


@pytest.mark.asyncio
async def test_work_context_reaches_llm_via_initial_messages():
    """无 ContextManager 路径（_build_initial_messages 分支）：完整注入且不被截断。"""
    bus = _FakeBus()
    llm = _CaptureLLM()
    session = ReactorSession(llm=llm, workflow_id="wf_wc2", sse_bus=bus)

    output = await session.run(
        "帮找分析一下我的图文为什么没有火",
        context={"work_context_prompt": _WORK_PROMPT},
    )
    assert output.get("ended_reason") == "completed"
    _assert_work_prompt_reached_llm(llm)


@pytest.mark.asyncio
async def test_no_work_context_still_works():
    """回归：没有 work_context_prompt 时两条路径都不炸。"""
    bus = _FakeBus()
    llm = _CaptureLLM()
    session = ReactorSession(llm=llm, workflow_id="wf_wc3", sse_bus=bus)
    cm = ContextManager(session_id="wf_wc3")
    cm.set_system_prompt(session.system_prompt)

    output = await session.run("你好", context={}, context_manager=cm)
    assert output.get("ended_reason") == "completed"

    output2 = await session.run("你好", context={})
    assert output2.get("ended_reason") == "completed"
