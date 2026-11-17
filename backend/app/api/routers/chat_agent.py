"""Agent Chat 适配层端点（ReAct 模式）。

流程：获取/创建 Session → 写用户消息 → 守卫 → 直接走 ReAct Loop →
LLM 自己选工具、自己执行、自己观察、自己决定下一步。
不再有 TopPlanner 预先决定工具链。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.chat_agent import ChatAgent
from app.agents.input_rules import ChatInputRule
from app.api.deps import get_current_user
from app.db.session import get_db
from app.engine.reactor.task_classifier import build_classifier_from_skills
from app.engine.schemas import WorkflowContext
from app.services import chat_session
from app.services.sse_bus import EVENT_INTENT_PARSED, sse_bus
from app.services.work_context_prompt import build_work_context_prompt

router = APIRouter(prefix="/api/v1/chat", tags=["chat_agent"])

logger = logging.getLogger(__name__)

_input_rule = ChatInputRule()

# ── 后台任务强引用池（2026-10-21 修复）────────────────────────────
# Python 官方文档警告：asyncio.create_task 返回的 Task 只被事件循环弱引用，
# 调用方不持强引用的任务可能在执行中途被垃圾回收——任务无声蒸发，
# 无异常、无日志（诊断实锤：/clarify 续跑任务曾整体消失，session.run()
# 的落盘证据在、后续日志全无）。所有 fire-and-forget 任务必须经此孵化。
_bg_tasks: set[asyncio.Task] = set()


def _spawn_background(coro) -> asyncio.Task:
    """create_task + 模块级强引用，任务结束时自动出池。"""
    task = asyncio.create_task(coro)
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)
    return task


def _resume_auto_approve(state: dict) -> bool:
    """续跑时从落盘快照恢复用户的「自动放行」开关（缺省 True 保持旧行为）。"""
    snapshot = state.get("context_snapshot") or {}
    return bool(snapshot.get("auto_approve", True))


_ANSWER_REPLAY_CHUNK = 12
_ANSWER_REPLAY_INTERVAL_MS = 30


async def _replay_answer_stream(session_id: str, turn_id: str | None, text: str) -> None:
    """终稿正文回放流（2026-11-08：修复「思考链路结束后正文阻塞整段蹦出」）。

    根因：reactor 主循环的 LLM 输出恒为 ReAct 协议 JSON（emit_delta=False，
    协议门防裸 JSON 泄源），终态人话由 POST 响应一次性带回——SSE 里从来没有
    正文流，思考一结束正文就整段出现。

    修复：POST 组装出 humanized_message 之后、返回响应之前，把终稿以小分片
    经 SSE 回放成 agent_message_delta——前端播放器现成、逐字渲染。回放完成
    后才返回响应，POST 的 message 与流文本天然一致，无双写/闪烁。
    """
    if not (text or "").strip():
        return
    try:
        for i in range(0, len(text), _ANSWER_REPLAY_CHUNK):
            await sse_bus.publish(
                session_id,
                "agent_message_delta",
                {
                    "node_id": "reactor",
                    "delta": text[i : i + _ANSWER_REPLAY_CHUNK],
                },
                turn_id=turn_id,
            )
            await asyncio.sleep(_ANSWER_REPLAY_INTERVAL_MS / 1000)
    except Exception:
        logger.warning(
            "[chat_agent] answer replay stream failed; POST response still carries full text"
        )


def _humanize_output(raw_message: str, output_data: dict | None) -> str:
    """Convert raw AgentOutput into natural language for chat display.

    Rules:
    - If output has summary, use it as the base
    - Strip internal keys (_model_used, _duration_ms, etc.)
    - Convert structured data into readable paragraphs
    - Truncate overly long content (skill_chain summaries exempt — they are pre-formatted)
    - Never expose error messages or $ symbols
    """
    if not output_data:
        return raw_message

    # 2026-10-22：兜底链 summary → _message → content → raw_message。
    # prompt.py「可读性规范」明确教 LLM 把人话写进 output 的 _message /
    # content / summary 字段，但此前只读 summary——续跑轮 LLM final 输出
    # {"_message": "人话"} 时被吞成空，落库变「操作已完成。」，用户全程
    # 看不到真实回复（10:33:52 实锤：零工具调用 + _message 被 _humanize
    # 丢弃）。按 prompt 承诺的字段顺序取第一个非空者；短句（>4 字）也是
    # 真实回复，不再用 10 字门槛误杀。
    # 2026-11-06：字段类型兜底。LLM 偶尔把 summary / content 写成对象或数组
    # （结构化摘要、卡片页数据），旧实现直接 summary.strip() 抛
    # AttributeError: 'dict' object has no attribute 'strip'，把
    # /confirm-guardian 的后台续跑任务整个打死（只发 workflow_error 关流，
    # 正文丢失，用户视角就是"跑到一半断了"）。改为只接受非空字符串，
    # 非字符串字段跳过并继续按顺序取下一个。
    summary = ""
    for _key in ("summary", "_message", "content", "text"):
        _cand = output_data.get(_key)
        if isinstance(_cand, str) and _cand.strip():
            summary = _cand
            break
    message = summary or raw_message

    key_findings = output_data.get("key_findings")
    recommendations = output_data.get("recommendations")

    parts: list[str] = []

    if message:
        cleaned = _strip_internal_markers(message)
        parts.append(cleaned)

    if key_findings and isinstance(key_findings, list) and "**关键发现：**" not in message:
        findings_text = [f"• {f}" for f in key_findings[:5] if f]
        if findings_text:
            parts.append("\n**关键发现：**\n" + "\n".join(findings_text))

    if (
        recommendations
        and isinstance(recommendations, list)
        and "**建议方向：**" not in message
        and "**选题建议：**" not in message
    ):
        recs_text = []
        for r in recommendations[:3]:
            if isinstance(r, dict):
                topic_dir = r.get("topic_direction", r.get("title", ""))
                if topic_dir:
                    recs_text.append(f"• {topic_dir}")
            elif isinstance(r, str):
                recs_text.append(f"• {r}")
        if recs_text:
            parts.append("\n**建议方向：**\n" + "\n".join(recs_text))

    result = "\n".join(parts).strip()

    result = _clean_error_leakage(result)
    return result


def _append_search_summary(summary_parts: list[str], search_data: dict) -> None:
    """把搜索结果摘要追加到 summary_parts（SEARCH_ONLY 或降级分析时调用）。"""
    results = search_data.get("results", [])
    if not results:
        return
    summary_parts.append("**搜索结果：**")
    for r in results[:5]:
        if not isinstance(r, dict):
            continue
        title = r.get("title", "")
        platform = r.get("platform", "")
        summary = r.get("summary", "")
        interactions = r.get("interactions", 0)
        prefix = f"[{platform}]" if platform else ""
        line = f"  - {prefix} {title}" if prefix else f"  - {title}"
        if interactions:
            line += f"（互动 {interactions}）"
        if summary:
            line += f"\n    {summary[:80]}"
        summary_parts.append(line)


def _sanitize_sse_payload(event_type: str, payload: dict) -> dict:
    """Presentation Layer boundary — sanitize SSE payloads before they reach the UI.

    Following Codex's Event Channel principle: internal events must NOT leak to UI.
    Following Claude Code's render-logic separation: UI only sees DisplayItem-compatible data.

    Rules:
    - Remove all keys starting with _ (internal markers)
    - Remove SQL, traceback, pymysql errors
    - Convert raw error messages to user-friendly text
    - Keep only UI-relevant fields per event type
    """
    INTERNAL_KEY_PREFIXES = ("_", "sql", "traceback", "pymysql", "internal")
    cleaned = {}
    for k, v in payload.items():
        if any(k.lower().startswith(p) for p in INTERNAL_KEY_PREFIXES):
            continue
        if isinstance(v, str):
            v = _strip_internal_markers(v)
            v = _clean_error_leakage(v)
        cleaned[k] = v

    if event_type in ("workflow_error", "workflow_failed"):
        error_msg = cleaned.get("error", "") or cleaned.get("message", "")
        if error_msg:
            cleaned["error"] = "处理过程中遇到问题，请稍后重试"
        cleaned.pop("traceback", None)
        cleaned.pop("exception", None)

    return cleaned


def _strip_internal_markers(text: str) -> str:
    """Remove internal markers like _model_used, _duration_ms, <thinking>, <needs_clarification> tags, etc."""
    import re

    patterns = [
        r"_model_used[\s\"\'=:]+[\w\-]+",
        r"_duration_ms[\s\"\'=:]+\d+",
        r"\$+\s*$",
        r"\w+_gen\s*未收到[^，。]*",
        r"未收到前端注入[^，。]*",
        r"，?请确认[^，。\n]*生成[^，。\n]*",
        r"Data truncated for column[^，。\n]*",
        r"SQL:\s*[^。\n]*",
        r"\(pymysql\.err\.[A-Za-z]+\)[^。\n]*",
        r"工作流启动失败[^，。\n]*",
        r"workflow\s+start\s+failed[^，.\n]*",
    ]
    for p in patterns:
        text = re.sub(p, "", text)
    # 保护代码块中的内容不被误删
    code_blocks: list[str] = []
    text = re.sub(
        r"```[\s\S]*?```",
        lambda m: (code_blocks.append(m.group()) or f"\x00CB{len(code_blocks) - 1}\x00"),
        text,
    )
    # 清理 <thinking>...</thinking> 标签及其内容（思考过程通过 reasoning 字段单独展示）
    text = re.sub(r"<thinking>[\s\S]*?</thinking>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<thinking>[\s\S]*$", "", text, flags=re.IGNORECASE)
    # 清理 <needs_clarification>...</needs_clarification> 标签及其内容（澄清通过独立卡片展示）
    text = re.sub(
        r"<needs_clarification>[\s\S]*?</needs_clarification>", "", text, flags=re.IGNORECASE
    )
    text = re.sub(r"<needs_clarification>[\s\S]*$", "", text, flags=re.IGNORECASE)
    # 恢复代码块
    text = re.sub(r"\x00CB(\d+)\x00", lambda m: code_blocks[int(m.group(1))], text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _clean_error_leakage(text: str) -> str:
    """Remove leaked internal error messages."""
    import re

    error_patterns = [
        # 含 "error" 键的结构化 JSON（loop 工具失败 observation 被 LLM 复述进最终回复时）
        # 必须放在行级模式之前：否则 Error 行级匹配会先破坏 JSON 结构留下残缺片段
        r'\{[^{}]*"error"\s*:[^{}]*\}',
        r"(?:Traceback|Error|Exception)[^\n]*(?:\n[^\n]*){0,3}",
        r'File\s+"[^"]+",\s*line\s+\d+',
        r"pymysql\.err\.\w+",
        r"DataError.*?row \d+",
    ]
    for p in error_patterns:
        text = re.sub(p, "[内部错误已隐藏]", text)
    # loop 工具失败/熔断标记词：直接剔除（整段替换会产生"引擎 [内部错误已隐藏] 已拦截"这类破碎句）
    text = re.sub(r"\btool_(?:failed|blocked)\b[\s:：,，]*", "", text)
    text = re.sub(r"\$\s*\n?", "", text)
    text = re.sub(r"\n+", "\n", text)
    return text.strip()


class AgentChatRequest(BaseModel):
    message: str = Field(..., max_length=10000)
    mode: str = Field(default="auto", pattern="^(auto|full_pipeline|chat)$")
    account_id: str | None = None
    session_id: str | None = None
    agent_id: str | None = Field(
        default=None, description="指定使用的智能体 ID，不传则默认 chat_agent"
    )
    model: str | None = Field(
        default=None,
        description="用户在输入框手动选择的模型 ID；不传则按「模型配置」页的分配（智能体/全局默认）",
    )
    creation_type: str | None = Field(
        default=None,
        description="当前创作类型(image_text/voiceover/short_video/ai_edit/long_article/live_clip)，用于按需加载Skill",
    )
    analysis_context: str | None = Field(
        default=None, description="AI数据分析报告上下文，注入到 prompt 中让 AI 基于真实数据给建议"
    )
    work_context: dict | None = Field(
        default=None, description="当前作品上下文，包含标题/平台/正文/指标等"
    )
    workspace_id: str | None = Field(default=None, description="当前绑定的本地工作区 ID")
    file_context: dict | None = Field(default=None, description="当前选中的对话/本地文件元数据")
    folder_context: dict | None = Field(
        default=None, description="当前文件夹的对话文件/工作区文件列表"
    )
    model_settings: dict | None = Field(
        default=None,
        description="模型和推送设置，包含 enable_wechat_push, wechat_target_user_id, enable_feishu_push, feishu_chat_id 等",
    )
    auto_approve: bool = Field(
        default=True, description="工具调用是否自动放行（True=自动放行，False=需要用户确认）"
    )
    image_asset_ids: list[str] = Field(
        default_factory=list,
        description="用户在聊天中附带/上传的图片 asset_id 列表，供视频生成等 Skill 直接使用",
    )
    video_asset_ids: list[str] = Field(
        default_factory=list,
        description="用户在聊天中附带/上传的视频 asset_id 列表，供智能剪辑/叠加增强等 Skill 直接使用",
    )
    turn_id: str | None = Field(
        default=None,
        description="回合 ID（前端发送前生成）。SSE 事件按回合隔离，防上一轮输出窜显；不传则后端生成",
    )


_trigger_words_cache: dict[str, str] | None = None


def _get_third_party_trigger_words() -> dict[str, str]:
    """从 Skill Registry 获取所有非本地平台的触发词（元数据驱动，无需硬编码）。

    结果在首次调用后缓存，避免每次请求都重新扫描注册表。
    """
    global _trigger_words_cache
    if _trigger_words_cache is not None:
        return _trigger_words_cache
    try:
        from app.core.skill_registry import SkillRegistry, ensure_builtin_skills_registered

        ensure_builtin_skills_registered()
        registry = SkillRegistry.instance()
        registry.scan_third_party()
        _trigger_words_cache = registry.all_trigger_words_for_non_local_platforms()
        return _trigger_words_cache
    except Exception:
        return {}


def _get_classifier():
    """Get the default TaskClassifier (built from DEFAULT_MANIFESTS)."""
    return build_classifier_from_skills(None)


def _looks_like_local_file_request(message: str) -> bool:
    if not message:
        return False
    _trigger_words = _get_third_party_trigger_words()
    if _trigger_words and any(kw in message.lower() for kw in _trigger_words):
        return False
    return _get_classifier().has_signal(message, "file_search")


def _extract_local_file_terms(message: str) -> list[str]:
    import re as _re

    classifier = _get_classifier()
    noise_words = classifier.get_noise_words("file_search")
    msg = message
    for noise in noise_words:
        msg = msg.replace(noise, " ")
    parts = [
        p.strip()
        for p in _re.split(r"[\s,，。;；、:：]+", msg)
        if p.strip() and len(p.strip()) >= 2
    ]
    return parts[:5] or [message.strip()[:40]]


def _is_bare_analysis_request(message: str) -> bool:
    """Return True when the message asks to analyze a piece of content without supplying it.

    2026-10 重构：触发词从 TaskClassifier (sub_type="analysis") 驱动，
    不再硬编码 analysis_phrases 元组。正则不该替 LLM 决定用户是不是要分析内容。
    """
    if not message or len(message.strip()) > 24:
        return False

    compact = message.strip()
    if not _get_classifier().has_signal(compact, "analysis"):
        return False

    if any(marker in compact for marker in ("：", ":", "\n", "http://", "https://")):
        return False

    return True


def _resolve_session_cwd(request: "AgentChatRequest", user_id: str) -> Path | None:
    """解析本轮会话的工作目录（对标 Codex 的 <cwd>）。

    优先用已绑定的 workspace_id；没有绑定则回退到 folder_context 里的本地路径。
    只返回一个绝对路径，不读任何文件内容。
    """
    if request.workspace_id:
        try:
            from app.api.routers import workspace as workspace_api

            return workspace_api._resolve_root(request.workspace_id, user_id)
        except Exception as exc:
            logger.warning(f"[chat_agent] resolve workspace root failed: {exc}")
    fctx = request.folder_context or {}
    raw = str(fctx.get("path") or "").strip()
    if raw:
        candidate = Path(raw)
        if candidate.is_dir():
            return candidate.resolve()
        logger.warning(f"[chat_agent] folder path not a dir: {raw}")
    return None


async def _try_local_file_search(
    message: str,
    session_id: str,
    user_id: str,
    request: "AgentChatRequest",
    workspace_root: Path | None = None,
) -> dict | None:
    if not _looks_like_local_file_request(message) or request.agent_id:
        return None
    _trigger_words = _get_third_party_trigger_words()
    if _trigger_words and any(kw in message.lower() for kw in _trigger_words):
        logger.info(
            f"[chat_agent] skip local_file_search: third-party platform keyword detected in '{message[:60]}'"
        )
        return None

    terms = _extract_local_file_terms(message)
    matches: list[dict] = []
    all_files: list[dict] = []
    all_file_count = 0

    try:
        from app.services import chat_file as chat_file_svc

        records = await chat_file_svc.list_files_for_session(session_id)
        for f in records:
            name = str(f.get("name") or "")
            content = str(f.get("content_text") or "")[:20000]
            meta = f.get("meta") or {}
            meta_text = str(meta.get("topic") or meta.get("source") or "")
            all_file_count += 1
            if len(all_files) < 40:
                all_files.append(
                    {
                        "name": name,
                        "path": name,
                        "size": f.get("size") or 0,
                        "source": "对话文件",
                        "meta": meta,
                    }
                )
            haystack = (name + "\n" + content + "\n" + meta_text).lower()
            if any(term.lower() in haystack for term in terms):
                matches.append(
                    {
                        "name": name,
                        "path": name,
                        "size": f.get("size") or 0,
                        "source": "对话文件",
                        "meta": meta,
                    }
                )
    except Exception as exc:
        logger.warning(f"[chat_agent] local chat-file scan failed: {exc}")

    if workspace_root is not None:
        try:
            root = workspace_root
            skip_dirs = {
                ".git",
                "node_modules",
                "__pycache__",
                ".venv",
                "venv",
                "dist",
                "build",
                ".next",
            }
            text_exts = {
                ".md",
                ".txt",
                ".json",
                ".csv",
                ".py",
                ".ts",
                ".vue",
                ".js",
                ".html",
                ".yaml",
                ".yml",
                ".org",
            }
            for p in root.rglob("*"):
                try:
                    rel = str(p.relative_to(root))
                    if any(part in skip_dirs for part in p.parts):
                        continue
                    if not p.is_file():
                        continue
                    all_file_count += 1
                    if len(all_files) < 40:
                        all_files.append(
                            {
                                "name": p.name,
                                "path": rel,
                                "size": p.stat().st_size if p.stat().st_size is not None else 0,
                                "source": "本地工作区",
                            }
                        )
                    haystack = rel.lower()
                    if p.suffix.lower() in text_exts and p.stat().st_size <= 200_000:
                        try:
                            haystack += (
                                "\n"
                                + p.read_text(encoding="utf-8", errors="ignore")[:20000].lower()
                            )
                        except Exception:
                            pass
                    if any(term.lower() in haystack for term in terms):
                        matches.append(
                            {
                                "name": p.name,
                                "path": rel,
                                "size": p.stat().st_size,
                                "source": "本地工作区",
                            }
                        )
                except Exception:
                    continue
        except Exception as exc:
            logger.warning(f"[chat_agent] local workspace scan failed: {exc}")

    seen = {(m.get("path"), m.get("name")) for m in matches}
    deduped = []
    for m in matches:
        key = (m.get("path"), m.get("name"))
        if key not in seen:
            continue
        seen.discard(key)
        deduped.append(m)
    matches = deduped[:20]

    if matches:
        lines = [f"📁 找到 {len(matches)} 个匹配文件（关键词：{' / '.join(terms)}）："]
        for m in matches:
            tag = m.get("source") or ""
            size = m.get("size") or 0
            size_text = f"{size / 1024:.1f} KB" if size >= 1024 else f"{size} B"
            topic = (m.get("meta") or {}).get("topic") if isinstance(m.get("meta"), dict) else ""
            topic_text = f" · {topic}" if topic else ""
            lines.append(
                f"- **{m.get('name')}** [{tag} · {size_text}]\n  `{m.get('path')}`{topic_text}"
            )
    else:
        lines = [f"未找到含「{' / '.join(terms)}」的文件。"]
        if all_files:
            lines.append(f"当前可访问的本地/对话文件共 {all_file_count} 个，以下是部分文件：")
            for f in all_files[-8:]:
                lines.append(f"- {f.get('name')} `{f.get('path')}`")
        else:
            lines.append("当前会话没有对话文件，也没有绑定本地工作区，所以不会去搜索互联网热点。")
    summary = "\n".join(lines)

    intent = {
        "action": "local_file_search",
        "tools": ["local_file_search"],
        "topic": message[:100],
        "params": None,
        "confidence": 1.0,
        "is_chat": False,
    }
    await chat_session.add_message(
        session_id,
        "assistant",
        summary,
        {"intent": intent, "workflow_status": "completed", "source": "local_file_search"},
    )
    _cs = await chat_session.get_creative_state(session_id)
    return {
        "session_id": session_id,
        "status": "shortcut_completed",
        "intent": intent,
        "message": summary,
        "plan_steps": ["local_file_search"],
        "creative_state": _cs,
    }


async def _generate_session_summary(
    session_id: str,
    recent_limit: int = 2,
) -> str | None:
    """用 LLM 将旧消息压缩为摘要，保留最近 recent_limit 条不参与摘要。"""
    from app.engine.governance.llm_circuit import get_llm_circuit
    from app.services import chat_session as _cs

    _circuit = get_llm_circuit()
    if not _circuit.allow_request():
        return None

    try:
        all_messages = await _cs.list_messages(session_id, limit=200)
        user_assistant_msgs = [m for m in all_messages if m["role"] in ("user", "assistant")]

        if len(user_assistant_msgs) <= recent_limit:
            return None

        old_msgs = user_assistant_msgs[:-recent_limit]

        history_lines = []
        for m in old_msgs:
            role = "用户" if m["role"] == "user" else "助手"
            content = m["content"][:500]
            history_lines.append(f"{role}：{content}")

        history_text = "\n".join(history_lines)

        if not history_text.strip():
            return None

        summary_prompt = (
            "请将以下对话历史压缩为简洁但完整的摘要。必须保留：\n"
            "1. 用户的核心需求（要写什么主题、什么风格、什么平台）\n"
            "2. 关键决策和修改指令（用户要求改了什么、调整了什么）\n"
            "3. 已产出的文案标题和关键内容\n"
            "4. 用户的偏好和反馈（喜欢什么、不喜欢什么）\n"
            "不要丢失任何重要信息，但可以省略过程细节。\n"
            "摘要长度控制在 300-500 字。"
        )

        from app.agents.registry import AgentRegistry

        _registry = AgentRegistry()
        try:
            _session = _registry.build_session("chat_agent")
            _llm = _session.llm
        except Exception:
            _llm = None

        if _llm is None:
            logger.warning("[chat_agent] no LLM available for summary generation")
            return None

        result = await _llm.chat(
            [
                {"role": "system", "content": summary_prompt},
                {"role": "user", "content": f"对话历史：\n{history_text}"},
            ],
            response_format=None,
        )

        summary = result.get("content", "").strip()
        if summary and len(summary) >= 20:
            return summary

    except Exception as e:
        logger.warning(f"[chat_agent] session summary generation failed: {e}")

    return None


@router.post("/agent")
async def agent_chat(
    request: AgentChatRequest,
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    # 1. 获取或创建 Session
    if request.session_id:
        existing = await chat_session.get_session(request.session_id)
        if existing is None:
            logger.warning(
                f"[chat_agent] session_id={request.session_id} not found in DB, ensuring session"
            )
            await chat_session.ensure_session(request.session_id, user_id)
            session_id = request.session_id
        elif existing["user_id"] != user_id:
            raise HTTPException(status_code=404, detail="会话不存在")
        else:
            session_id = request.session_id
    else:
        session = await chat_session.create_session(user_id)
        session_id = session["id"]

    # 1.1 回合 ID：前端传来则复用（发送前生成），否则后端生成。
    # 本回合所有 SSE 事件（chat_turn_started / decision_made / observer 全量 /
    # workflow_completed）均盖章该值，响应体回传供前端对账。
    import uuid as _uuid

    _turn_id = request.turn_id or f"turn_{_uuid.uuid4().hex[:16]}"
    logger.info(f"[chat_agent] turn_id={_turn_id} session={session_id}")

    # 1.2 侧会话写确认未决时拒绝新请求（PRD Review Focus 3 → 409）
    try:
        from app.services import side_session as _side_svc

        await _side_svc.ensure_no_pending_write(session_id)
    except HTTPException:
        raise
    except Exception:
        pass  # 非侧会话或服务异常不阻断主流程

    # 1.3 archived 侧会话只读（PRD FR-C）：主对话入口与 add_message 一致拒绝
    try:
        from app.services import side_session as _side_svc2

        _side_row = await _side_svc2.get_side_session_row(session_id)
        if _side_row and _side_row.get("status") == "archived":
            raise HTTPException(status_code=403, detail="侧会话已归档，不能继续对话")
    except HTTPException:
        raise
    except Exception:
        pass  # 非侧会话或服务异常不阻断主流程

    # 1.5 注入作品上下文（强化版：强制AI必须使用）
    if request.work_context:
        wc = request.work_context
        logger.info(
            f"[chat_agent] work_context received: keys={list(wc.keys())}, title={wc.get('title')}, contentText_len={len(wc.get('contentText') or '')}"
        )
        work_prompt_parts = [build_work_context_prompt(wc)]
        work_prompt_parts.extend(
            [
                "",
                "=== 强制要求 ===",
                "1. 回答时必须引用上述作品的实际内容，不要说'没有内容'或'未保存'",
                "2. 如果用户要求分析/优化/改写，直接针对上述正文内容进行操作",
                "3. 绝对禁止回复'草稿文件还没有保存'或类似的内容缺失提示",
                "4. 作品内容已经完整提供给你，立即开始工作！",
                "",
            ]
        )
        await chat_session.add_message(session_id, "system", "\n".join(work_prompt_parts))

    # 1.6 注入分析上下文
    if request.analysis_context:
        await chat_session.add_message(
            session_id,
            "system",
            request.analysis_context
            + "\n\n请基于以上数据分析上下文来回答用户问题，给出具体、可操作的建议。",
        )

    # 1.7 注入草稿结构说明书
    if request.work_context:
        try:
            from app.agents.draft_schema import build_draft_schema_prompt

            schema_prompt = build_draft_schema_prompt(request.work_context)
            await chat_session.add_message(session_id, "system", schema_prompt)
        except Exception:
            pass

    # 2. 写用户消息
    await chat_session.add_message(session_id, "user", request.message)

    # 3. 输入守卫
    context = WorkflowContext(
        workflow_id=session_id,
        node_id="chat",
        user_id=user_id,
        account_id=request.account_id or "",
    )
    if request.work_context:
        context.extra["work_context"] = request.work_context
    if request.model_settings:
        context.extra["model_settings"] = request.model_settings
        logger.info(f"[chat_agent] model_settings received: {list(request.model_settings.keys())}")
    if not await _input_rule.check_pre({"message": request.message}, context):
        await chat_session.add_message(
            session_id, "assistant", "输入未通过安全校验", {"workflow_status": "error"}
        )
        return {"session_id": session_id, "status": "blocked", "message": "输入未通过安全校验"}

    # 3.5 输入清洗
    sanitized_message = _input_rule.sanitize(request.message)

    # 3.6 本地文件请求直接走文件检索
    _session_cwd = _resolve_session_cwd(request, user_id)
    _local_file_result = await _try_local_file_search(
        sanitized_message,
        session_id,
        user_id,
        request,
        workspace_root=_session_cwd,
    )
    if _local_file_result is not None:
        return _local_file_result

    # 3.7 纯分析请求但没有提供正文/上下文时，直接说明需要内容，避免空转结束。
    if (
        _is_bare_analysis_request(sanitized_message)
        and not request.work_context
        and not request.analysis_context
        and not request.file_context
        and not request.folder_context
    ):
        _direct_message = (
            "我还没有看到要分析的具体文案内容。"
            "你可以直接把文案正文粘贴在问题里，例如：\n"
            "帮我分析一下这篇文案：<粘贴正文>\n"
            "或者先在作品区选中/打开对应作品后再发送，我就能基于实际内容继续分析。"
        )
        await chat_session.add_message(
            session_id,
            "assistant",
            _direct_message,
            {
                "workflow_status": "completed",
                "source": "analysis_content_guard",
                "intent": {
                    "action": "ANALYZE_ONLY",
                    "tools": [],
                    "topic": sanitized_message[:100],
                    "confidence": 1.0,
                    "is_chat": True,
                },
            },
        )
        return {
            "session_id": session_id,
            "status": "chat",
            "intent": {
                "action": "ANALYZE_ONLY",
                "tools": [],
                "topic": sanitized_message[:100],
                "confidence": 1.0,
                "is_chat": True,
            },
            "message": _direct_message,
        }

    # 4. LLM 熔断检查
    from app.engine.governance.llm_circuit import get_llm_circuit

    _circuit = get_llm_circuit()
    if not _circuit.allow_request():
        _abort_reason = _circuit.open_reason or "LLM 连续失败"
        logger.error(f"[chat_agent] LLM circuit OPEN at entry, refusing: {_abort_reason}")
        await chat_session.add_message(
            session_id,
            "assistant",
            f"LLM 服务不可用（{_abort_reason}），请检查 API 余额和配置后重试。",
            {"workflow_status": "error", "source": "llm_circuit"},
        )
        return {
            "session_id": session_id,
            "status": "error",
            "message": f"LLM 服务不可用（{_abort_reason}），请检查 API 余额和配置",
        }

    # 5. 会话摘要（长对话压缩）
    # 逻辑：达到阈值 → 生成摘要 → 记录已摘要数 → 等新增消息再次达到阈值才重新生成
    _session_summary_text: str | None = None
    try:
        _SUMMARY_THRESHOLD = 8
        _RECENT_LIMIT = 4
        _total_msg_count = await chat_session.count_messages(session_id)
        _summary_meta = await chat_session.get_session_summary_meta(session_id)
        _last_summarized_count = (_summary_meta or {}).get("summarized_count", 0) or 0
        _new_msgs_since_summary = _total_msg_count - _last_summarized_count

        logger.info(
            f"[chat_agent] summary check: total={_total_msg_count}, last_summarized={_last_summarized_count}, new_since={_new_msgs_since_summary}, threshold={_SUMMARY_THRESHOLD}"
        )

        if _new_msgs_since_summary > _SUMMARY_THRESHOLD:
            try:
                await sse_bus.publish(
                    session_id,
                    "decision_made",
                    {
                        "decision": f"正在更新对话摘要…",
                        "iteration": 0,
                    },
                    turn_id=_turn_id,
                )
            except Exception as _sse_err:
                logger.warning(f"[chat_agent] SSE publish failed: {_sse_err}")
            _new_summary = await _generate_session_summary(session_id, recent_limit=_RECENT_LIMIT)
            if _new_summary:
                await chat_session.save_session_summary(
                    session_id,
                    _new_summary,
                    summarized_count=_total_msg_count - _RECENT_LIMIT,
                )
                _session_summary_text = _new_summary
                logger.info(
                    f"[chat_agent] summary updated, {len(_new_summary)} chars, summarized_count={_total_msg_count - _RECENT_LIMIT}"
                )
            else:
                logger.warning(
                    "[chat_agent] _generate_session_summary returned None, fallback to cached"
                )
                _cached_summary = await chat_session.get_session_summary(session_id)
                if _cached_summary:
                    _session_summary_text = _cached_summary
        else:
            _session_summary_text = await chat_session.get_session_summary(session_id)
            logger.info(
                f"[chat_agent] new msgs ({_new_msgs_since_summary}) <= threshold ({_SUMMARY_THRESHOLD}), use cached summary"
            )
    except Exception as _summary_err:
        logger.warning(f"[chat_agent] summary block error: {_summary_err}")

    # 6. 直接走 ReAct Loop — LLM 自己选工具、自己执行、自己观察
    _extra_prompt: str | None = None
    if _session_summary_text:
        _extra_prompt = f"[早期对话摘要]\n{_session_summary_text}\n[摘要结束]"

    if request.model_settings:
        _wx_target = (request.model_settings or {}).get("wechat_target_user_id", "")
        if _wx_target:
            _wx_prompt = f"[微信推送配置] wechat_target_user_id={_wx_target} — 当用户要求发到微信时，使用此 wxid 作为 to_user_id 参数调用 wechat_send_text 或 wechat_send_file。"
            if _extra_prompt:
                _extra_prompt = _extra_prompt + "\n" + _wx_prompt
            else:
                _extra_prompt = _wx_prompt

    if request.work_context:
        wc = request.work_context
        _work_prompt_parts = [build_work_context_prompt(wc)]
        _work_prompt_parts.extend(
            [
                "",
                "=== 强制要求 ===",
                "1. 回答时必须引用上述作品的实际内容，不要说'没有内容'或'未保存'",
                "2. 如果用户要求分析/优化/改写，直接针对上述正文内容进行操作",
                "3. 绝对禁止回复'草稿文件还没有保存'或类似的内容缺失提示",
                "4. 作品内容已经完整提供给你，立即开始工作！",
            ]
        )
        _work_prompt_str = "\n".join(_work_prompt_parts)
        if _extra_prompt:
            _extra_prompt = _extra_prompt + "\n" + _work_prompt_str
        else:
            _extra_prompt = _work_prompt_str
        logger.info(
            f"[chat_agent] work_context injected into extra_system_prompt, len={len(_work_prompt_str)}"
        )

    if request.file_context:
        fc = request.file_context
        _file_prompt_parts = [
            "",
            "【当前用户选中的文件】",
            f"文件名: {fc.get('name', '未知')}",
            f"类型: {fc.get('type', '未知')}",
            f"大小: {fc.get('size', 0)} 字节",
        ]
        if fc.get("contentText"):
            _file_prompt_parts.append(f"\n文件内容:\n{fc['contentText'][:6000]}")
        _file_prompt_str = "\n".join(_file_prompt_parts)
        if _extra_prompt:
            _extra_prompt = _extra_prompt + "\n" + _file_prompt_str
        else:
            _extra_prompt = _file_prompt_str
        logger.info(
            f"[chat_agent] file_context injected into extra_system_prompt, len={len(_file_prompt_str)}"
        )

    # 会话级工作目录 —— 对标 Codex 的 <environment_context><cwd>：
    # 入口只解析出一个绝对路径，写进 run context，让 file_read /
    # glob / grep / bash 这些 primitive 以它为根。绝不预扫描、预注入文件内容
    # （Codex 靠 exec_command、Claude Code 靠 read_file/glob/grep 按需读取）。
    if request.folder_context or _session_cwd is not None:
        fctx = request.folder_context or {}
        _folder_name = (
            fctx.get("name")
            or fctx.get("folderId")
            or (_session_cwd.name if _session_cwd else "未知")
        )
        _folder_parts = ["", "<environment_context>"]
        if _session_cwd is not None:
            _folder_parts.append(f"  <cwd>{_session_cwd}</cwd>")
        _folder_parts.append(f"  <folder>{_folder_name}</folder>")
        _folder_parts.append("</environment_context>")
        if _session_cwd is not None:
            _folder_parts.append(
                "以上是你这一轮的工作目录。file_read / file_write / file_edit / glob / grep / bash "
                "的相对路径都相对于该 cwd。要看目录里有什么用 glob（如 **/*）或 bash（ls / dir）；"
                "要看某个文件的内容用 file_read。不要凭文件名猜内容，先读再答。"
            )
        else:
            # 纯对话内的虚拟文件夹（文件存在数据库、没有本地路径），
            # 工具无法触达，只能把已上传的正文随环境一起给出（有上限，不无限膨胀）。
            _folder_files = fctx.get("files") or []
            if _folder_files:
                _folder_parts.append(f"该文件夹含 {len(_folder_files)} 个文件：")
                for _f in _folder_files[:5]:
                    _file_line = f"- {_f.get('name', '未知')} ({_f.get('type') or 'file'}, {_f.get('size', 0)} 字节)"
                    if _f.get("contentText"):
                        _file_line += f"\n  内容:\n  {_f['contentText'][:1200]}"
                    _folder_parts.append(_file_line)
                if len(_folder_files) > 5:
                    _folder_parts.append(
                        f"（其余 {len(_folder_files) - 5} 个文件未展开，需要时请让用户指定文件名）"
                    )
        _folder_prompt_str = "\n".join(_folder_parts)
        if _extra_prompt:
            _extra_prompt = _extra_prompt + "\n" + _folder_prompt_str
        else:
            _extra_prompt = _folder_prompt_str
        logger.info(
            f"[chat_agent] environment_context injected: folder={_folder_name}, "
            f"cwd={_session_cwd or '-'}, files={len(fctx.get('files') or [])}, len={len(_folder_prompt_str)}"
        )

    # reasoning 回声抑制状态：滚动记录最近产出的 content 尾部，
    # 模型在后续 reasoning 里重复已吐出的 answer 时跳过不推事件
    # （前端侧还有相似度折叠兜底，双保险）
    _echo_state = {"content_tail": "", "answer_text": ""}

    async def _sse_emit_callback(node_id: str, event_type: str, payload: dict) -> None:
        try:
            if event_type == "agent_message_delta":
                delta_preview = (payload.get("delta") or "")[:60]
                logger.info(
                    f"[chat_agent] SSE emit: {event_type}, node={node_id}, delta_len={len(payload.get('delta', ''))}, preview={delta_preview!r}"
                )
                _echo_state["content_tail"] = (
                    _echo_state["content_tail"] + (payload.get("delta") or "")
                )[-400:]
                # 记录本回合已实时流出的正文全文：终稿回放层据此判断
                # 是否还需要 replay（skill 已边跑边推正文时，回放会造成
                # 同一段文字重复两遍——2026-11-08 前端实锤）
                _echo_state["answer_text"] += payload.get("delta") or ""
            elif event_type == "reasoning_summary_text_delta":
                delta_preview = (payload.get("delta") or "")[:60]
                logger.info(
                    f"[chat_agent] SSE emit: {event_type}, node={node_id}, delta_len={len(payload.get('delta', ''))}, preview={delta_preview!r}"
                )
                # 回声抑制：reasoning 片段尾部（≤40 字符）已逐字出现在最近 content 中 → 跳过
                _delta = (payload.get("delta") or "").strip()
                _probe = _delta[-40:] if len(_delta) > 40 else _delta
                _tail = _echo_state["content_tail"]
                if _delta and _probe and _tail and _probe in _tail:
                    logger.info(
                        f"[chat_agent] reasoning echo suppressed: {len(_delta)} chars overlap with content tail"
                    )
                    return
            else:
                logger.info(
                    f"[chat_agent] SSE emit: {event_type}, node={node_id}, keys={list(payload.keys())}"
                )
            await sse_bus.publish(session_id, event_type, payload, turn_id=_turn_id)
        except Exception as _cb_err:
            logger.warning(f"[chat_agent] SSE emit callback error: {_cb_err}")

    agent = ChatAgent(emit_callback=_sse_emit_callback)

    # 发送 chat_turn_started 事件，标记新一轮对话开始
    # 前端用此事件作为"开始接收事件"的标志，忽略之前的 replay 事件
    await sse_bus.publish(
        session_id,
        "chat_turn_started",
        {
            "session_id": session_id,
            "message_preview": sanitized_message[:60],
            "turn_id": _turn_id,
        },
        turn_id=_turn_id,
    )
    # 注入 image_asset_ids 到消息上下文
    if request.image_asset_ids:
        # 主对话模型是纯文本模型（DeepSeek），看不见像素。这里必须把
        # 「你看不见图 + 该用哪个技能看图」写成指令，否则模型会当已知信息
        # 直接编内容（用户反馈"LLM 看不见配图"的根因之一）。
        _asset_hint = (
            f"\n\n[用户附带了 {len(request.image_asset_ids)} 张图片，asset_ids: {request.image_asset_ids}]"
            " 你无法直接看到图片内容。需要理解图片时调用 "
            "skill_call(skill='vl_analyze', arguments={'asset_id': '<上面的某个 asset_id>'})，"
            "多张图逐张调用；拿到 tags/mood/composition/color_palette 后再创作。"
        )
        sanitized_message = sanitized_message + _asset_hint
        logger.info(f"[chat_agent] image_asset_ids injected: {request.image_asset_ids}")

    # 注入 video_asset_ids 到消息上下文（含本地路径，供 LLM 调用 Skill 时传 video_path）
    if request.video_asset_ids:
        _video_parts = [
            f"用户附带了 {len(request.video_asset_ids)} 个视频，video_asset_ids: {request.video_asset_ids}"
        ]
        try:
            from capabilities.video.services.video_orchestrator import resolve_video_assets

            _v_urls, _v_paths = await resolve_video_assets(request.video_asset_ids, user_id)
            _valid_paths = [p for p in _v_paths if p]
            if _valid_paths:
                _video_parts.append(f"视频本地路径（可直接用作 video_path 参数）：{_valid_paths}")
                _video_parts.append(
                    "重要：请使用 skill_call 技能处理视频，不要用 bash/ffprobe。"
                    "口播任务（要演示幻灯片/角落人像小窗）必须用一键编排技能："
                    "skill_call(skill='talking_head_demo', arguments={'video_path': '"
                    + _valid_paths[0]
                    + "'}) — 裁剪/抠像/AI幻灯片/说到才翻页已内置，"
                    "只传 video_path 即可，禁止用 talking_head_overlay 自行组参数"
                    "（缺参数只会出字幕增强片）。"
                    "只要字幕叠加增强（不要幻灯片/小窗）才用 "
                    "skill_call(skill='talking_head_overlay', "
                    "arguments={'video_path': '" + _valid_paths[0] + "'})。"
                )
        except Exception as _ve:
            logger.warning(f"[chat_agent] resolve_video_assets failed: {_ve}")
        _video_asset_hint = "\n\n[" + "; ".join(_video_parts) + "]"
        sanitized_message = sanitized_message + _video_asset_hint
        logger.info(f"[chat_agent] video_asset_ids injected: {request.video_asset_ids}")

    try:
        result = await agent.process(
            sanitized_message,
            session_id,
            user_id,
            account_id=request.account_id or "",
            db=db,
            workflow_id=session_id,
            agent_id=request.agent_id,
            model_override=request.model,
            creation_type=request.creation_type,
            extra_system_prompt=_extra_prompt,
            work_context=request.work_context,
            auto_approve=request.auto_approve,
            image_asset_ids=request.image_asset_ids,
            video_asset_ids=request.video_asset_ids,
            workspace_root=str(_session_cwd) if _session_cwd is not None else None,
        )
    except Exception as _agent_err:
        logger.error(f"[chat_agent] agent.process() failed: {_agent_err}", exc_info=_agent_err)
        try:
            await sse_bus.publish(
                session_id,
                "workflow_error",
                {
                    "workflow_id": session_id,
                    "error": "agent processing failed",
                    "detail": str(_agent_err),
                },
                turn_id=_turn_id,
            )
        except Exception:
            pass
        raise

    # 7. 结果处理
    if result.status == "awaiting_clarification":
        output_data = result.output.output if result.output else {}
        await chat_session.add_message(
            session_id,
            "assistant",
            result.message,
            {
                "workflow_status": "awaiting_clarification",
                "source": "react_loop",
                "loop_state_path": output_data.get("_loop_state_path", ""),
                "clarification_prompt": output_data.get("_clarification_prompt", ""),
                "clarification_skill": output_data.get("_clarification_skill", ""),
                "clarification_batches": output_data.get("_clarification_batches", []),
            },
        )
        return {
            "session_id": session_id,
            "status": "awaiting_clarification",
            "clarification_prompt": output_data.get("_clarification_prompt", ""),
            "clarification_skill": output_data.get("_clarification_skill", ""),
            "clarification_batches": output_data.get("_clarification_batches", []),
            "loop_state_path": output_data.get("_loop_state_path", ""),
            "turn_id": _turn_id,
        }

    if result.status == "awaiting_confirmation":
        output_data = result.output.output if result.output else {}
        await chat_session.add_message(
            session_id,
            "assistant",
            result.message,
            {
                "workflow_status": "awaiting_confirmation",
                "source": "react_loop",
                "loop_state_path": output_data.get("_loop_state_path", ""),
                "confirmation_prompt": output_data.get("_confirmation_prompt", ""),
                "confirmation_skill": output_data.get("_confirmation_skill", ""),
            },
        )
        return {
            "session_id": session_id,
            "status": "awaiting_confirmation",
            "confirmation_prompt": output_data.get("_confirmation_prompt", ""),
            "confirmation_skill": output_data.get("_confirmation_skill", ""),
            "loop_state_path": output_data.get("_loop_state_path", ""),
            "turn_id": _turn_id,
        }

    if result.status == "awaiting_storyboard_confirmation":
        output_data = result.output.output if result.output else {}
        await chat_session.add_message(
            session_id,
            "assistant",
            result.message,
            {
                "workflow_status": "awaiting_storyboard_confirmation",
                "source": "react_loop",
                "loop_state_path": output_data.get("_loop_state_path", ""),
                "storyboard_shots": output_data.get("_storyboard_shots", []),
                "storyboard_title": output_data.get("_storyboard_title", ""),
                "storyboard_duration": output_data.get("_storyboard_duration"),
            },
        )
        return {
            "session_id": session_id,
            "status": "awaiting_storyboard_confirmation",
            "loop_state_path": output_data.get("_loop_state_path", ""),
            "storyboard_shots": output_data.get("_storyboard_shots", []),
            "storyboard_title": output_data.get("_storyboard_title", ""),
            "storyboard_duration": output_data.get("_storyboard_duration"),
            "turn_id": _turn_id,
        }

    if result.status == "awaiting_cut_plan_confirmation":
        output_data = result.output.output if result.output else {}
        await chat_session.add_message(
            session_id,
            "assistant",
            result.message,
            {
                "workflow_status": "awaiting_cut_plan_confirmation",
                "source": "react_loop",
                "loop_state_path": output_data.get("_loop_state_path", ""),
                "cut_plan": output_data.get("_cut_plan", {}),
            },
        )
        return {
            "session_id": session_id,
            "status": "awaiting_cut_plan_confirmation",
            "loop_state_path": output_data.get("_loop_state_path", ""),
            "cut_plan": output_data.get("_cut_plan", {}),
            "turn_id": _turn_id,
        }

    if result.status == "awaiting_guardian_confirmation":
        output_data = result.output.output if result.output else {}
        await chat_session.add_message(
            session_id,
            "assistant",
            result.message,
            {
                "workflow_status": "awaiting_guardian_confirmation",
                "source": "react_loop",
                "loop_state_path": output_data.get("_loop_state_path", ""),
                "confirmation_prompt": output_data.get("confirmation_prompt", ""),
                "risk_info": output_data.get("error", ""),
            },
        )
        return {
            "session_id": session_id,
            "status": "awaiting_guardian_confirmation",
            "loop_state_path": output_data.get("_loop_state_path", ""),
            "confirmation_prompt": output_data.get("confirmation_prompt", ""),
            "risk_info": output_data.get("error", ""),
            "turn_id": _turn_id,
        }

    # 侧会话写副作用暂停（review #3）：与 guardian 同构落库，持久化 loop_state_path
    # 与 side_write 详情——刷新/重连后确认弹窗可从历史消息恢复，且
    # ensure_no_pending_write 的 409 拒绝有对应的解锁入口。
    if result.status == "awaiting_side_write_confirmation":
        output_data = result.output.output if result.output else {}
        await chat_session.add_message(
            session_id,
            "assistant",
            result.message,
            {
                "workflow_status": "awaiting_side_write_confirmation",
                "source": "react_loop",
                "loop_state_path": output_data.get("_loop_state_path", ""),
                "side_write": output_data.get("side_write", {}),
            },
        )
        return {
            "session_id": session_id,
            "status": "awaiting_side_write_confirmation",
            "loop_state_path": output_data.get("_loop_state_path", ""),
            "side_write": output_data.get("side_write", {}),
            "turn_id": _turn_id,
        }

    output_data = result.output.output if result.output else None
    humanized_message = _humanize_output(result.message, output_data)

    # 2026-10-22：空消息防落库。LLM 偶发输出无任何面向用户正文的 final
    # （reasoner 空转/截断），空字符串写进 chat_messages 会污染后续轮次的
    # 上下文（LLM 看到一串空 assistant 消息，误判任务状态）。
    # 兜底链：人话正文 → 最后思考 → 诚实占位。
    _empty_output_fallback = False
    if not (humanized_message or "").strip():
        _lt = (output_data or {}).get("_last_thought", "") if isinstance(output_data, dict) else ""
        _lt_stripped = (_lt or "").strip()
        _is_internal_reasoning = any(
            _kw in _lt_stripped
            for _kw in ("不需要调用", "不涉及创作", "不涉及传输", "纯问答", "闲聊", "这是纯")
        )
        if _lt_stripped and not _is_internal_reasoning and len(_lt_stripped) > 20:
            humanized_message = _lt_stripped
        else:
            humanized_message = "本轮执行结束，但没有产出文本结果（可重发或换个说法再试）。"
        _empty_output_fallback = True

    agent_meta = {
        "workflow_status": "completed" if result.output else "error",
        "source": "react_loop",
        "sessionId": session_id,
    }
    if output_data and isinstance(output_data, dict):
        _artifacts: dict[str, Any] = {}
        _card_draft = output_data.get("card_draft")
        if _card_draft and isinstance(_card_draft, dict):
            _artifacts["card_draft_summary"] = f"{len(_card_draft.get('pages', []))}页"
        _file_path = output_data.get("file_path") or output_data.get("html_path")
        if _file_path:
            _artifacts["file_path"] = _file_path
        _asset_ids = output_data.get("asset_ids", [])
        if _asset_ids:
            _artifacts["asset_ids"] = _asset_ids
        if _artifacts:
            agent_meta["artifacts"] = _artifacts
    if _empty_output_fallback:
        agent_meta["empty_output_fallback"] = True
    await chat_session.add_message(session_id, "assistant", humanized_message, agent_meta)

    # 2026-11-08：正文回放流——先流完、后发 workflow_completed、再返回响应，
    # 保证前端「思考结束 → 正文逐字流出」而不是整段蹦出
    # 2026-11-10 防重复：skill 已通过 _observer 边跑边推过正文（answer_text
    # 非空）时，再无条件回放会令同一段文字重复两遍。终稿是已流内容的
    # 前缀延续 → 只补剩余尾部；不一致（humanize 改写/多节点流）→ 跳过回放，
    # 正文以 POST message 为准，绝不重复投喂。
    _live_answer = _echo_state.get("answer_text", "")
    if _live_answer and humanized_message.startswith(_live_answer):
        _replay_tail = humanized_message[len(_live_answer) :]
        if _replay_tail:
            logger.info(
                f"[chat_agent] answer already streamed live ({len(_live_answer)} chars), replaying tail only ({len(_replay_tail)} chars)"
            )
            await _replay_answer_stream(session_id, _turn_id, _replay_tail)
    elif _live_answer:
        logger.info(
            f"[chat_agent] answer already streamed live ({len(_live_answer)} chars) but final differs, skip replay to avoid duplication"
        )
    else:
        await _replay_answer_stream(session_id, _turn_id, humanized_message)

    try:
        await sse_bus.publish(
            session_id,
            "workflow_completed",
            {
                "workflow_id": session_id,
            },
            turn_id=_turn_id,
        )
        logger.info(
            f"[chat_agent] workflow_completed published to SSE bus for session={session_id}"
        )
    except Exception as _sse_finish_err:
        logger.warning(f"[chat_agent] failed to publish workflow_completed: {_sse_finish_err}")

    thinking = output_data.get("_last_thought", "") if output_data else ""

    creative_state_data = await chat_session.get_creative_state(session_id)

    return {
        "session_id": session_id,
        "status": result.status,
        "output": output_data,
        "message": humanized_message,
        "thinking": thinking or None,
        "creative_state": creative_state_data,
        "turn_id": _turn_id,
    }


# Confirm API
class ConfirmRequest(BaseModel):
    session_id: str = Field(..., description="会话 ID")
    loop_state_path: str = Field(..., description="循环状态文件路径")
    action: str = Field(..., pattern="^(approve|reject)$")
    feedback: str = Field(default="", description="用户反馈（可选）")
    turn_id: str | None = Field(
        default=None,
        description="首轮回合 ID（前端发送前生成）。续跑事件按该回合盖章，前端沙箱放行；不传则续跑事件不带回合标识",
    )


async def _serialize_resume_by_state(http_request: Request):
    """恢复端点并发串行化：同一 loop_state_path 一次只允许一个请求进入。

    2026-11-12 BUG-2：/confirm 与 /confirm-guardian 原本完全无锁，两个并发
    请求会各自读到同一份 loop_state 并重复续跑。这里用 yield 依赖项在端点
    执行前取锁、响应发送后释放，避免给上百行函数体重排缩进。
    其余三个恢复端点在函数体内用同一套 _get_async_lock（见各自 try/finally）。
    """
    from app.engine.reactor.loop_state import _get_async_lock

    try:
        body = await http_request.json()
        path = str(body.get("loop_state_path") or "")
    except Exception:
        path = ""

    if not path:
        # 缺路径时交给端点自己的 404 分支；yield 依赖必须 yield 一次
        yield
        return

    async with _get_async_lock(path):
        yield


@router.post("/confirm", dependencies=[Depends(_serialize_resume_by_state)])
async def confirm_action(
    request: ConfirmRequest,
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """确认或拒绝等待中的高危操作，恢复 ReactorSession。"""
    import asyncio
    import logging as _logging

    _logger = _logging.getLogger("chat_agent.confirm")
    _logger.info(
        f"[confirm] session={request.session_id} action={request.action} path={request.loop_state_path}"
    )

    from app.agents.registry import AgentRegistry
    from app.engine.reactor.loop_state import cleanup_loop_state, load_loop_state

    try:
        state = load_loop_state(request.loop_state_path)
    except FileNotFoundError:
        # 2026-11-03 死卡推广：状态文件没了，库里那条"待确认"也该退场
        try:
            await chat_session.mark_awaiting_status_resolved(
                request.session_id,
                "awaiting_confirmation",
                "confirmation_resolved",
                "rejected",
                db=db,
            )
        except Exception:
            pass
        raise HTTPException(status_code=404, detail="循环状态不存在或已过期")

    decision = "approved" if request.action == "approve" else "rejected"
    obs = f"[observation] 用户{decision}了操作" + (
        f": {request.feedback}" if request.feedback else ""
    )
    state["messages"].append({"role": "user", "content": obs})

    if request.action == "reject":
        # 落库：拒绝后旧"待确认"消息标记 resolved，重进会话不再弹卡
        try:
            await chat_session.mark_awaiting_status_resolved(
                request.session_id,
                "awaiting_confirmation",
                "confirmation_resolved",
                "rejected",
                db=db,
            )
        except Exception:
            _logger.warning("[confirm] failed to persist reject state", exc_info=True)
        await chat_session.add_message(
            request.session_id,
            "assistant",
            "操作已取消。",
            {"workflow_status": "completed", "source": "react_loop"},
        )
        cleanup_loop_state(request.loop_state_path)
        return {
            "session_id": request.session_id,
            "status": "rejected",
            "message": "操作已取消。",
            "action": request.action,
        }

    _confirm_session_id = state["context_snapshot"].get("workflow_id", "") or request.session_id

    async def _run_resume_background():
        """后台续跑 ReactorSession，结果通过 SSE 实时推送。"""
        from app.core.context_vars import current_db_session as _cdbs
        from app.db.session import AsyncSessionLocal
        from app.engine.factory import get_deepseek_llm
        from app.engine.reactor.session import ReactorSession

        registry = AgentRegistry()
        session = registry.build_session(
            "chat_agent",
            workflow_id=_confirm_session_id,
            auto_approve=_resume_auto_approve(state),
        )
        session.turn_id = request.turn_id
        _bg_db = AsyncSessionLocal()
        _token = _cdbs.set(_bg_db)
        # 续跑是独立的 HTTP 请求，contextvar 不会继承首轮的绑定，
        # 必须从落盘的 context_snapshot 恢复 cwd，否则续跑时被批准的工具
        # 会以默认工作区为根，所有相对路径都指错地方。
        from app.core.context_vars import current_workspace_root as _cwr

        _cwd_token = _cwr.set(
            (state.get("context_snapshot", {}) or {}).get("workspace_root") or None
        )
        try:
            _ctx_snapshot = state.get("context_snapshot", {}) or {}
            context = dict(_ctx_snapshot)
            context.pop("extra", None)
            _existing_clarification = _ctx_snapshot.get("clarification_result", {}) or {}
            if _existing_clarification:
                context["clarification_result"] = _existing_clarification
            context.setdefault("user_id", user_id)
            context.setdefault("workflow_id", _confirm_session_id)

            # obs 已在上方追加进 state["messages"]，这里不能再拼一遍（会重复）；
            # 原始请求本身就在历史首条 user 消息里，无需再复述。
            # emit_workflow_completed=False：正文由本端点落库后随 agent_loop_done
            # 发布（唯一终态），run() 内部的终态会把 SSE 流提前关掉（正文丢失）。
            output = await session.run(
                "",
                context=context,
                seed_messages=state.get("messages") or None,
                emit_workflow_completed=False,
            )
        finally:
            _cdbs.reset(_token)
            _cwr.reset(_cwd_token)
            try:
                cleanup_loop_state(request.loop_state_path)
            except Exception:
                pass

        _output_ended_reason = output.get("ended_reason", "") if isinstance(output, dict) else ""
        _is_paused_output = _output_ended_reason in (
            "clarification_paused",
            "guardian_paused",
            "storyboard_paused",
            "cut_plan_paused",
        )
        if _is_paused_output:
            logger.info(
                f"[confirm] resume paused again (ended_reason={_output_ended_reason}), skipping agent_loop_done"
            )
            return

        output_data = output if isinstance(output, dict) else {}
        final_message = _humanize_output(output_data.get("summary", ""), output_data)
        if not final_message:
            # 2026-10-22：零工具调用的空转轮不该说"已完成"（10:33:52 实锤）。
            # 兜底链：最后思考 → 诚实占位。
            _lt = str(output_data.get("_last_thought", "") or "").strip()
            final_message = _lt or "本轮执行结束，但没有产出文本结果（可重发或换个说法再试）。"
        await chat_session.add_message(
            request.session_id,
            "assistant",
            final_message,
            {"workflow_status": "completed", "source": "react_loop"},
        )
        try:
            await sse_bus.publish(
                _confirm_session_id,
                "agent_loop_done",
                {
                    "status": "completed",
                    "message": final_message,
                    # BUG-007：终态事件带终止原因，前端据此区分正常完成/资源截断/假完成
                    "ended_reason": _output_ended_reason or "completed",
                },
                turn_id=request.turn_id,
            )
        except Exception:
            pass

    _bg_task = _spawn_background(_run_resume_background())

    # 落库：批准即标 resolved=confirmed，续跑等待期间刷新/重进不再弹旧确认卡
    try:
        await chat_session.mark_awaiting_status_resolved(
            request.session_id,
            "awaiting_confirmation",
            "confirmation_resolved",
            "confirmed",
            db=db,
        )
    except Exception:
        _logger.warning("[confirm] failed to persist approve state", exc_info=True)

    def _on_confirm_done(t: asyncio.Task) -> None:
        if t.cancelled():
            return
        exc = t.exception()
        if exc is None:
            return
        _logger.error(f"[confirm] background task failed: {exc}", exc_info=exc)
        try:
            loop = asyncio.get_running_loop()
            _spawn_background(
                sse_bus.publish(
                    request.session_id,
                    "workflow_error",
                    {
                        "error": "background task failed",
                        "detail": str(exc),
                    },
                    turn_id=request.turn_id,
                )
            )
        except Exception:
            pass

    _bg_task.add_done_callback(_on_confirm_done)
    _logger.info(f"[confirm] approve → background resume started for session={request.session_id}")

    return {
        "session_id": request.session_id,
        "status": "resumed",
        "message": "操作已确认，正在继续执行…",
        "action": request.action,
    }


# Clarify API
class ConfirmGuardianRequest(BaseModel):
    session_id: str = Field(..., description="会话 ID")
    loop_state_path: str = Field(..., description="循环状态文件路径")
    action: str = Field(
        ..., pattern="^(approve|reject)$", description="approve 批准执行，reject 拒绝"
    )
    feedback: str = Field(default="", description="用户反馈（可选）")
    turn_id: str | None = Field(default=None, description="首轮回合 ID")


@router.post("/confirm-guardian", dependencies=[Depends(_serialize_resume_by_state)])
async def confirm_guardian_action(
    request: ConfirmGuardianRequest,
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """确认或拒绝 Guardian 拦截的高危操作。"""
    import asyncio
    import logging as _logging

    _logger = _logging.getLogger("chat_agent.confirm_guardian")
    _logger.info(f"[confirm-guardian] session={request.session_id} action={request.action}")

    from app.agents.registry import AgentRegistry
    from app.engine.reactor.loop_state import cleanup_loop_state, load_loop_state

    try:
        state = load_loop_state(request.loop_state_path)
    except FileNotFoundError:
        # 2026-11-03 死卡推广：库里那条"等待批准"消息同步退场
        try:
            await chat_session.mark_awaiting_status_resolved(
                request.session_id,
                "awaiting_guardian_confirmation",
                "guardian_resolved",
                "rejected",
                db=db,
            )
        except Exception:
            pass
        raise HTTPException(status_code=404, detail="循环状态不存在或已过期")

    if not state.get("_awaiting_guardian_confirmation"):
        raise HTTPException(status_code=400, detail="此循环状态不是 Guardian 确认类型")

    if request.action == "reject":
        decision = "rejected"
        obs = f"[observation] 用户拒绝了此操作" + (
            f": {request.feedback}" if request.feedback else ""
        )
        state["messages"].append({"role": "user", "content": obs})
        # 落库：拒绝后旧"等待批准"消息标记 resolved，重进会话不再弹卡
        try:
            await chat_session.mark_awaiting_status_resolved(
                request.session_id,
                "awaiting_guardian_confirmation",
                "guardian_resolved",
                "rejected",
                db=db,
            )
        except Exception:
            _logger.warning("[confirm-guardian] failed to persist reject state", exc_info=True)
        await chat_session.add_message(
            request.session_id,
            "assistant",
            "操作已取消。",
            {"workflow_status": "completed", "source": "react_loop"},
        )
        cleanup_loop_state(request.loop_state_path)
        try:
            await sse_bus.publish(
                request.session_id,
                "agent_loop_done",
                {
                    "status": "rejected",
                    "message": "操作已取消。",
                },
                turn_id=request.turn_id,
            )
        except Exception:
            pass
        return {
            "session_id": request.session_id,
            "status": "rejected",
            "message": "操作已取消。",
        }

    _guardian_session_id = state["context_snapshot"].get("workflow_id", "") or request.session_id

    async def _run_guardian_resume_background():
        from app.core.context_vars import current_db_session as _cdbs
        from app.db.session import AsyncSessionLocal
        from app.engine.factory import get_deepseek_llm
        from app.engine.reactor.session import ReactorSession

        registry = AgentRegistry()
        session = registry.build_session(
            "chat_agent",
            workflow_id=_guardian_session_id,
            auto_approve=_resume_auto_approve(state),
        )
        session.turn_id = request.turn_id
        # skill_call 一次性确认（2026-11-05）：用户已显式批准本次工具调用，
        # 续跑后同 turn 内后续 low/medium 风险的 skill_call 不再重复弹卡，
        # 否则一轮视频任务会连撞 N 次确认闸门（15:47/15:51 日志实锤）。
        session.skill_call_once_confirmed = True
        _bg_db = AsyncSessionLocal()
        _token = _cdbs.set(_bg_db)
        # 同 confirm 续跑：独立请求拿不到首轮的 contextvar，从落盘快照恢复 cwd，
        # 否则被批准的工具（如 bash）会以默认工作区为根跑偏。
        from app.core.context_vars import current_workspace_root as _cwr

        _cwd_token = _cwr.set(
            (state.get("context_snapshot", {}) or {}).get("workspace_root") or None
        )
        try:
            _ctx_snapshot = state.get("context_snapshot", {}) or {}
            context = dict(_ctx_snapshot)
            context.pop("extra", None)
            context.setdefault("user_id", user_id)
            context.setdefault("workflow_id", _guardian_session_id)

            _original_topic = _ctx_snapshot.get("topic", "")
            _pending = state.get("pending_skill_call", {})
            _tool_name = _pending.get("name", "") if isinstance(_pending, dict) else ""
            _tool_args = _pending.get("arguments", {}) if isinstance(_pending, dict) else {}
            import json as _json

            _args_preview = _json.dumps(_tool_args, ensure_ascii=False, default=str)[:200]

            # Execute the intercepted tool call now that user has approved it.
            # This is critical: without actually executing the tool, the LLM
            # only sees "user approved" text but no result, so it either
            # re-invokes the tool (triggering another Guardian confirm) or
            # gives up, leaving the task stuck.
            # We bypass Guardian by calling session._transport.call() directly,
            # since the user has already explicitly approved this specific call.
            _exec_result = None
            if _tool_name:
                try:
                    _tool = session._transport.get_tool(_tool_name)
                    if _tool is not None:
                        from app.engine.schemas import Permission

                        for _perm in _tool.required_permissions:
                            if _perm not in session.allowed_permissions:
                                _exec_result = {
                                    "ok": False,
                                    "error": f"permission denied: {_perm.value}",
                                }
                                break
                        if _exec_result is None:
                            await sse_bus.publish(
                                _guardian_session_id,
                                "tool_call_start",
                                {
                                    "node_id": "reactor",
                                    "iteration": 0,
                                    "tool_name": _tool_name,
                                    "inputs": _tool_args,
                                },
                                turn_id=request.turn_id,
                            )
                            _exec_result = await session._transport.call(_tool_name, _tool_args)
                            _exec_ok = (
                                _exec_result.get("ok", True)
                                if isinstance(_exec_result, dict)
                                else True
                            )
                            await sse_bus.publish(
                                _guardian_session_id,
                                "tool_call_end",
                                {
                                    "node_id": "reactor",
                                    "iteration": 0,
                                    "tool_name": _tool_name,
                                    "success": _exec_ok,
                                    "summary": (
                                        str(_exec_result)[:200]
                                        if isinstance(_exec_result, dict)
                                        else str(_exec_result)[:200]
                                    ),
                                    "duration_ms": 0,
                                },
                                turn_id=request.turn_id,
                            )
                    else:
                        _exec_result = {"ok": False, "error": f"unknown tool: {_tool_name}"}
                    _logger.info(
                        f"[confirm-guardian] executed approved tool {_tool_name}, "
                        f"ok={_exec_result.get('ok') if isinstance(_exec_result, dict) else 'N/A'}"
                    )
                except Exception as _exec_exc:
                    _exec_result = {"ok": False, "error": str(_exec_exc)}
                    _logger.warning(
                        f"[confirm-guardian] failed to execute approved tool {_tool_name}: {_exec_exc}"
                    )

            # 缺口2 修复（2026-11-02）：guardian resume 路径绕过主循环 cut_plan 闸门的 bug。
            # 被批准执行的技能若返回 overlay_plan_ready + needs_confirmation=True
            # （talking_head_overlay 规划完成，等待渲染确认），主循环里这会命中
            # session.py 的 cut_plan 闸门分支（挂起等确认）；但 resume 路径原本把该
            # 结果当 observation 文本喂回 LLM → 模型幻觉「用户已批准，带 confirm
            # 参数再调一次」→ 再次撞 Guardian → 死循环（19:56-19:57 日志实锤）。
            # 此处复刻主循环闸门：落盘 loop_state + SSE cut_plan_confirmation_required
            # （前端弹确认卡），确认后由 POST /confirm-cut-plan 以 auto_confirm
            # 确定性回放，不经 LLM。
            if (
                isinstance(_exec_result, dict)
                and _exec_result.get("event") == "overlay_plan_ready"
                and _exec_result.get("needs_confirmation")
            ):
                from app.engine.reactor.loop_state import save_loop_state as _cp_save

                # 与主循环闸门同一套取法：skill_call 包装时取内层技能名/参数。
                _cp_inner_name = _tool_name
                _cp_inner_args = _tool_args
                if _tool_name == "skill_call":
                    _cp_inner_name = str(_tool_args.get("skill") or "")
                    _cp_nested = _tool_args.get("arguments")
                    _cp_inner_args = _cp_nested if isinstance(_cp_nested, dict) else {}

                _cp_loop_state = {
                    "messages": state.get("messages") or [],
                    "iteration": 0,
                    "context_snapshot": context,
                    "pending_skill_call": {
                        "name": _cp_inner_name,
                        "arguments": dict(_cp_inner_args),
                    },
                    "cut_plan": _exec_result.get("data", {}),
                    "_awaiting_cut_plan_confirmation": True,
                }
                _cp_path = _cp_save(_cp_loop_state)
                _logger.info(
                    f"[confirm-guardian] cut_plan gate hit after approved execution "
                    f"({_cp_inner_name}), pausing for user confirmation: {_cp_path}"
                )

                try:
                    await sse_bus.publish(
                        _guardian_session_id,
                        "cut_plan_confirmation_required",
                        {
                            "node_id": "reactor",
                            "iteration": 0,
                            "skill": _cp_inner_name,
                            "plan": _exec_result.get("data", {}),
                            "message": _exec_result.get("message", ""),
                            "loop_state_path": _cp_path,
                        },
                        turn_id=request.turn_id,
                    )
                except Exception:
                    pass

                # 与主循环 paused 路径一致：落一条待确认 assistant 消息
                # （/confirm-cut-plan 的版本号按此计数）。
                try:
                    await chat_session.add_message(
                        request.session_id,
                        "assistant",
                        _exec_result.get("message") or "叠加方案已规划完成，请确认后开始渲染。",
                        {
                            "workflow_status": "awaiting_cut_plan_confirmation",
                            "source": "react_loop",
                            "loop_state_path": _cp_path,
                            "cut_plan": _exec_result.get("data", {}),
                        },
                    )
                except Exception:
                    _logger.warning(
                        "[confirm-guardian] failed to persist awaiting_cut_plan message",
                        exc_info=True,
                    )
                return

            # Build resume intent: include the actual execution result so
            # the LLM can continue reasoning with real data.
            if _exec_result is not None and isinstance(_exec_result, dict):
                _result_preview = _json.dumps(_exec_result, ensure_ascii=False, default=str)[:500]
                resume_intent = (
                    f"[observation] 用户批准并执行了此操作：{_tool_name}({_args_preview})\n"
                    f"执行结果：{_result_preview}"
                )
            else:
                resume_intent = f"[observation] 用户批准了此操作：{_tool_name}({_args_preview})"
            if _original_topic:
                resume_intent += f"\n\n请继续执行原始请求：{_original_topic}"
            # 用户随批准一起输入的补充说明（例如"这条命令别带管道，改用 --depth 1"），
            # 直接拼进 observation，模型才能改命令而不是原样重试。
            _user_feedback = (request.feedback or "").strip()
            if _user_feedback:
                resume_intent += f"\n\n用户补充说明：{_user_feedback}"

            # 关键：回灌暂停前的完整历史。只喂一句 observation 的话，
            # 模型看不到自己上一轮发过什么命令、报了什么错，只能凭这句话
            # 重新决策 → 重发同一条命令 → 再次撞 Guardian → 二次暂停死循环。
            # emit_workflow_completed=False：正文由本端点落库后随 agent_loop_done
            # 发布（唯一终态），run() 内部的终态会把 SSE 流提前关掉（正文丢失）。
            output = await session.run(
                resume_intent,
                context=context,
                seed_messages=state.get("messages") or None,
                emit_workflow_completed=False,
            )
        finally:
            _cdbs.reset(_token)
            _cwr.reset(_cwd_token)
            try:
                cleanup_loop_state(request.loop_state_path)
            except Exception:
                pass

        _output_ended_reason = output.get("ended_reason", "") if isinstance(output, dict) else ""
        _is_paused_output = _output_ended_reason in (
            "clarification_paused",
            "guardian_paused",
            "storyboard_paused",
            "cut_plan_paused",
        )
        if _is_paused_output:
            logger.info(
                f"[guardian] resume paused again (ended_reason={_output_ended_reason}), skipping agent_loop_done"
            )
            return

        output_data = output if isinstance(output, dict) else {}
        final_message = _humanize_output(output_data.get("summary", ""), output_data)
        if not final_message:
            # 2026-10-22：零工具调用的空转轮不该说"已完成"（10:33:52 实锤）。
            # 兜底链：最后思考 → 诚实占位。
            _lt = str(output_data.get("_last_thought", "") or "").strip()
            final_message = _lt or "本轮执行结束，但没有产出文本结果（可重发或换个说法再试）。"
        await chat_session.add_message(
            request.session_id,
            "assistant",
            final_message,
            {"workflow_status": "completed", "source": "react_loop"},
        )
        try:
            await sse_bus.publish(
                _guardian_session_id,
                "agent_loop_done",
                {
                    "status": "completed",
                    "message": final_message,
                    # BUG-007：终态事件带终止原因，前端据此区分正常完成/资源截断/假完成
                    "ended_reason": _output_ended_reason or "completed",
                },
                turn_id=request.turn_id,
            )
        except Exception:
            pass

    _bg_task = _spawn_background(_run_guardian_resume_background())

    # 落库：批准即标 resolved=confirmed，续跑等待期间刷新/重进不再弹旧批准卡
    try:
        await chat_session.mark_awaiting_status_resolved(
            request.session_id,
            "awaiting_guardian_confirmation",
            "guardian_resolved",
            "confirmed",
            db=db,
        )
    except Exception:
        _logger.warning("[confirm-guardian] failed to persist approve state", exc_info=True)

    def _on_guardian_done(t: asyncio.Task) -> None:
        if t.cancelled():
            return
        exc = t.exception()
        if exc is None:
            return
        _logger.error(f"[confirm-guardian] background task failed: {exc}", exc_info=exc)
        try:
            loop = asyncio.get_running_loop()
            _spawn_background(
                sse_bus.publish(
                    request.session_id,
                    "workflow_error",
                    {
                        "error": "background task failed",
                        "detail": str(exc),
                    },
                    turn_id=request.turn_id,
                )
            )
        except Exception:
            pass

    _bg_task.add_done_callback(_on_guardian_done)
    _logger.info(
        f"[confirm-guardian] approve → background resume started for session={request.session_id}"
    )

    return {
        "session_id": request.session_id,
        "status": "resumed",
        "message": "操作已确认，正在继续执行…",
        "action": request.action,
    }


class ClarifyRequest(BaseModel):
    model_config = {"arbitrary_types_allowed": True}
    session_id: str = Field(..., description="会话 ID")
    loop_state_path: str = Field(..., description="循环状态文件路径")
    answers: dict = Field(default_factory=dict, description="用户回答 {field_name: value}")
    batch_id: str = Field(default="", description="当前批次 ID")
    action: str = Field(default="answer", pattern="^(answer|skip|cancel)$")
    user_comment: str = Field(default="", description="用户补充说明")
    turn_id: str | None = Field(
        default=None,
        description="首轮回合 ID（前端发送前生成）。续跑事件按该回合盖章，前端沙箱放行；不传则续跑事件不带回合标识",
    )


@router.post("/clarify")
async def clarify_action(
    request: ClarifyRequest,
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """回答澄清问题，恢复 ReactorSession。"""
    from app.agents.clarification_graph import collect_clarify_fields, compile_clarification_result
    from app.agents.registry import AgentRegistry
    from app.core.context_vars import current_db_session
    from app.engine.reactor.loop_state import (
        _get_async_lock,
        check_clarification_timeout,
        cleanup_loop_state,
        load_loop_state,
        mark_loop_state_expired,
    )

    _file_lock = _get_async_lock(request.loop_state_path)
    await _file_lock.acquire()
    try:

        try:
            state = load_loop_state(request.loop_state_path)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="循环状态不存在或已过期")

        if state.get("_clarification_expired"):
            raise HTTPException(status_code=410, detail="澄清已超时，按默认设置继续")

        _timeout_status = check_clarification_timeout(state)
        if _timeout_status == "expired":
            mark_loop_state_expired(request.loop_state_path)
            raise HTTPException(status_code=410, detail="澄清已超时，按默认设置继续")

        if request.action == "cancel":
            _pending = state.get("pending_clarification") or state.get("pending_skill_call") or {}
            _skill_name = _pending.get("name", "") if isinstance(_pending, dict) else ""
            _cancel_session_id = (
                state["context_snapshot"].get("workflow_id", "") or request.session_id
            )

            registry = AgentRegistry()
            session = registry.build_session(
                "chat_agent",
                workflow_id=_cancel_session_id,
                auto_approve=_resume_auto_approve(state),
            )
            session.turn_id = request.turn_id
            _CLARIFY_TRIGGER_NODE_TYPES_CANCEL = {
                "produce",
                "create",
                "design",
                "generate",
                "copywrite",
                "card_design",
            }
            _clarify_skills_cancel = [
                s
                for s in getattr(session, "_skills", [])
                if getattr(s, "node_type", "") in _CLARIFY_TRIGGER_NODE_TYPES_CANCEL
                and getattr(s, "clarify_meta", None)
            ]
            _fields = (
                collect_clarify_fields(_clarify_skills_cancel, clarified_answers={})
                if _clarify_skills_cancel
                else {}
            )
            _ctx_snapshot = state.get("context_snapshot", {}) or {}
            _model_settings = _ctx_snapshot.get("model_settings", {}) or _ctx_snapshot.get(
                "extra", {}
            ).get("model_settings", {})
            _clarification_result = compile_clarification_result(
                all_answers={},
                fields=_fields,
                model_settings=_model_settings,
            )

            try:
                await chat_session.mark_clarification_resolved(request.session_id)
            except Exception:
                pass

            context = dict(_ctx_snapshot)
            context.pop("extra", None)
            if isinstance(context, dict):
                context["clarification_result"] = _clarification_result
                context.update(_clarification_result)
            # 用户已明确取消澄清——续跑中 Layer 2 不得再补弹任何表单
            context["user_clarified"] = True
            context.setdefault("user_id", user_id)
            context.setdefault("workflow_id", _cancel_session_id)

            _original_topic = _ctx_snapshot.get("topic", "")
            _resume_intent = "用户取消了澄清，按默认设置继续"
            if _original_topic:
                _resume_intent = f"用户取消了澄清，按默认设置继续。\n\n原始请求：{_original_topic}"

            token = current_db_session.set(db)
            try:
                # emit_workflow_completed=False：同 /confirm-guardian，正文随
                # agent_loop_done 发布，内部终态会提前关流
                output = await session.run(
                    _resume_intent,
                    context=context,
                    seed_messages=state.get("messages") or None,
                    emit_workflow_completed=False,
                )
            finally:
                current_db_session.reset(token)
                try:
                    cleanup_loop_state(request.loop_state_path)
                except Exception:
                    pass

            output_data = output if isinstance(output, dict) else {}
            final_message = _humanize_output(output_data.get("summary", ""), output_data)
            if not final_message:
                # 2026-10-22：零工具调用的空转轮不该说"已完成"（10:33:52 实锤）。
                # 兜底链：最后思考 → 诚实占位。
                _lt = str(output_data.get("_last_thought", "") or "").strip()
                final_message = _lt or "本轮执行结束，但没有产出文本结果（可重发或换个说法再试）。"
            return {
                "session_id": request.session_id,
                "status": "completed",
                "message": final_message,
                "output": output,
                "clarification_result": _clarification_result,
            }

        if request.action == "skip":
            pass

        _existing = state.get("clarified_answers", {})
        if request.action == "answer":
            _existing.update(request.answers)
        state["clarified_answers"] = _existing

        _pending = state.get("pending_clarification") or state.get("pending_skill_call") or {}
        _skill_name = _pending.get("name", "") if isinstance(_pending, dict) else ""

        _clarify_session_id = state["context_snapshot"].get("workflow_id", "") or request.session_id

        registry = AgentRegistry()
        session = registry.build_session(
            "chat_agent",
            workflow_id=_clarify_session_id,
            auto_approve=_resume_auto_approve(state),
        )
        session.turn_id = request.turn_id
        _CLARIFY_TRIGGER_NODE_TYPES = {
            "produce",
            "create",
            "design",
            "generate",
            "copywrite",
            "card_design",
        }
        _clarify_skills = [
            s
            for s in getattr(session, "_skills", [])
            if getattr(s, "node_type", "") in _CLARIFY_TRIGGER_NODE_TYPES
            and getattr(s, "clarify_meta", None)
        ]
        _fields = (
            collect_clarify_fields(_clarify_skills, clarified_answers=_existing)
            if _clarify_skills
            else {}
        )
        _ctx_snapshot = state.get("context_snapshot", {}) or {}
        _model_settings = _ctx_snapshot.get("model_settings", {}) or _ctx_snapshot.get(
            "extra", {}
        ).get("model_settings", {})
        _clarification_result = compile_clarification_result(
            all_answers=_existing,
            fields=_fields,
            model_settings=_model_settings,
        )

        _answer_summary = (
            ", ".join(f"{k}={v}" for k, v in request.answers.items())
            if request.answers
            else "skipped"
        )
        obs = f"[observation] 用户确认了创作偏好: {_answer_summary}"
        state["messages"].append({"role": "user", "content": obs})

        try:
            await sse_bus.publish(
                _clarify_session_id,
                "agent_clarification_ack",
                {"batch_id": request.batch_id, "action": request.action},
                turn_id=request.turn_id,
            )
        except Exception:
            pass

        try:
            await chat_session.mark_clarification_resolved(request.session_id)
        except Exception:
            pass

        # obs 已追加进 state["messages"]（见上），续跑直接回灌整份历史，
        # 不再重复拼一遍 observation。
        _resume_intent = ""

        async def _run_clarify_resume_background():
            from app.core.context_vars import current_db_session as _cdbs
            from app.db.session import AsyncSessionLocal

            _bg_db = AsyncSessionLocal()
            _bg_token = _cdbs.set(_bg_db)
            try:
                context = dict(_ctx_snapshot)
                context.pop("extra", None)
                if isinstance(context, dict):
                    context["clarification_result"] = _clarification_result
                    context.update(_clarification_result)
                # 用户本轮已参与澄清（answer/skip/cancel 均算）。
                # Layer 1（ask_user_question，LLM 自由提问，键为 q_0/q_1…）与
                # Layer 2（技能 clarify_meta 字段键）键空间不同，用户答完
                # Layer 1 后 clarification_result 里技能字段仍可能"缺失"，
                # Layer 2 若再补弹就是用户视角的"不相干澄清单"死循环
                # （19:15-19:31 实测 5/5 复现）。此标记让 Layer 2 在本轮
                # 续跑中完全静默，后续缺参由 LLM 自己走 Layer 1 问。
                context["user_clarified"] = True
                context.setdefault("user_id", user_id)
                context.setdefault("workflow_id", _clarify_session_id)

                output = await session.run(
                    _resume_intent,
                    context=context,
                    seed_messages=state.get("messages") or None,
                    emit_workflow_completed=False,
                )
            finally:
                _cdbs.reset(_bg_token)
                try:
                    cleanup_loop_state(request.loop_state_path)
                except Exception:
                    pass

            # session.run() 可能因后续澄清/确认而暂停返回，
            # 此时不应发 agent_loop_done（终态事件，会关闭 SSE 流）。
            # 只有正常完成才发。
            _output_ended_reason = (
                output.get("ended_reason", "") if isinstance(output, dict) else ""
            )
            _output_keys = (
                list(output.keys()) if isinstance(output, dict) else type(output).__name__
            )
            logger.warning(
                f"[clarify] session.run() output: ended_reason={_output_ended_reason!r}, keys={_output_keys}, type={type(output).__name__}"
            )
            # 强制写文件日志
            try:
                import pathlib

                pathlib.Path("clarify_debug.log").write_text(
                    f"ended_reason={_output_ended_reason!r}\nkeys={_output_keys}\ntype={type(output).__name__}\n",
                )
            except Exception as _de:
                pass
            _is_paused_output = _output_ended_reason in (
                "clarification_paused",
                "guardian_paused",
                "storyboard_paused",
                "cut_plan_paused",
            )
            if _is_paused_output:
                logger.info(
                    f"[clarify] resume paused again (ended_reason={_output_ended_reason}), skipping agent_loop_done"
                )
                return

            output_data = output if isinstance(output, dict) else {}
            final_message = _humanize_output(output_data.get("summary", ""), output_data)
            if not final_message:
                # 2026-10-22：零工具调用的空转轮不该说"已完成"（10:33:52 实锤）。
                # 兜底链：最后思考 → 诚实占位。
                _lt = str(output_data.get("_last_thought", "") or "").strip()
                final_message = _lt or "本轮执行结束，但没有产出文本结果（可重发或换个说法再试）。"
            await chat_session.add_message(
                request.session_id,
                "assistant",
                final_message,
                {"workflow_status": "completed", "source": "react_loop"},
            )
            logger.warning(
                f"[clarify] PUBLISHING agent_loop_done (ended_reason={_output_ended_reason!r}, is_paused={_is_paused_output})"
            )
            try:
                await sse_bus.publish(
                    _clarify_session_id,
                    "agent_loop_done",
                    {
                        "status": "completed",
                        "message": final_message,
                        # BUG-007：终态事件带终止原因，前端据此区分正常完成/资源截断/假完成
                        "ended_reason": _output_ended_reason or "completed",
                    },
                    turn_id=request.turn_id,
                )
            except Exception:
                pass

        _bg_task = _spawn_background(_run_clarify_resume_background())

        def _on_clarify_done(t: asyncio.Task) -> None:
            if t.cancelled():
                return
            exc = t.exception()
            if exc is None:
                return
            logger.error(f"[clarify] background task failed: {exc}", exc_info=exc)
            try:
                loop = asyncio.get_running_loop()
                _spawn_background(
                    sse_bus.publish(
                        request.session_id,
                        "workflow_error",
                        {
                            "error": "background task failed",
                            "detail": str(exc),
                        },
                        turn_id=request.turn_id,
                    )
                )
            except Exception:
                pass

        _bg_task.add_done_callback(_on_clarify_done)
        logger.info(
            f"[clarify] answer → background resume started for session={request.session_id}"
        )

        return {
            "session_id": request.session_id,
            "status": "resumed",
            "message": "创作偏好已确认，正在继续执行…",
            "clarification_result": _clarification_result,
        }
    finally:
        _file_lock.release()


class StoryboardConfirmRequest(BaseModel):
    model_config = {"arbitrary_types_allowed": True}
    session_id: str = Field(..., description="会话 ID")
    loop_state_path: str = Field(..., description="循环状态文件路径")
    shots: list[dict] | None = Field(
        default=None,
        description="用户确认（或编辑）后的分镜列表；None=用落盘那份，[]=用户清空了（报错）",
    )
    action: str = Field(default="confirm", pattern="^(confirm|cancel)$")
    turn_id: str | None = Field(
        default=None, description="首轮回合 ID（前端发送前生成）。续跑事件按该回合盖章"
    )


@router.post("/confirm-storyboard")
async def confirm_storyboard_action(
    request: StoryboardConfirmRequest,
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """确认分镜草稿，按用户（可能编辑过的）分镜生成视频。

    与 /clarify 的分工：/clarify 是"补答创作偏好，继续 ReAct 循环"；
    本接口是"分镜已经看过，按它出片"。

    走确定性回放：直接用 refine_shots 重放落盘的 pending_skill_call，
    不经 LLM。因为整份分镜 JSON 若靠 LLM 从对话历史重新吐出，
    镜头一多就容易字段丢失/串镜——那是"效果不好还查不出原因"的典型来源。
    """
    from app.core.context_vars import current_db_session
    from app.engine.reactor.loop_state import (
        _get_async_lock,
        cleanup_loop_state,
        load_loop_state,
    )
    from app.engine.reactor.primitives import SkillCallPrimitive

    _sb_logger = logging.getLogger("chat_agent.confirm_storyboard")

    _file_lock = _get_async_lock(request.loop_state_path)
    await _file_lock.acquire()
    try:

        try:
            state = load_loop_state(request.loop_state_path)
        except FileNotFoundError:
            # 2026-11-03 死卡推广：库里那条"待确认分镜"同步退场
            try:
                await chat_session.mark_awaiting_status_resolved(
                    request.session_id,
                    "awaiting_storyboard_confirmation",
                    "storyboard_resolved",
                    "cancelled",
                    error_key="storyboard_error",
                    error="分镜待确认状态已过期",
                    db=db,
                )
            except Exception:
                pass
            raise HTTPException(status_code=404, detail="分镜待确认状态不存在或已过期")

        pending = state.get("pending_skill_call") or {}
        stored = state.get("storyboard") or {}
        stored_shots = stored.get("shots") or []
        stored_title = str(stored.get("title") or "").strip()
        _ctx = state.get("context_snapshot") or {}
        _workflow_id = _ctx.get("workflow_id", "") or request.session_id
        _sb_logger.info(
            f"[confirm-storyboard] session_id={request.session_id!r} "
            f"ctx_workflow_id={_ctx.get('workflow_id')!r} "
            f"resolved_workflow_id={_workflow_id!r}"
        )

        async def _resolve_event(action: str, **extra: Any) -> None:
            try:
                await sse_bus.publish(
                    _workflow_id,
                    "storyboard_confirmation_resolved",
                    {"action": action, **extra},
                    turn_id=request.turn_id,
                )
            except Exception:
                pass

        if request.action == "cancel":
            cleanup_loop_state(request.loop_state_path)
            # 落库：取消后旧"待确认分镜"消息标记 resolved，重进会话不再弹卡
            try:
                await chat_session.mark_awaiting_status_resolved(
                    request.session_id,
                    "awaiting_storyboard_confirmation",
                    "storyboard_resolved",
                    "cancelled",
                    db=db,
                )
            except Exception:
                _sb_logger.warning(
                    "[confirm-storyboard] failed to persist cancel state", exc_info=True
                )
            await _resolve_event("cancel")
            return {
                "session_id": request.session_id,
                "status": "cancelled",
                "message": "已取消，未生成视频",
            }

        if not pending.get("name"):
            cleanup_loop_state(request.loop_state_path)
            raise HTTPException(status_code=400, detail="落盘状态里没有待回放的技能调用")

        if request.shots is not None and not request.shots:
            raise HTTPException(status_code=400, detail="分镜列表为空，无可生成内容")

        shots = request.shots if request.shots else stored_shots
        if not shots:
            raise HTTPException(status_code=400, detail="没有可分镜（shots 为空）")

        call_args = dict(pending.get("arguments") or {})
        call_args["refine_shots"] = shots
        if stored_title:
            call_args["refine_title"] = stored_title

        stored_character_lock = str(stored.get("character_lock", "") or "").strip()
        if stored_character_lock:
            call_args["_character_lock"] = stored_character_lock
            _sb_logger.info(
                f"[confirm-storyboard] 角色锚定已注入 refine 参数 "
                f"(长度={len(stored_character_lock)})"
            )

        async def _run_refine_in_background() -> None:
            token = current_db_session.set(db)
            try:
                result = await SkillCallPrimitive().execute(
                    skill=str(pending["name"]),
                    arguments=call_args,
                    _user_id=str(user_id),
                    _workflow_id=str(_workflow_id),
                    _turn_id=request.turn_id or "",
                )
            except Exception as exc:
                _sb_logger.exception(f"[confirm-storyboard] refine execute failed: {exc}")
                await _resolve_event("confirm", ok=False, error=str(exc))
                try:
                    await sse_bus.publish(
                        request.session_id,
                        "workflow_error",
                        {
                            "workflow_id": request.session_id,
                            "error": "storyboard refine failed",
                            "detail": str(exc),
                        },
                        turn_id=request.turn_id,
                    )
                except Exception:
                    pass
                return
            finally:
                current_db_session.reset(token)
                cleanup_loop_state(request.loop_state_path)

            # 与 confirm-cut-plan 同一归一化：SkillCallPrimitive 返回技能
            # 最后 chunk（ok + data.output_path），不是 {success, asset_url}。
            if isinstance(result, dict):
                _rf_data = result.get("data") if isinstance(result.get("data"), dict) else {}
                ok = bool(result.get("success", result.get("ok", False)))
                from app.services.media_download_service import to_web_url as _rf_to_web

                _rf_out = (
                    result.get("output_path")
                    or _rf_data.get("output_path")
                    or _rf_data.get("asset_url")
                )
                _asset_url = result.get("asset_url") or _rf_to_web(_rf_out)
                _error = result.get("error") or _rf_data.get("error")
            else:
                ok, _asset_url, _error = False, None, None
            await _resolve_event(
                "confirm",
                ok=ok,
                asset_url=_asset_url,
                error=_error,
            )
            # 渲染结果落库（放在 resolve 事件之后：事件通知优先，落库慢/失败不挡前端时序）
            # 成功标 confirmed；失败标 failed + 原因
            # （接受时已先标 confirmed——pending_values 让这条渲染中的消息仍可被翻到）
            try:
                await chat_session.mark_awaiting_status_resolved(
                    request.session_id,
                    "awaiting_storyboard_confirmation",
                    "storyboard_resolved",
                    "confirmed" if ok else "failed",
                    error_key="storyboard_error",
                    error=_error,
                    pending_values={"confirmed"},
                )
            except Exception:
                _sb_logger.warning(
                    "[confirm-storyboard] failed to persist render result", exc_info=True
                )

        import asyncio

        # 落库：确认即标 resolved=confirmed，视频生成等待期间刷新/重进不再弹分镜卡；
        # 渲染结果（confirmed/failed）由后台任务完成后继续回写。
        try:
            await chat_session.mark_awaiting_status_resolved(
                request.session_id,
                "awaiting_storyboard_confirmation",
                "storyboard_resolved",
                "confirmed",
                db=db,
            )
        except Exception:
            _sb_logger.warning(
                "[confirm-storyboard] failed to persist confirm state", exc_info=True
            )
        _bg_task = _spawn_background(_run_refine_in_background())

        def _on_storyboard_done(t: asyncio.Task) -> None:
            if t.cancelled():
                return
            exc = t.exception()
            if exc is None:
                return
            logger.error(f"[confirm-storyboard] background task failed: {exc}", exc_info=exc)
            try:
                loop = asyncio.get_running_loop()
                _spawn_background(
                    sse_bus.publish(
                        request.session_id,
                        "workflow_error",
                        {
                            "error": "background task failed",
                            "detail": str(exc),
                        },
                        turn_id=request.turn_id,
                    )
                )
            except Exception:
                pass

        _bg_task.add_done_callback(_on_storyboard_done)

        return {
            "session_id": request.session_id,
            "status": "processing",
            "message": "视频生成已启动，进度通过 SSE 推送",
        }
    finally:
        _file_lock.release()


class CutPlanConfirmRequest(BaseModel):
    model_config = {"arbitrary_types_allowed": True}
    session_id: str = Field(..., description="会话 ID")
    loop_state_path: str = Field(..., description="循环状态文件路径")
    action: str = Field(default="confirm", pattern="^(confirm|cancel)$")
    turn_id: str | None = Field(
        default=None, description="首轮回合 ID（前端发送前生成）。续跑事件按该回合盖章"
    )


@router.post("/confirm-cut-plan")
async def confirm_cut_plan_action(
    request: CutPlanConfirmRequest,
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """确认叠加方案（cut_plan），以确定性回放续跑 talking_head_overlay。

    设计文档 3.4 #2 缺口2 的修复：主流程在规划出 OverlayPlan 后挂起
    （cut_plan_confirmation_required），用户确认后由本接口带
    gate_mode="auto_confirm" 重放落盘的 pending_skill_call——不经 LLM，
    避免重新规划出不一致的方案。cancel 则直接清理状态并广播 resolved。
    """
    from app.core.context_vars import current_db_session
    from app.engine.reactor.loop_state import (
        _get_async_lock,
        cleanup_loop_state,
        load_loop_state,
    )
    from app.engine.reactor.primitives import SkillCallPrimitive

    _cp_logger = logging.getLogger("chat_agent.confirm_cut_plan")

    _file_lock = _get_async_lock(request.loop_state_path)
    await _file_lock.acquire()
    try:
        try:
            state = load_loop_state(request.loop_state_path)
        except FileNotFoundError:
            # 2026-11-03 死卡修复：loop_state 已被清理（任务早就结束），
            # 库里那条 awaiting_cut_plan_confirmation 消息却永远是"待确认"，
            # 每次重进会话都渲染出一张点什么都 404 的死卡。这里顺手把
            # DB 状态标成 cancelled，让这张历史死卡永久退场。
            try:
                await chat_session.mark_cut_plan_resolved(
                    request.session_id,
                    "cancelled",
                    error="叠加方案待确认状态已过期",
                    db=db,
                )
            except Exception:
                logger.warning(
                    "[confirm-cut-plan] failed to expire stale awaiting message", exc_info=True
                )
            raise HTTPException(status_code=404, detail="叠加方案待确认状态不存在或已过期")

        pending = state.get("pending_skill_call") or {}
        _ctx = state.get("context_snapshot") or {}
        _workflow_id = _ctx.get("workflow_id", "") or request.session_id
        _cp_logger.info(
            f"[confirm-cut-plan] session_id={request.session_id!r} "
            f"ctx_workflow_id={_ctx.get('workflow_id')!r} "
            f"resolved_workflow_id={_workflow_id!r}"
        )

        async def _resolve_event(action: str, **extra: Any) -> None:
            try:
                await sse_bus.publish(
                    _workflow_id,
                    "cut_plan_confirmation_resolved",
                    {"action": action, **extra},
                    turn_id=request.turn_id,
                )
            except Exception:
                pass

        if request.action == "cancel":
            cleanup_loop_state(request.loop_state_path)
            # 落库：把库里那条"待确认"消息标成 cancelled，重进会话不再弹卡
            try:
                await chat_session.mark_cut_plan_resolved(request.session_id, "cancelled", db=db)
            except Exception:
                logger.warning("[confirm-cut-plan] failed to persist cancel state", exc_info=True)
            await _resolve_event("cancel")
            return {
                "session_id": request.session_id,
                "status": "cancelled",
                "message": "已取消，未渲染视频",
            }

        if not pending.get("name"):
            cleanup_loop_state(request.loop_state_path)
            raise HTTPException(status_code=400, detail="落盘状态里没有待回放的技能调用")

        call_args = dict(pending.get("arguments") or {})
        # 确定性回放：方案已确认，显式 auto_confirm 让技能跳过闸门直接渲染
        call_args["gate_mode"] = "auto_confirm"

        # 设计文档 3.4 #4 版本号：统计历史里 workflow_status=awaiting_cut_plan_confirmation
        # 的 assistant 消息条数 N（每次渲染前恰好落一条待确认消息），version=N。
        version = 1  # 兜底：读取历史失败时按首次渲染计
        try:
            _history = await chat_session.list_messages(request.session_id, limit=200)
            _n = sum(
                1
                for m in _history
                if isinstance(m, dict)
                and m.get("role") == "assistant"
                and isinstance(m.get("agent_meta"), dict)
                and m["agent_meta"].get("workflow_status") == "awaiting_cut_plan_confirmation"
            )
            if _n > 0:
                version = _n
        except Exception:
            version = 1

        # 落库：确认即标 resolved=confirmed。否则渲染这几分钟内刷新/重进，
        # 历史消息还是"待确认"，方案卡重现、可被二次确认造成重复渲染。
        # 渲染结果（version/asset_url 或 failed）由后台任务完成后继续回写。
        try:
            await chat_session.mark_cut_plan_resolved(request.session_id, "confirmed", db=db)
        except Exception:
            logger.warning("[confirm-cut-plan] failed to persist confirm state", exc_info=True)

        async def _run_replay_in_background() -> None:
            token = current_db_session.set(db)
            try:
                result = await SkillCallPrimitive().execute(
                    skill=str(pending["name"]),
                    arguments=call_args,
                    _user_id=str(user_id),
                    _workflow_id=str(_workflow_id),
                    _turn_id=request.turn_id or "",
                )
            except Exception as exc:
                _cp_logger.exception(f"[confirm-cut-plan] replay execute failed: {exc}")
                await _resolve_event("confirm", ok=False, error=str(exc))
                try:
                    await sse_bus.publish(
                        request.session_id,
                        "workflow_error",
                        {
                            "workflow_id": request.session_id,
                            "error": "cut_plan replay failed",
                            "detail": str(exc),
                        },
                        turn_id=request.turn_id,
                    )
                except Exception:
                    pass
                return
            finally:
                current_db_session.reset(token)
                cleanup_loop_state(request.loop_state_path)

            # SkillCallPrimitive.execute 返回的是技能最后一个 chunk
            # （ok + event + data.output_path），不是 {success, asset_url} 包装。
            # 2026-11-02 E2E 实锤：成片已产出但这里读不到 success → ok=False
            # → 前端误报「渲染失败」。两种形状都归一化，output_path 转 /outputs Web 路径。
            if isinstance(result, dict):
                _rp_data = result.get("data") if isinstance(result.get("data"), dict) else {}
                ok = bool(result.get("success", result.get("ok", False)))
                from app.services.media_download_service import to_web_url as _rp_to_web

                _rp_out = (
                    result.get("output_path")
                    or _rp_data.get("output_path")
                    or _rp_data.get("asset_url")
                )
                _asset_url = result.get("asset_url") or _rp_to_web(_rp_out)
                _error = result.get("error") or _rp_data.get("error")
            else:
                ok, _asset_url, _error = False, None, None
            await _resolve_event(
                "confirm",
                ok=ok,
                asset_url=_asset_url,
                error=_error,
                # 设计文档 3.4 #4：SSE 载荷携带本次渲染的版本号（cancel 路径不带）
                version=version,
            )
            # 渲染结果落库（放在 resolve 事件之后：事件通知优先，落库不挡前端时序）：
            # 成功回写 version+asset_url（重进会话能直接看片），
            # 失败标 failed（否则库里停在 confirmed，刷新后误显示成"成片"）。
            try:
                if ok:
                    await chat_session.mark_cut_plan_resolved(
                        request.session_id,
                        "confirmed",
                        version=version,
                        asset_url=_asset_url,
                    )
                else:
                    await chat_session.mark_cut_plan_resolved(
                        request.session_id,
                        "failed",
                        error=_error,
                    )
            except Exception:
                logger.warning("[confirm-cut-plan] failed to persist render result", exc_info=True)

        import asyncio

        _bg_task = _spawn_background(_run_replay_in_background())

        def _on_cut_plan_done(t: asyncio.Task) -> None:
            if t.cancelled():
                return
            exc = t.exception()
            if exc is None:
                return
            logger.error(f"[confirm-cut-plan] background task failed: {exc}", exc_info=exc)
            try:
                loop = asyncio.get_running_loop()
                _spawn_background(
                    sse_bus.publish(
                        request.session_id,
                        "workflow_error",
                        {
                            "error": "background task failed",
                            "detail": str(exc),
                        },
                        turn_id=request.turn_id,
                    )
                )
            except Exception:
                pass

        _bg_task.add_done_callback(_on_cut_plan_done)

        return {
            "session_id": request.session_id,
            "status": "processing",
            "message": "视频渲染已启动，进度通过 SSE 推送",
        }
    finally:
        _file_lock.release()


@router.post("/llm-circuit/reset")
async def reset_llm_circuit(
    user_id: str = Depends(get_current_user),
) -> dict:
    """重置 LLM 熔断器（用户充值/更换 API Key 后调用）。"""
    from app.engine.governance.llm_circuit import get_llm_circuit

    circuit = get_llm_circuit()
    old_state = circuit.to_dict()
    circuit.reset()
    logger.info(f"[chat_agent] LLM circuit manually reset by user={user_id}, old_state={old_state}")
    return {"success": True, "message": "LLM 熔断器已重置", "previous_state": old_state}


@router.get("/llm-circuit/status")
async def get_llm_circuit_status(
    user_id: str = Depends(get_current_user),
) -> dict:
    """查询 LLM 熔断器状态。"""
    from app.engine.governance.llm_circuit import get_llm_circuit

    return get_llm_circuit().to_dict()


@router.post("/llm-circuit/_internal-reset")
async def internal_reset_llm_circuit(request: Request) -> dict:
    """内部重置端点（免鉴权，仅限 localhost 调用）。用于脚本/运维重置熔断器。"""
    client_host = request.client.host if request.client else ""
    if client_host not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(status_code=403, detail="仅限本地调用")
    from app.engine.governance.llm_circuit import get_llm_circuit

    circuit = get_llm_circuit()
    old_state = circuit.to_dict()
    circuit.reset()
    logger.info(f"[chat_agent] LLM circuit internal reset, old_state={old_state}")
    return {"success": True, "message": "LLM 熔断器已重置", "previous_state": old_state}


@router.post("/llm-circuit/probe")
async def probe_llm_circuit(
    user_id: str = Depends(get_current_user),
) -> dict:
    """探测 LLM API 可用性。如果 API 恢复（如充值后），自动重置熔断器。

    前端在发送消息前调用此端点：
    - 熔断器 CLOSED → 直接返回 ok
    - 熔断器 OPEN/HALF_OPEN → 发一个极轻量 API 调用
      - 成功 → 自动重置为 CLOSED → 返回 {recovered: true}
      - 失败 → 保持 OPEN → 返回 {recovered: false, detail: ...}
    """
    from app.engine.governance.llm_circuit import get_llm_circuit

    circuit = get_llm_circuit()
    result = await circuit.probe_and_recover()
    logger.info(f"[chat_agent] LLM circuit probe: {result}")
    return result
