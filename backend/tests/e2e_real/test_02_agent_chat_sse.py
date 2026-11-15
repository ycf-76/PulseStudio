"""E2E 真实 API：对话智能体 / SSE 事件流 / 会话与侧会话。

覆盖核心创作路径的入口：POST /api/v1/chat/agent 发起回合 → SSE 订阅
/api/sse/chat/{session_id} 采集事件 → 落库消息回读。
"""

from __future__ import annotations

import os

import pytest

from helpers import (
    AWAITING_STATUSES,
    PAUSE_EVENTS,
    collect_sse,
    event_types,
    extract_message,
    is_success_status,
    load_live_event_types,
    new_turn_id,
)

pytestmark = pytest.mark.integration

# 澄清续跑窗口：续跑在后台任务里执行，第三方 LLM 断网时重试阶梯
# （stream_retry conn_retry 最多 10 次 + 指数退避）会把单轮拖到 10 分钟以上。
# 300s 是稳定网络下的合理值，抖动环境用 E2E_RESUME_TIMEOUT 放宽。
RESUME_TIMEOUT = float(os.getenv("E2E_RESUME_TIMEOUT", "600"))


@pytest.fixture(scope="module")
def qa_turn(client):
    """模块级共享：一次真实 Q&A 回合，产出会话与全量 SSE 事件。"""
    turn = new_turn_id("qa")
    resp = client.post(
        "/api/v1/chat/agent",
        json={
            "message": "用一句话说明你能帮我做什么",
            "mode": "chat",
            "turn_id": turn,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    events = collect_sse(client, body["session_id"], turn_id=turn, timeout=180.0)
    return {"body": body, "events": events, "turn": turn}


def test_agent_chat_returns_answer_and_persists_message(client, qa_turn):
    """测试：chat 模式回合返回真实回复并落库。

    场景：POST /api/v1/chat/agent（mode=chat）后回读会话消息。
    预期：status 为成功态（completed/agent_output）、回复文本非空、turn_id 回传；
          DB 中有 user+assistant 两条。
    文档依据：backend/app/agents/chat_agent.py L529（成功态为 agent_output）；
             backend/app/api/routers/chat_agent.py agent_chat L1268。
    """
    body = qa_turn["body"]
    assert is_success_status(body["status"]), f"chat 回合未成功结束: {body['status']}"
    reply = extract_message(body)
    assert reply.strip(), f"回复为空: {str(body)[:300]}"
    assert body["turn_id"] == qa_turn["turn"], "响应未回传 turn_id"

    msgs = client.get(f"/api/chat/sessions/{body['session_id']}/messages")
    assert msgs.status_code == 200, msgs.text
    rows = msgs.json()["data"]
    roles = [m.get("role") for m in rows]
    assert "user" in roles and "assistant" in roles, f"消息未落库: {roles}"
    assistant = [m for m in rows if m.get("role") == "assistant"][-1]
    assert (assistant.get("content") or "").strip(), "助手消息内容为空"


def test_sse_stream_emits_turn_events(qa_turn):
    """测试：SSE 通道按回合回放并发出核心事件。

    场景：订阅带 turn_id 的 SSE（后端已跑完，走 replay）。
    预期：至少收到 chat_turn_started + 一个终态事件，事件信封字段齐全。
    文档依据：backend/app/services/sse_bus.py L226-295。
    """
    events = qa_turn["events"]
    assert events, "SSE 未收到任何事件"
    types = event_types(events)
    assert "chat_turn_started" in types, f"缺少回合开始事件: {types}"
    assert any(t in {"workflow_completed", "agent_loop_done"} for t in types), f"缺少终态事件: {types}"

    for item in events[:3]:
        data = item["data"]
        for key in ("event_id", "type", "payload", "timestamp"):
            assert key in data, f"事件信封缺字段 {key}: {data}"
        assert data["type"] == item["event"], "event 行与 data.type 不一致"


def test_sse_events_all_declared_in_frontend_contract(qa_turn, repo_root):
    """测试：后端发出的每个事件类型都在前端契约表内。

    场景：对比实际观测到的事件名与 frontend/src/components/chat/sse-contract.ts 的 Ev 表。
    预期：无「后端发了但前端没接」的事件（否则前端静默丢弃）。
    文档依据：sse-contract.ts 顶部维护规则。
    """
    declared = load_live_event_types(repo_root)
    assert declared, "未解析到前端事件契约表"
    observed = set(event_types(qa_turn["events"]))
    undeclared = observed - declared
    assert not undeclared, f"以下事件未在前端契约表声明: {sorted(undeclared)}"


def test_multi_turn_keeps_conversation_context(client, qa_turn):
    """测试：同一会话多轮对话保留上下文。

    场景：在 qa 会话里追问「我上一条消息问的是什么」。
    预期：回复能复述首轮问题（出现「帮我做什么」类关键词）。
    文档依据：chat_agent 会话历史注入。
    """
    turn = new_turn_id("ctx")
    resp = client.post(
        "/api/v1/chat/agent",
        json={
            "message": "我上一条消息问的是什么？只回答我的原话。",
            "mode": "chat",
            "session_id": qa_turn["body"]["session_id"],
            "turn_id": turn,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    reply = extract_message(body)
    assert reply.strip(), "追问回合回复为空"
    assert ("帮我做什么" in reply) or ("你能" in reply), f"上下文丢失，回复未复述首轮问题: {reply[:120]}"


def test_session_crud_lifecycle(client):
    """测试：会话增删改查完整可用。

    场景：创建 → 列表 → 改名 → 加消息 → 读消息 → 删除。
    预期：每步状态码与数据一致，删除后列表不再包含。
    文档依据：backend/app/api/routers/chat_session.py。
    """
    created = client.post("/api/chat/sessions", json={"title": "E2E 临时会话"})
    assert created.status_code == 200, created.text
    session_id = created.json()["data"]["id"]

    listed = client.get("/api/chat/sessions")
    assert listed.status_code == 200, listed.text
    assert any(s["id"] == session_id for s in listed.json()["data"])

    renamed = client.patch(f"/api/chat/sessions/{session_id}", json={"title": "E2E 改名后"})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["data"]["title"] == "E2E 改名后"

    added = client.post(
        f"/api/chat/sessions/{session_id}/messages",
        json={"role": "user", "content": "E2E 落库校验"},
    )
    assert added.status_code == 200, added.text

    msgs = client.get(f"/api/chat/sessions/{session_id}/messages")
    assert any(m.get("content") == "E2E 落库校验" for m in msgs.json()["data"])

    deleted = client.delete(f"/api/chat/sessions/{session_id}")
    assert deleted.status_code == 200, deleted.text
    after = client.get("/api/chat/sessions")
    assert all(s["id"] != session_id for s in after.json()["data"])


def test_side_session_lifecycle(client, qa_turn):
    """测试：侧会话创建 / 列举 / 改名 / 归档只读 / 删除。

    场景：基于有消息的主会话创建 recent_n 侧会话，再走状态流转。
    预期：创建 201；归档后追加消息返回 403；删除后列表不含。
    文档依据：backend/app/api/routers/side_session.py + chat_session.add_message L80-88。
    """
    main_id = qa_turn["body"]["session_id"]
    created = client.post(
        f"/api/chat/sessions/{main_id}/side-sessions",
        json={"granularity": "recent_n", "recent_n": 6},
    )
    assert created.status_code == 201, created.text
    side_id = created.json()["data"]["id"]

    listed = client.get(f"/api/chat/sessions/{main_id}/side-sessions")
    assert listed.status_code == 200, listed.text
    assert any(s["id"] == side_id for s in listed.json()["data"])

    renamed = client.patch(f"/api/side-sessions/{side_id}", json={"title": "E2E 侧会话"})
    assert renamed.status_code == 200, renamed.text

    archived = client.patch(f"/api/side-sessions/{side_id}", json={"status": "archived"})
    assert archived.status_code == 200, archived.text

    blocked = client.post(
        f"/api/chat/sessions/{side_id}/messages",
        json={"role": "user", "content": "归档后不应可写"},
    )
    assert blocked.status_code == 403, f"归档侧会话仍可写: {blocked.status_code} {blocked.text[:200]}"

    removed = client.delete(f"/api/side-sessions/{side_id}")
    assert removed.status_code in (200, 204), removed.text
    after = client.get(f"/api/chat/sessions/{main_id}/side-sessions")
    assert all(s["id"] != side_id for s in after.json()["data"])


def test_cross_user_cannot_read_others_session(client, second_user_client, qa_turn):
    """测试：会话数据按用户隔离（越权访问必须被拒）。

    场景：第二个账号读取第一个账号的会话消息、改名。
    预期：403/404，绝不返回他人消息内容。
    文档依据：backend/app/api/routers/chat_session.py（_require_owned_session 归属闸门）。
    """
    session_id = qa_turn["body"]["session_id"]

    read = second_user_client.get(f"/api/chat/sessions/{session_id}/messages")
    assert read.status_code in (403, 404), (
        f"越权读取会话消息成功（疑似越权漏洞）: {read.status_code} {read.text[:300]}"
    )

    rename = second_user_client.patch(f"/api/chat/sessions/{session_id}", json={"title": "hijack"})
    assert rename.status_code in (403, 404), f"越权改名成功: {rename.status_code} {rename.text[:200]}"

    removed = second_user_client.delete(f"/api/chat/sessions/{session_id}")
    assert removed.status_code in (403, 404), f"越权删除成功: {removed.status_code} {removed.text[:200]}"
    still_there = client.get(f"/api/chat/sessions/{session_id}/messages")
    assert still_there.status_code == 200, "会话被他人删除"

    listing = second_user_client.get("/api/chat/sessions")
    assert all(s["id"] != session_id for s in listing.json()["data"]), "他人会话出现在自己的列表里"


def test_sse_rejects_foreign_or_anonymous_subscribers(anon_client, second_user_client, qa_turn):
    """测试：SSE 订阅端点做鉴权与会话归属校验。

    场景：匿名 / 他人账号订阅同一会话。
    预期：401 或 403。
    文档依据：backend/app/api/routers/sse.py L33-38。
    """
    session_id = qa_turn["body"]["session_id"]
    anon = anon_client.get(f"/api/sse/chat/{session_id}")
    assert anon.status_code in (401, 403), f"匿名可订阅 SSE: {anon.status_code}"

    foreign = second_user_client.get(f"/api/sse/chat/{session_id}")
    assert foreign.status_code == 403, f"他人可订阅 SSE: {foreign.status_code} {foreign.text[:200]}"


def test_creation_workflow_image_text_real(client):
    """测试：图文创作真实跑通（LLM + 工具链 + 事件流）。

    场景：creation_type=image_text，mode=auto，auto_approve=True，请求一期图文。
    预期：回合完成，或合法停在澄清/确认闸门（此时走 /api/v1/chat/clarify 续跑，
    校验暂停-恢复整条链路）；事件里有工具调用；无 workflow_error。
    文档依据：docs/开发指南.md 核心路径；backend/app/skills/creation_type_map.py。
    """
    turn = new_turn_id("create")
    resp = client.post(
        "/api/v1/chat/agent",
        json={
            "message": "帮我做一期小红书图文，主题是「番茄工作法提高专注力」，要求 4 页卡片，标题要抓人",
            "mode": "auto",
            "creation_type": "image_text",
            "auto_approve": True,
            "turn_id": turn,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert is_success_status(body["status"]) or body["status"] in AWAITING_STATUSES, (
        f"创作回合异常结束: {body['status']} / {str(body)[:300]}"
    )
    assert body["session_id"]

    events = collect_sse(client, body["session_id"], turn_id=turn, timeout=300.0)
    types = event_types(events)
    assert "workflow_error" not in types, (
        "创作流程报错: "
        + str([e["data"]["payload"] for e in events if e["event"] == "workflow_error"][:1])
    )
    assert "tool_call_start" in types, f"创作回合没有任何工具调用: {types}"

    paused = any(t in PAUSE_EVENTS for t in types)
    if paused:
        assert body["status"] in AWAITING_STATUSES, (
            f"SSE 已暂停在闸门，但 HTTP 响应状态未体现暂停: {body['status']}"
        )
        assert body.get("loop_state_path"), "暂停回合未返回 loop_state_path，无法续跑"
        resume = client.post(
            "/api/v1/chat/clarify",
            json={
                "session_id": body["session_id"],
                "loop_state_path": body["loop_state_path"],
                "answers": {},
                "action": "skip",
                "user_comment": "按默认偏好继续，4 页卡片，瑞士极简风",
                "turn_id": turn,
            },
        )
        assert resume.status_code == 200, f"澄清续跑失败: {resume.status_code} {resume.text[:300]}"
        resumed_events = collect_sse(
            client, body["session_id"], turn_id=turn, timeout=RESUME_TIMEOUT,
            stop_on_pause=False,
        )
        resumed_types = event_types(resumed_events)
        assert "workflow_error" not in resumed_types, (
            "澄清续跑报错: "
            + str([e["data"]["payload"] for e in resumed_events if e["event"] == "workflow_error"][:1])
        )
        assert any(t in {"workflow_completed", "agent_loop_done"} for t in resumed_types), (
            f"澄清续跑后仍无终态事件: {resumed_types[-10:]}"
        )
        return

    assert any(t in {"workflow_completed", "agent_loop_done"} for t in types), (
        f"缺少终态事件: {types[-10:]}"
    )

    card_events = [e for e in events if e["event"] == "card_draft_ready"]
    if card_events:
        draft = card_events[-1]["data"]["payload"].get("card_draft") or {}
        pages = draft.get("pages") or []
        assert pages, "card_draft_ready 未携带任何页面"
        for page in pages:
            assert page.get("htmlContent"), f"页面缺少渲染产物: {page.get('id')}"


def test_clarify_and_confirm_reject_missing_loop_state(client):
    """测试：澄清/确认接口对不存在的循环状态返回 404。

    场景：传伪造 loop_state_path 调 /api/v1/chat/clarify 与 /confirm。
    预期：404（而不是 500 崩溃或静默成功）。
    文档依据：backend/app/api/routers/chat_agent.py L1810-1814。
    """
    clarify = client.post(
        "/api/v1/chat/clarify",
        json={
            "session_id": "sess-not-exist",
            "loop_state_path": "/tmp/e2e-not-exist-loop.json",
            "action": "cancel",
        },
    )
    assert clarify.status_code == 404, f"澄清接口未返回 404: {clarify.status_code} {clarify.text[:200]}"

    confirm = client.post(
        "/api/v1/chat/confirm",
        json={
            "session_id": "sess-not-exist",
            "loop_state_path": "/tmp/e2e-not-exist-loop.json",
            "action": "approve",
        },
    )
    assert confirm.status_code == 404, f"确认接口未返回 404: {confirm.status_code} {confirm.text[:200]}"


def test_llm_circuit_status_is_readable(client):
    """测试：LLM 熔断状态可查询且结构稳定。

    场景：GET /api/v1/chat/llm-circuit/status。
    预期：200 且返回 dict（供前端熔断提示使用）。
    文档依据：backend/app/api/routers/chat_agent.py L2511。
    """
    resp = client.get("/api/v1/chat/llm-circuit/status")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert isinstance(data, dict) and data, f"熔断状态返回异常: {data}"