"""E2E 真实 API 测试公共工具。

提供：回合 ID 生成、chat SSE 采集、OpenAI 兼容流采集、前端路径契约解析。
所有函数都面向真实 HTTP，不 mock 任何第三方能力。
"""

from __future__ import annotations

import base64
import json
import re
import time
import uuid
from pathlib import Path

import httpx

TERMINAL_EVENTS = frozenset(
    {
        "workflow_completed",
        "workflow_error",
        "workflow_terminated",
        "workflow_failed",
        "workflow_suspended",
        "workflow_cancelled",
        "shortcut_completed",
        "agent_loop_done",
    }
)

AWAITING_STATUSES = frozenset(
    {
        "awaiting_clarification",
        "awaiting_confirmation",
        "awaiting_storyboard_confirmation",
        "awaiting_cut_plan_confirmation",
        "awaiting_guardian_confirmation",
        "awaiting_side_write_confirmation",
    }
)

PAUSE_EVENTS = frozenset(
    {
        "agent_clarification_required",
        "guardian_confirmation_required",
        "storyboard_confirmation_required",
        "cut_plan_confirmation_required",
        "side_write_confirmation_required",
    }
)


def new_turn_id(prefix: str = "e2e") -> str:
    """生成前端同款回合 ID（毫秒时间戳 + 随机后缀）。"""
    return f"{prefix}-{int(time.time() * 1000)}-{uuid.uuid4().hex[:6]}"


def collect_sse(
    client: httpx.Client,
    session_id: str,
    turn_id: str | None = None,
    timeout: float = 180.0,
    stop_on_terminal: bool = True,
    stop_on_pause: bool = True,
) -> list[dict]:
    """订阅 chat SSE 并采集事件。

    返回 [{"event": 事件名, "data": 完整信封}]。
    首帧会回放该回合历史事件，因此「先 POST 再订阅」也不会丢事件。

    stop_on_pause=True：遇到暂停事件（澄清/确认闸门）即返回。
    暂停回合没有终态事件（流会一直挂着等用户回答后续跑），
    若不停在这里只能干等到 timeout。
    """
    events: list[dict] = []
    params = {"turn_id": turn_id} if turn_id else None
    deadline = time.monotonic() + timeout
    read_timeout = httpx.Timeout(connect=15.0, read=45.0, write=15.0, pool=15.0)
    with client.stream(
        "GET",
        f"/api/sse/chat/{session_id}",
        params=params,
        timeout=read_timeout,
    ) as resp:
        assert resp.status_code == 200, resp.read().decode("utf-8", "ignore")
        current: str | None = None
        for line in resp.iter_lines():
            if time.monotonic() > deadline:
                break
            if not line or line.startswith(":"):
                continue
            if line.startswith("event:"):
                current = line[len("event:") :].strip()
                continue
            if line.startswith("data:"):
                raw = line[len("data:") :].strip()
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    data = {"raw": raw}
                events.append({"event": current, "data": data})
                if stop_on_terminal and current in TERMINAL_EVENTS:
                    return events
                if stop_on_pause and current in PAUSE_EVENTS:
                    return events
    return events


def event_types(events: list[dict]) -> list[str]:
    return [e["event"] for e in events if e.get("event")]


def collect_openai_stream(
    client: httpx.Client, body: dict, timeout: float = 180.0
) -> list[dict | str]:
    """采集 /api/v1/chat/completions 的 SSE 分块，返回 chunk 列表（含 "[DONE]"）。"""
    chunks: list[dict | str] = []
    deadline = time.monotonic() + timeout
    with client.stream("POST", "/api/v1/chat/completions", json=body) as resp:
        assert resp.status_code == 200, resp.read().decode("utf-8", "ignore")
        for line in resp.iter_lines():
            if time.monotonic() > deadline:
                break
            if not line.startswith("data:"):
                continue
            raw = line[len("data:") :].strip()
            if raw == "[DONE]":
                chunks.append("[DONE]")
                break
            try:
                chunks.append(json.loads(raw))
            except json.JSONDecodeError:
                continue
    return chunks


def stream_text(chunks: list[dict | str]) -> str:
    parts: list[str] = []
    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        for choice in chunk.get("choices") or []:
            delta = choice.get("delta") or {}
            if delta.get("content"):
                parts.append(delta["content"])
    return "".join(parts)


_PATH_LITERAL = re.compile(r"`(/api[^`]*)`|'(/api[^']*)'|\"(/api[^\"]*)\"")


def normalize_api_path(raw: str) -> str:
    """把前端模板字面量归一成 openapi 风格的路径模板。

    规则：`${...}` 紧跟在 `/` 之后视为路径参数（归一为 `{}`），
    否则视为查询串/后缀拼接（整体丢弃），例如 `/api/chat/sessions${params}`。
    """
    path = raw.split("?")[0].split("#")[0]
    path = re.sub(r"/\$\{[^}]*\}", "/{}", path)
    path = re.sub(r"\$\{[^}]*\}", "", path)
    path = re.sub(r"\{[^}]*\}", "{}", path)
    path = path.rstrip("/")
    return path or "/"


def extract_frontend_api_paths(frontend_src: Path) -> list[tuple[str, str]]:
    """扫描前端源码里的 /api 字面量，返回 [(文件相对路径, 原始路径)]。"""
    found: list[tuple[str, str]] = []
    if not frontend_src.exists():
        return found
    for file in frontend_src.rglob("*"):
        if file.suffix not in {".ts", ".vue", ".js"}:
            continue
        if any(part in {"node_modules", "dist"} for part in file.parts):
            continue
        try:
            text = file.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for match in _PATH_LITERAL.finditer(text):
            raw = next(g for g in match.groups() if g)
            found.append((str(file.relative_to(frontend_src)), raw))
    return found


def load_live_event_types(repo_root: Path) -> set[str]:
    """从 sse-contract.ts 的 Ev 表抽取活事件名。"""
    contract = repo_root / "frontend" / "src" / "components" / "chat" / "sse-contract.ts"
    if not contract.exists():
        return set()
    text = contract.read_text(encoding="utf-8", errors="ignore")
    block = text.split("export const Ev = {", 1)[-1].split("} as const", 1)[0]
    return set(re.findall(r":\s*'([a-z_]+)'", block))


SUCCESS_STATUSES = frozenset({"completed", "agent_output"})

_MESSAGE_KEYS = ("_message", "message", "content", "text", "summary")


def extract_message(body: dict) -> str:
    """从 chat/agent 响应体里抽取可读文本（兼容 agent_output 结构）。"""
    direct = body.get("message")
    if isinstance(direct, str) and direct.strip():
        return direct
    output = body.get("output")
    if isinstance(output, dict):
        for key in _MESSAGE_KEYS:
            value = output.get(key)
            if isinstance(value, str) and value.strip():
                return value
    if isinstance(output, str) and output.strip():
        return output
    return ""


def is_success_status(status: str) -> bool:
    return status in SUCCESS_STATUSES


ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
