"""Engine factory — LLM adapters + Observer construction.

红线：
- 不 import langgraph / fastapi。
- LLM 不可用时返回 None（调用方走兜底），不抛异常。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.adapters.llm_base import BaseLLM

logger = logging.getLogger(__name__)


_MOCK_LLM_RESPONSES: dict[str, dict[str, Any]] = {
    "search": {
        "content": '{"final": true, "output": {"results": [{"note_id": "mock_1", "title": "mock 笔记", "likes": 1000}], "count": 1, "summary": "mock search result"}}',
        "reasoning_content": "搜索关键词已生成，准备调用 xhs_search 工具",
        "token_usage": 120,
    },
    "analyze": {
        "content": '{"factors": {"color": "暖色调", "composition": "俯拍", "topic": "手冲咖啡"}, "patterns": {"title_patterns": [{"type": "数字型", "template": "N个技巧"}], "content_structures": [{"structure": "总分总"}], "emotion_triggers": [{"emotion": "好奇"}]}, "insights": {"trend_signals": {"is_topic_trend": true, "trend_strength": "medium", "trend_basis": "mock 趋势依据"}, "recommendations": [{"direction": "mock 方向", "reason": "mock 理由"}]}}',
        "reasoning_content": "分析爆款因子：颜色 + 构图 + 主题",
        "token_usage": 200,
    },
    "copywrite": {
        "content": '{"title": "今日分享 | 3个让你效率翻倍的小技巧", "content": "这是 mock 文案内容，用于测试工作流链路。\\n\\n1. 第一个技巧\\n2. 第二个技巧\\n3. 第三个技巧", "tags": ["#mock", "#测试", "#效率"]}',
        "reasoning_content": "思考标题吸引力，构造文案结构，挑选标签",
        "token_usage": 350,
    },
    "audit": {
        "content": '{"passed": true, "issues": [], "suggestions": ["mock 审核建议"]}',
        "reasoning_content": "审核：无敏感词，无违规内容",
        "token_usage": 80,
    },
    "publish": {
        "content": '{"final": true, "output": {"post_id": "mock_post_001", "status": "published", "message": "发布成功"}}',
        "reasoning_content": "发布节点（xhs_publish 已移除）",
        "token_usage": 50,
    },
    "default": {
        "content": '{"result": "mock", "status": "ok"}',
        "reasoning_content": None,
        "token_usage": 100,
    },
}


class _MockLLM:
    """测试模式 LLM 适配器，返回固定响应，不连真实 API。"""

    def __init__(self, model: str = "mock-llm") -> None:
        self.model = model

    @property
    def model_name(self) -> str:
        return self.model

    def _pick_response(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        text = ""
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                text += content + "\n"
            elif isinstance(content, list):
                for item in content:
                    if isinstance(item, dict):
                        text += str(item.get("text", "")) + "\n"
        text_lower = text.lower()
        for keyword in ("search", "analyze", "copywrite", "audit", "publish"):
            if keyword in text_lower:
                resp = _MOCK_LLM_RESPONSES[keyword]
                break
        else:
            resp = _MOCK_LLM_RESPONSES["default"]
        if "r1" in self.model.lower() or "reasoner" in self.model.lower():
            return dict(resp)
        return {**resp, "reasoning_content": None}

    async def _chat_impl(
        self,
        messages: list[dict[str, Any]],
        response_format: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._pick_response(messages)

    async def _stream_chat_impl(
        self,
        messages: list[dict[str, Any]],
        response_format: dict[str, Any] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        resp = self._pick_response(messages)
        if resp.get("reasoning_content"):
            yield {
                "content": None,
                "reasoning_content": resp["reasoning_content"],
                "is_final": False,
                "token_usage": None,
            }
        yield {
            "content": resp["content"],
            "reasoning_content": None,
            "is_final": False,
            "token_usage": None,
        }
        yield {
            "content": None,
            "reasoning_content": None,
            "is_final": True,
            "token_usage": resp["token_usage"],
        }


_llm_cache: dict[str, Any] = {}


def get_deepseek_llm(
    temperature: float | None = None,
    model: str | None = None,
) -> Any | None:
    """获取 DeepSeek 适配器（按温度+模型缓存）。

    Args:
        temperature: 用户在右侧工作区调节的温度（0.0-1.0）。None 时使用默认 0.7。
        model: 模型名（如 "deepseek-chat"）。None 时使用配置默认值。

    无 API key 返回 None。
    熔断器开启时返回 None（防止余额不足时继续烧钱）。
    """
    from app.engine.governance.llm_circuit import get_llm_circuit

    circuit = get_llm_circuit()
    if not circuit.allow_request():
        logger.warning(
            f"[factory] LLM circuit OPEN, refusing request. "
            f"reason={circuit.open_reason}, total_failures={circuit.total_failures}"
        )
        return None

    temp_val = 0.7 if temperature is None else float(max(0.0, min(1.0, temperature)))
    model_val = model or ""
    cache_key = f"deepseek|{temp_val:.3f}|{model_val}"
    if cache_key in _llm_cache:
        return _llm_cache[cache_key]

    if model_val.startswith("mock-"):
        adapter = _MockLLM(model=model_val)
        _llm_cache[cache_key] = adapter
        logger.info(f"[mock-mode] LLM 使用 _MockLLM (model={model_val})，不消耗 token")
        return adapter

    normalized = _normalize_deepseek_model(model_val) if model_val else None
    from app.adapters.model_router import get_model_router

    llm = get_model_router().resolve_llm(model=normalized, temperature=temp_val)
    if llm is not None:
        _llm_cache[cache_key] = llm
    return llm


async def get_llm_for_model(
    model_id: str,
    user_id: str = "",
    temperature: float | None = None,
) -> Any | None:
    """按模型 ID 构造 LLM 适配器（支持 BYOK 任意 Provider）。

    与 get_deepseek_llm 的差异：这里走完整的「模型 → Provider → 用户凭据」
    解析链路，因此可用于「模型配置」页为技能 / 智能体指定的任意模型。
    解析失败、凭据缺失或 Provider 不支持 chat 时返回 None（由调用方回退）。
    """
    if not model_id:
        return None

    from app.engine.governance.llm_circuit import get_llm_circuit

    circuit = get_llm_circuit()
    if not circuit.allow_request():
        logger.warning(
            f"[factory] LLM circuit OPEN, refusing request. "
            f"reason={circuit.open_reason}, total_failures={circuit.total_failures}"
        )
        return None

    temp_val = 0.7 if temperature is None else float(max(0.0, min(1.0, temperature)))
    cache_key = f"assigned|{user_id}|{model_id}|{temp_val:.3f}"
    if cache_key in _llm_cache:
        return _llm_cache[cache_key]

    if model_id.startswith("mock-"):
        adapter = _MockLLM(model=model_id)
        _llm_cache[cache_key] = adapter
        return adapter

    try:
        from types import SimpleNamespace

        from app.api.routers.chat import _resolve_model

        api_model, base_url, api_key, provider_type = await _resolve_model(
            SimpleNamespace(model=model_id), user_id or "anonymous"
        )
    except Exception as e:
        logger.warning(f"[factory] get_llm_for_model resolve failed: {e}")
        return None

    if not api_model or not api_key:
        logger.warning(f"[factory] get_llm_for_model: no credential for model={model_id}")
        return None

    if provider_type == "anthropic":
        logger.warning(f"[factory] get_llm_for_model: anthropic chat 暂不支持, model={model_id}")
        return None

    try:
        from app.adapters.deepseek import DeepSeekAdapter

        adapter = DeepSeekAdapter(
            api_key=api_key,
            base_url=base_url,
            model=api_model,
            temperature=temp_val,
        )
    except Exception as e:
        logger.warning(f"[factory] get_llm_for_model build failed: {e}")
        return None

    _llm_cache[cache_key] = adapter
    return adapter


def _normalize_deepseek_model(name: str) -> str:
    """把前端展示名映射成 DeepSeek API 的 model id。

    修复（2026-10-21）：原实现把 "deepseek-v3" 硬编码映射到字面量
    "deepseek-chat"，导致 .env 的 DEEPSEEK_MODEL_V3 被绕过——agent
    编排层永远用 V3（无思考流），思考块前端永远拿不到数据。
    现改为从配置读取，.env 切模型即对 agent 层生效。"""
    from app.config import get_settings

    s = get_settings()
    mapping = {
        "deepseek-v3": s.deepseek_model_v3,
        "deepseek chat": s.deepseek_model_v3,
        "deepseek-v4-flash": "deepseek-v4-flash",
        "deepseek v4 flash": "deepseek-v4-flash",
        "deepseek-r1": s.deepseek_model_r1,
        "deepseek reasoner": s.deepseek_model_r1,
        "deepseek-reasoner": s.deepseek_model_r1,
    }
    return mapping.get(name.strip().lower(), name)


def _make_observer(workflow_id: str) -> Any:
    """构造把 trace 事件转 SSE 的 Observer。"""
    from app.services.sse_bus import sse_bus

    class _SSEObserver:
        """统一 emit + emit_* 适配方法（四期回归修复）。

        ReactorSession._emit_observer 按 emit_progress/emit_decision/
        emit_tool_call_start/emit_tool_call_end 方法名调用；loop 重构时
        细分方法被删只剩 emit，导致桥接静默断掉（事件全部丢失）。
        事件映射照抄旧 harness/observer/observer.py。
        """

        async def emit(self, node_id: str, event_type: str, payload: dict[str, Any]) -> None:
            await sse_bus.publish(workflow_id, event_type, {"node_id": node_id, **payload})

        async def emit_progress(
            self, node_id: str, current: int, total: int, label: str = ""
        ) -> None:
            await self.emit(
                node_id, "progress_update", {"current": current, "total": total, "label": label}
            )

        async def emit_decision(self, node_id: str, decision_text: str) -> None:
            await self.emit(node_id, "decision_made", {"decision": decision_text})

        async def emit_tool_call_start(
            self, node_id: str, tool_name: str, inputs: dict[str, Any] | None = None
        ) -> None:
            await self.emit(
                node_id, "tool_call_start", {"tool_name": tool_name, "inputs": inputs or {}}
            )

        async def emit_tool_call_end(
            self, node_id: str, tool_name: str, success: bool, summary: str = ""
        ) -> None:
            await self.emit(
                node_id,
                "tool_call_end",
                {"tool_name": tool_name, "success": success, "summary": summary},
            )

    return _SSEObserver()


def _make_chat_observer(session_id: str) -> Any:
    """构造以 session_id 为 SSE 频道的 Observer。"""
    from app.services.sse_bus import sse_bus

    class _SSEObserver:
        """同 _make_observer：统一 emit + emit_* 适配方法（四期回归修复）。"""

        async def emit(self, node_id: str, event_type: str, payload: dict[str, Any]) -> None:
            await sse_bus.publish(session_id, event_type, {"node_id": node_id, **payload})

        async def emit_progress(
            self, node_id: str, current: int, total: int, label: str = ""
        ) -> None:
            await self.emit(
                node_id, "progress_update", {"current": current, "total": total, "label": label}
            )

        async def emit_decision(self, node_id: str, decision_text: str) -> None:
            await self.emit(node_id, "decision_made", {"decision": decision_text})

        async def emit_tool_call_start(
            self, node_id: str, tool_name: str, inputs: dict[str, Any] | None = None
        ) -> None:
            await self.emit(
                node_id, "tool_call_start", {"tool_name": tool_name, "inputs": inputs or {}}
            )

        async def emit_tool_call_end(
            self, node_id: str, tool_name: str, success: bool, summary: str = ""
        ) -> None:
            await self.emit(
                node_id,
                "tool_call_end",
                {"tool_name": tool_name, "success": success, "summary": summary},
            )

    return _SSEObserver()
