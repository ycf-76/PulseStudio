import asyncio

from app.engine.schemas import LLMProtocol
from app.adapters.model_router import ChatResponse, ModelRouter


def _router_with_stubbed_route():
    """⚠️ 硬规矩：测试绝不真调 LLM API。

    stream_chat 现实现走 resolve_llm(deepseek_model_v3) 拿真实 adapter
    （旧版走 self.route，stub route 已拦不住）——必须同时 stub resolve_llm，
    否则测试会真发 DeepSeek 请求产生真实费用（2026-10-20 踩过一次）。
    """
    router = ModelRouter()

    class _FakeAdapter:
        model_name = "fake-model"

        async def chat(self, messages, response_format=None, **kwargs):
            return {"content": "hello", "token_usage": 10, "reasoning_content": None}

        async def stream_chat(self, messages, response_format=None, tools=None, **kwargs):
            yield {
                "content": "hello",
                "reasoning_content": None,
                "is_final": True,
                "token_usage": 10,
            }

    async def fake_route(request):
        return ChatResponse.ok(
            content="hello",
            tokens_used=10,
            model_used="fake-model",
            provider_used=None,
        )

    router.route = fake_route
    router.resolve_llm = lambda model=None, **kwargs: _FakeAdapter()
    return router


def test_model_router_conforms_to_llm_protocol():
    assert isinstance(ModelRouter(), LLMProtocol)


def test_model_router_exposes_model_name():
    router = ModelRouter()
    assert hasattr(router, "model_name")
    assert router.model_name == "model-router"


def test_model_router_chat_returns_protocol_shape():
    async def run():
        router = _router_with_stubbed_route()
        result = await router.chat([{"role": "user", "content": "hi"}])
        assert result["content"] == "hello"
        assert result["reasoning_content"] is None
        assert result["token_usage"] == 10

    asyncio.run(run())


def test_model_router_stream_chat_yields_protocol_chunk():
    async def run():
        router = _router_with_stubbed_route()
        chunks = []
        async for chunk in router.stream_chat([{"role": "user", "content": "hi"}]):
            chunks.append(chunk)
        assert len(chunks) == 1
        assert chunks[0]["content"] == "hello"
        assert chunks[0]["is_final"] is True
        assert chunks[0]["token_usage"] == 10

    asyncio.run(run())