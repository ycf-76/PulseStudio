"""反馈驱动的自动复盘服务。

当用户点踩（dislike）一条 Agent 产出时，自动触发复盘：
1. 读取该 session 的完整对话轨迹
2. 让 LLM 分析失败原因 + 生成改进建议
3. 写入 AgentMemory（RETROSPECT_INSIGHT）
4. 如果改进建议可沉淀为 skill，生成 skill 草稿

幂等：同一 session + 同一维度 24h 内不重复触发。
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, select

from app.db.models import AgentMemory, FeedbackEvent, FeedbackEventType, MemoryType
from app.db.session import AsyncSessionLocal

logger = logging.getLogger(__name__)

_DEDUP_HOURS = 24


async def trigger_retrospect_if_needed(
    user_id: str,
    session_id: str | None,
    event_type: FeedbackEventType,
    dimension: str | None,
) -> None:
    """点踩后异步触发复盘（fire-and-forget）。

    幂等：同一 user + session + dimension 24h 内不重复。
    """
    if event_type != FeedbackEventType.DISLIKE:
        return
    if not session_id:
        return

    try:
        async with AsyncSessionLocal() as db:
            if await _is_duplicate(db, user_id, session_id, dimension):
                logger.debug(
                    f"[retrospect] dedup skip: user={user_id}, "
                    f"session={session_id}, dim={dimension}"
                )
                return

            messages = await _load_session_messages(session_id)
            if not messages:
                logger.debug(f"[retrospect] no messages for session={session_id}")
                return

            insight = await _run_llm_retrospect(user_id, session_id, messages, dimension)

            if insight:
                await _save_insight(user_id, session_id, dimension, insight)
                logger.info(
                    f"[retrospect] saved insight: user={user_id}, "
                    f"session={session_id}, dim={dimension}"
                )
    except Exception as e:
        logger.warning(f"[retrospect] trigger failed: {e}")


async def _is_duplicate(
    db: Any,
    user_id: str,
    session_id: str,
    dimension: str | None,
) -> bool:
    since = datetime.now(UTC) - timedelta(hours=_DEDUP_HOURS)
    stmt = (
        select(AgentMemory)
        .where(
            and_(
                AgentMemory.user_id == user_id,
                AgentMemory.memory_type == MemoryType.RETROSPECT_INSIGHT,
                AgentMemory.created_at >= since,
            )
        )
        .limit(1)
    )
    existing = await db.scalar(stmt)
    return existing is not None


async def _load_session_messages(session_id: str) -> list[dict]:
    from app.services.chat_session import get_recent_messages

    return await get_recent_messages(session_id, limit=20)


async def _run_llm_retrospect(
    user_id: str,
    session_id: str,
    messages: list[dict],
    dimension: str | None,
) -> dict | None:
    """让 LLM 分析对话轨迹，输出失败归因 + 改进建议。"""
    try:
        from app.engine.factory import get_deepseek_llm

        llm = get_deepseek_llm(temperature=0.3)
        if llm is None:
            logger.warning("[retrospect] no LLM available, skipping")
            return None
    except Exception:
        return None

    conversation_lines = []
    for msg in messages:
        role = msg.get("role", "?")
        content = msg.get("content", "")
        meta = msg.get("agent_meta") or {}
        status = meta.get("workflow_status", "")
        line = f"[{role}]"
        if status:
            line += f" ({status})"
        line += f" {content[:300]}"
        conversation_lines.append(line)

    conversation_text = "\n".join(conversation_lines)

    prompt = f"""你是一位 AI 创作助手的质量分析师。用户对一次创作会话的结果不满意（点踩维度：{dimension or '未指定'}），请分析失败原因并给出改进建议。

## 会话轨迹
{conversation_text}

## 分析要求
1. **失败归因**：从选题、文案、结构、风格、平台适配等维度判断最可能的失败原因
2. **改进建议**：给出 3 条具体可执行的改进建议
3. **skill 改进提示**：如果失败模式可复用（如"该用户偏好数据型内容而非情绪型"），提炼为一条可沉淀的 skill 改进提示

请输出 JSON（不要 markdown fence）：
{{
  "failure_attribution": {{
    "primary_cause": "最可能的失败原因（一句话）",
    "dimensions": ["失败维度1", "失败维度2"],
    "confidence": 0.7
  }},
  "improvement_suggestions": [
    {{"suggestion": "建议内容", "priority": "high/medium/low", "actionable": true}}
  ],
  "skill_improvement_hint": {{
    "target_skill": "可能需要改进的 skill 名（如 copywrite/content_postmortem）",
    "hint": "改进提示（如 '该用户偏好数据型内容，copywrite 应降低情绪密度、增加数据引用'）",
    "auto_applicable": false
  }}
}}"""

    try:
        raw = await llm.chat(messages=[{"role": "user", "content": prompt}])
        if not raw:
            return None

        content = raw.get("content", "") if isinstance(raw, dict) else str(raw)
        content = content.strip()
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()
        if content.endswith("```"):
            content = content[:-3].strip()

        result = json.loads(content)
        if isinstance(result, dict) and "failure_attribution" in result:
            result["_session_id"] = session_id
            result["_dimension"] = dimension
            result["_timestamp"] = datetime.now(UTC).isoformat()
            return result

    except Exception as e:
        logger.warning(f"[retrospect] LLM analysis failed: {e}")

    return None


async def _save_insight(
    user_id: str,
    session_id: str,
    dimension: str | None,
    insight: dict,
) -> None:
    from app.services.agent_memory import save_memory

    await save_memory(
        user_id=user_id,
        memory_type=MemoryType.RETROSPECT_INSIGHT,
        memory_key=f"dislike_{dimension or 'general'}_{session_id[:8]}",
        memory_value=insight,
        source="auto_retrospect",
        importance=0.8,
    )
