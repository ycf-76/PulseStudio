"""vl_analyze asset_id 通道 + VLAnalyzeContextHook 兜底注入测试。

覆盖「LLM 看不见配图」修复的两条腿：
1. 技能侧：asset_id → 查库 → base64 → Qwen-VL（不依赖 file_ops 白名单）
2. 框架侧：钩子在 LLM 忘传/编造 asset_id 时注入真实 id

文档依据：本次需求（路线1兜底 + 视觉模型做视觉分析）。
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.agents.dispatch_hooks import VLAnalyzeContextHook
from app.engine.protocol.dispatch_hooks import DispatchContext
from app.skills.analyze import vl_analyze as vl_mod
from app.skills.analyze.vl_analyze import VLAnalyzeInput, VLAnalyzeSkill


class _FakeVL:
    """最小 VL 适配器替身：记录 messages，返回固定 JSON。"""

    def __init__(
        self,
        reply: str = '{"tags": ["猫"], "mood": "温暖", "composition": "居中", "color_palette": ["白"]}',
    ) -> None:
        self.reply = reply
        self.calls: list[list[dict]] = []

    async def chat(self, messages, **kwargs):
        self.calls.append(messages)
        return {"content": self.reply}


@pytest.fixture(autouse=True)
def _clear_cache():
    vl_mod._ANALYSIS_CACHE.clear()
    yield
    vl_mod._ANALYSIS_CACHE.clear()


# ── 1. 入参 schema ──────────────────────────────────────────


def test_input_accepts_asset_id():
    """场景: 只传 asset_id（最省事的通道）
    预期: 校验通过，其余图片通道留空
    """
    inp = VLAnalyzeInput(asset_id="asset_001")
    assert inp.asset_id == "asset_001"
    assert inp.image_base64 == ""
    assert inp.image_path == ""


# ── 2. asset_id → base64 ────────────────────────────────────


@pytest.mark.asyncio
async def test_load_asset_as_base64_uses_library():
    """场景: asset_id 存在且属于当前用户
    预期: 通过 original_url 转出 data URI
    """
    inputs = {"_context": {"user_id": "u1"}}
    with (
        patch(
            "app.services.asset_library.get_asset",
            new=AsyncMock(return_value={"original_url": "/uploads/assets/u1/a.png"}),
        ),
        patch(
            "app.services.asset_library.url_to_base64",
            return_value="data:image/png;base64,AAAA",
        ),
    ):
        uri = await vl_mod._load_asset_as_base64("a1", inputs)

    assert uri == "data:image/png;base64,AAAA"


@pytest.mark.asyncio
async def test_load_asset_as_base64_missing_user_context():
    """场景: _context 缺失 user_id
    预期: 明确报错（不静默降级）
    """
    with pytest.raises(ValueError, match="用户上下文"):
        await vl_mod._load_asset_as_base64("a1", {})


@pytest.mark.asyncio
async def test_load_asset_as_base64_not_found():
    """场景: asset_id 不存在或不属于该用户
    预期: 抛 ValueError
    """
    with patch(
        "app.services.asset_library.get_asset",
        new=AsyncMock(return_value=None),
    ):
        with pytest.raises(ValueError, match="asset not found"):
            await vl_mod._load_asset_as_base64("ghost", {"_context": {"user_id": "u1"}})


# ── 3. execute 主链路 ──────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_with_asset_id_sends_image_block():
    """场景: 传 asset_id，库能解析
    预期: 发给 VL 的 messages 里出现 {"image": data-uri}
    """
    fake = _FakeVL()
    with (
        patch(
            "app.services.asset_library.get_asset",
            new=AsyncMock(return_value={"original_url": "/uploads/assets/u1/a.png"}),
        ),
        patch(
            "app.services.asset_library.url_to_base64",
            return_value="data:image/png;base64,AAAA",
        ),
    ):
        result = await VLAnalyzeSkill(llm=fake).execute(
            {"asset_id": "a1", "_context": {"user_id": "u1"}}
        )

    assert result["ok"] is True
    assert result["tags"] == ["猫"]
    block = fake.calls[0][0]["content"]
    assert any(item.get("image") == "data:image/png;base64,AAAA" for item in block)


@pytest.mark.asyncio
async def test_execute_asset_id_result_cached():
    """场景: 同一 asset_id + prompt 连续调用两次
    预期: 第二次命中缓存，不再调用 VL（省钱）
    """
    fake = _FakeVL()
    with (
        patch(
            "app.services.asset_library.get_asset",
            new=AsyncMock(return_value={"original_url": "/uploads/assets/u1/a.png"}),
        ),
        patch(
            "app.services.asset_library.url_to_base64",
            return_value="data:image/png;base64,AAAA",
        ),
    ):
        skill = VLAnalyzeSkill(llm=fake)
        await skill.execute({"asset_id": "a1", "_context": {"user_id": "u1"}})
        second = await skill.execute({"asset_id": "a1", "_context": {"user_id": "u1"}})

    assert second["tags"] == ["猫"]
    assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_execute_without_any_image_source_raises():
    """场景: 三个图片通道全空
    预期: ValueError（不许静默返回空结果）
    """
    with pytest.raises(ValueError, match="至少需要一个"):
        await VLAnalyzeSkill(llm=_FakeVL()).execute({})


# ── 4. 钩子兜底 ────────────────────────────────────────────


def _ctx(asset_ids):
    return DispatchContext(
        run_context={"image_asset_ids": asset_ids, "user_id": "u1"},
        workflow_id="wf",
        turn_id="t1",
    )


@pytest.mark.asyncio
async def test_hook_injects_missing_asset_id():
    """场景: LLM 调 vl_analyze 忘传 asset_id
    预期: 钩子补第一张真实图片
    """
    name, args = await VLAnalyzeContextHook().pre_execute(
        "skill_call",
        {"skill": "vl_analyze", "arguments": {}},
        _ctx(["asset_a", "asset_b"]),
    )
    assert name == "skill_call"
    assert args["arguments"]["asset_id"] == "asset_a"


@pytest.mark.asyncio
async def test_hook_replaces_hallucinated_asset_id():
    """场景: LLM 编了一个不存在的 asset_id
    预期: 替换为真实 id
    """
    _, args = await VLAnalyzeContextHook().pre_execute(
        "skill_call",
        {"skill": "vl_analyze", "arguments": {"asset_id": "img_1"}},
        _ctx(["asset_a"]),
    )
    assert args["arguments"]["asset_id"] == "asset_a"


@pytest.mark.asyncio
async def test_hook_keeps_real_asset_id():
    """场景: LLM 传了真实 id（可能是第二张）
    预期: 原样保留，不强行改成第一张
    """
    _, args = await VLAnalyzeContextHook().pre_execute(
        "skill_call",
        {"skill": "vl_analyze", "arguments": {"asset_id": "asset_b"}},
        _ctx(["asset_a", "asset_b"]),
    )
    assert args["arguments"]["asset_id"] == "asset_b"


@pytest.mark.asyncio
async def test_hook_ignores_explicit_path_channel():
    """场景: LLM 显式传了 image_path
    预期: 不注入 asset_id（另一条通道自带图源）
    """
    _, args = await VLAnalyzeContextHook().pre_execute(
        "skill_call",
        {"skill": "vl_analyze", "arguments": {"image_path": "outputs/x.png"}},
        _ctx(["asset_a"]),
    )
    assert "asset_id" not in args["arguments"]


@pytest.mark.asyncio
async def test_hook_ignores_explicit_url_channel():
    """场景: LLM 用作品配图通道传了 image_url（本轮聊天也附了图）
    预期: 不注入 asset_id（image_url 优先级更高，注入了也是死代码）
    """
    _, args = await VLAnalyzeContextHook().pre_execute(
        "skill_call",
        {"skill": "vl_analyze", "arguments": {"image_url": "/uploads/topic_images/a.jpg"}},
        _ctx(["asset_a"]),
    )
    assert "asset_id" not in args["arguments"]
    assert args["arguments"]["image_url"] == "/uploads/topic_images/a.jpg"


@pytest.mark.asyncio
async def test_hook_ignores_other_skills():
    """场景: 调的是别的技能
    预期: 参数原样不动
    """
    _, args = await VLAnalyzeContextHook().pre_execute(
        "skill_call",
        {"skill": "compose_card_deck", "arguments": {"topic": "x"}},
        _ctx(["asset_a"]),
    )
    assert args["arguments"] == {"topic": "x"}


@pytest.mark.asyncio
async def test_hook_noop_without_uploaded_images():
    """场景: 本轮用户没附图
    预期: 什么都不注入
    """
    _, args = await VLAnalyzeContextHook().pre_execute(
        "skill_call",
        {"skill": "vl_analyze", "arguments": {}},
        _ctx([]),
    )
    assert args["arguments"] == {}


# ── 5. image_url 通道（作品配图） ───────────────────────────


@pytest.mark.asyncio
async def test_execute_with_http_url_passes_url_directly():
    """场景: 作品配图是远端 CDN 链接
    预期: 原样交给 VL（DashScope 支持 URL 直传，不必下载转 base64）
    """
    fake = _FakeVL()
    result = await VLAnalyzeSkill(llm=fake).execute({"image_url": "https://cdn.example.com/a.png"})

    assert result["ok"] is True
    block = fake.calls[0][0]["content"]
    assert any(item.get("image") == "https://cdn.example.com/a.png" for item in block)


@pytest.mark.asyncio
async def test_execute_with_outputs_path_reads_local_file(tmp_path):
    """场景: 作品配图是后端 /outputs 路径（VL 访问不到）
    预期: 读本地文件转 data URI 再送 VL
    """
    img = tmp_path / "a.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    fake = _FakeVL()
    with patch.object(vl_mod, "_resolve_local_image", return_value=img):
        result = await VLAnalyzeSkill(llm=fake).execute({"image_url": "/outputs/a.png"})

    assert result["ok"] is True
    block = fake.calls[0][0]["content"]
    assert block[1]["image"].startswith("data:image/png;base64,")


@pytest.mark.asyncio
async def test_execute_missing_local_file_raises(tmp_path):
    """场景: /outputs 路径指向的文件不存在
    预期: ValueError（让 LLM 如实说「这张没读到」，不能静默空返回）
    """
    missing = tmp_path / "gone.png"
    with patch.object(vl_mod, "_resolve_local_image", return_value=missing):
        with pytest.raises(ValueError, match="image not found"):
            await VLAnalyzeSkill(llm=_FakeVL()).execute({"image_url": "/outputs/gone.png"})


@pytest.mark.asyncio
async def test_execute_url_result_cached():
    """场景: 同一作品配图 + 同一 prompt 连续两次
    预期: 第二次命中缓存（详情页多轮追问不该重复计费）
    """
    fake = _FakeVL()
    skill = VLAnalyzeSkill(llm=fake)
    await skill.execute({"image_url": "https://cdn.example.com/a.png"})
    second = await skill.execute({"image_url": "https://cdn.example.com/a.png"})

    assert second["tags"] == ["猫"]
    assert len(fake.calls) == 1


# ── 6. 本地静态路径映射 ─────────────────────────────────────


def test_data_uri_mime_follows_extension(tmp_path):
    """场景: 采集作品缓存图是 webp
    预期: data URI 前缀写 image/webp（统一写 png 会让 VL 解码走错分支）
    """
    assert vl_mod._to_data_uri("AAAA", tmp_path / "a.webp").startswith("data:image/webp;base64,")
    assert vl_mod._to_data_uri("AAAA", tmp_path / "a.jpg").startswith("data:image/jpeg;base64,")
    assert vl_mod._to_data_uri("AAAA").startswith("data:image/png;base64,")


def test_resolve_uploads_prefix(tmp_path, monkeypatch):
    """场景: 作品配图是 /uploads/topic_images/a.webp（image_store 缓存产物）
    预期: 映射到 uploads 根下的真实文件，而不是被沙箱拒掉
    """
    import app.services.image_store as image_store

    root = tmp_path / "uploads"
    (root / "topic_images").mkdir(parents=True)
    monkeypatch.setattr(image_store, "uploads_root", lambda: root)

    target = vl_mod._resolve_local_image("/uploads/topic_images/a.webp")
    assert target == (root / "topic_images" / "a.webp").resolve()


def test_resolve_rejects_path_escape():
    """场景: LLM 传 /outputs/../../.env 试图越界读文件
    预期: 直接拒绝（静态前缀映射不能变成任意文件读后门）
    """
    with pytest.raises(ValueError, match="invalid image path"):
        vl_mod._resolve_local_image("/outputs/../../.env")
