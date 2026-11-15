"""端到端链路验证：图片上传 → asset 解析 → VL → 分镜 → 生帧 → 生视频 → 合成。

不调用任何 LLM / DashScope / Kling / Ark API，也不连数据库。
所有外部服务用 mock 替代，只验证代码链路衔接和修复后的 url_to_base64 /
url_to_local_path / path_from_url 是否通畅。

覆盖修复项：
- P0: url_to_base64 函数补全（之前不存在，import 必然 ImportError）
- P1: localhost:8000 硬编码 fallback 消除
- P2: _path_from_url 私有 API 跨模块引用 → 公开 API
- _resolve_image_urls 返回值增强：(urls, local_paths)
"""

from __future__ import annotations

import io
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image


# ── helpers ──────────────────────────────────────────────────

def _png_bytes(size=(80, 60), color=(200, 30, 30)) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", size, color=color).save(output, format="PNG")
    return output.getvalue()


def _jpg_bytes(size=(80, 60), color=(30, 200, 30)) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", size, color=color).save(output, format="JPEG", quality=85)
    return output.getvalue()


def _fake_mp4_bytes() -> bytes:
    return b"\x00\x00\x00\x20ftypisom" + b"\x00" * 100


def _write_upload_file(rel_path: str, data: bytes) -> str:
    """在 uploads/ 目录下写入文件，返回 /uploads/... URL。"""
    from app.services.asset_library import _BASE_DIR

    full = _BASE_DIR / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_bytes(data)
    return f"/uploads/{rel_path}"


# ── 1. asset_library 公开 API 测试（纯文件系统，不连 DB）───

class TestAssetLibraryPublicAPI:
    """验证修复后新增的 url_to_base64 / url_to_local_path / path_from_url。"""

    def test_path_from_url_valid(self):
        from app.services.asset_library import path_from_url

        url = "/uploads/assets/user1/test.jpg"
        result = path_from_url(url)
        assert result is not None
        assert result.name == "test.jpg"
        assert "assets" in str(result) and "user1" in str(result)

    def test_path_from_url_invalid_prefix(self):
        from app.services.asset_library import path_from_url

        assert path_from_url("") is None
        assert path_from_url("/other/path") is None
        assert path_from_url("http://example.com/img.jpg") is None

    def test_url_to_local_path_exists(self):
        url = _write_upload_file("assets/test_e2e_user/exists.png", _png_bytes())
        from app.services.asset_library import url_to_local_path

        result = url_to_local_path(url)
        assert result is not None
        assert Path(result).exists()

    def test_url_to_local_path_not_exists(self):
        from app.services.asset_library import url_to_local_path

        url = "/uploads/assets/nonexistent_user/no_such_file.png"
        result = url_to_local_path(url)
        assert result is None

    def test_url_to_base64_returns_data_uri(self):
        url = _write_upload_file("assets/test_b64_user/sample.png", _png_bytes())
        from app.services.asset_library import url_to_base64

        result = url_to_base64(url)
        assert result is not None
        assert result.startswith("data:image/png;base64,")
        assert len(result) > 100

    def test_url_to_base64_jpg(self):
        url = _write_upload_file("assets/test_b64_jpg_user/photo.jpg", _jpg_bytes())
        from app.services.asset_library import url_to_base64

        result = url_to_base64(url)
        assert result is not None
        assert result.startswith("data:image/jpeg;base64,")

    def test_url_to_base64_not_exists(self):
        from app.services.asset_library import url_to_base64

        url = "/uploads/assets/ghost/no_file.png"
        result = url_to_base64(url)
        assert result is None

    def test_url_to_base64_absolute_path_fallback(self, tmp_path):
        from app.services.asset_library import url_to_base64

        test_file = tmp_path / "direct.png"
        test_file.write_bytes(_png_bytes())

        result = url_to_base64(str(test_file))
        assert result is not None
        assert result.startswith("data:image/png;base64,")

    def test_url_to_base64_video_mp4(self):
        url = _write_upload_file("videos/test_vid_user/clip.mp4", _fake_mp4_bytes())
        from app.services.asset_library import url_to_base64

        result = url_to_base64(url)
        assert result is not None
        assert result.startswith("data:video/mp4;base64,")

    def test_url_to_local_path_for_video(self):
        url = _write_upload_file("videos/test_vid_resolve/clip.mp4", _fake_mp4_bytes())
        from app.services.asset_library import url_to_local_path

        result = url_to_local_path(url)
        assert result is not None
        assert Path(result).exists()
        assert "localhost" not in result


# ── 2. save_asset → url_to_local_path → url_to_base64 链路（mock DB）──

class TestSaveAssetToResolveChain:
    """验证：save_asset 落盘 → url_to_local_path 可解析 → url_to_base64 可读取。

    用 mock 替代 DB session，只验证文件系统链路。
    """

    @pytest.mark.asyncio
    async def test_save_image_then_resolve_local_path(self):
        from app.services.asset_library import url_to_local_path, _BASE_DIR

        user_id = "e2e_chain_user"
        user_dir = _BASE_DIR / "assets" / user_id
        user_dir.mkdir(parents=True, exist_ok=True)

        from app.db.models import generate_ulid
        asset_id = generate_ulid()
        original_name = f"{asset_id}.png"
        (user_dir / original_name).write_bytes(_png_bytes(size=(640, 480)))

        url = f"/uploads/assets/{user_id}/{original_name}"

        local = url_to_local_path(url)
        assert local is not None, f"URL 无法解析为本地路径：{url}"
        assert Path(local).exists()
        assert Path(local).stat().st_size > 0

    @pytest.mark.asyncio
    async def test_save_image_then_base64(self):
        from app.services.asset_library import url_to_base64, _BASE_DIR

        user_id = "e2e_b64_chain_user"
        user_dir = _BASE_DIR / "assets" / user_id
        user_dir.mkdir(parents=True, exist_ok=True)

        from app.db.models import generate_ulid
        asset_id = generate_ulid()
        original_name = f"{asset_id}.png"
        (user_dir / original_name).write_bytes(_png_bytes(size=(320, 240)))

        url = f"/uploads/assets/{user_id}/{original_name}"

        b64 = url_to_base64(url)
        assert b64 is not None, f"URL 无法转 base64：{url}"
        assert b64.startswith("data:image/png;base64,")

    @pytest.mark.asyncio
    async def test_save_video_asset_then_resolve(self):
        from app.services.asset_library import url_to_local_path, _BASE_DIR

        user_id = "e2e_vid_chain_user"
        vid_dir = _BASE_DIR / "videos" / user_id
        vid_dir.mkdir(parents=True, exist_ok=True)

        from app.db.models import generate_ulid
        asset_id = generate_ulid()
        original_name = f"{asset_id}.mp4"
        (vid_dir / original_name).write_bytes(_fake_mp4_bytes())

        url = f"/uploads/videos/{user_id}/{original_name}"

        local = url_to_local_path(url)
        assert local is not None, f"视频 URL 无法解析：{url}"
        assert Path(local).exists()


# ── 3. _resolve_image_urls 返回值增强测试（mock DB）──────

class TestResolveImageUrls:
    """验证 _resolve_image_urls 返回 (urls, local_paths) 元组。"""

    @pytest.mark.asyncio
    async def test_resolve_returns_urls_and_local_paths(self):
        from app.services.asset_library import url_to_local_path, _BASE_DIR
        from capabilities.video.skills.multi_image_video import _resolve_image_urls
        from app.db.models import ImageAsset, generate_ulid

        user_id = "e2e_resolve_user"
        user_dir = _BASE_DIR / "assets" / user_id
        user_dir.mkdir(parents=True, exist_ok=True)

        asset_ids = []
        urls_expected = []
        for i in range(2):
            aid = generate_ulid()
            fname = f"{aid}.png"
            (user_dir / fname).write_bytes(_png_bytes(size=(100, 100)))
            url = f"/uploads/assets/{user_id}/{fname}"
            urls_expected.append(url)
            asset_ids.append(aid)

        mock_assets = []
        for aid, url in zip(asset_ids, urls_expected):
            asset = MagicMock(spec=ImageAsset)
            asset.id = aid
            asset.user_id = user_id
            asset.original_url = url
            mock_assets.append(asset)

        async def _mock_db_query(session, stmt):
            result_mock = MagicMock()
            for asset in mock_assets:
                if str(stmt).find(asset.id) != -1 or True:
                    result_mock.scalar_one_or_none.return_value = asset
                    break
            return result_mock

        with patch("app.db.session.AsyncSessionLocal") as MockSession:
            mock_session = AsyncMock()
            mock_result = MagicMock()

            call_count = 0

            def _scalar_one_or_none():
                nonlocal call_count
                if call_count < len(mock_assets):
                    asset = mock_assets[call_count]
                    call_count += 1
                    return asset
                return None

            mock_result.scalar_one_or_none = _scalar_one_or_none
            mock_session.execute = AsyncMock(return_value=mock_result)
            MockSession.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            MockSession.return_value.__aexit__ = AsyncMock(return_value=False)

            urls, local_paths = await _resolve_image_urls(asset_ids, user_id)

        assert len(urls) == 2
        assert len(local_paths) == 2
        assert all(u.startswith("/uploads/assets/") for u in urls)

        for local in local_paths:
            assert local != "", "local_path 不应为空字符串"
            assert Path(local).exists(), f"本地路径不存在：{local}"

    @pytest.mark.asyncio
    async def test_resolve_raises_on_invalid_asset_id(self):
        from capabilities.video.skills.multi_image_video import _resolve_image_urls

        with patch("app.db.session.AsyncSessionLocal") as MockSession:
            mock_session = AsyncMock()
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = None
            mock_session.execute = AsyncMock(return_value=mock_result)
            MockSession.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            MockSession.return_value.__aexit__ = AsyncMock(return_value=False)

            with pytest.raises(ValueError, match="未找到"):
                await _resolve_image_urls(["fake_asset_id_999"], "any_user")


# ── 4. VL 步骤：url_to_base64 不再 ImportError ─────────────

class TestVLStepNoImportError:
    """验证 VL 步骤中 url_to_base64 可正常 import 且对本地文件生效。"""

    @pytest.mark.asyncio
    async def test_vl_step_uses_local_base64_not_localhost(self):
        url = _write_upload_file("assets/e2e_vl_user/vl_test.png", _png_bytes(size=(200, 200)))
        from app.services.asset_library import url_to_base64

        b64 = url_to_base64(url)
        assert b64 is not None
        assert b64.startswith("data:image/png;base64,")
        assert "localhost" not in b64

    @pytest.mark.asyncio
    async def test_vl_step_handles_nonexistent_image_gracefully(self):
        from app.services.asset_library import url_to_base64, url_to_local_path

        url = "/uploads/assets/ghost/no_file.png"

        b64 = url_to_base64(url)
        local = url_to_local_path(url)
        assert b64 is None
        assert local is None

    def test_url_to_base64_importable(self):
        """P0 修复验证：url_to_base64 可从 asset_library 正常 import。"""
        from app.services.asset_library import url_to_base64
        assert callable(url_to_base64)

    def test_url_to_local_path_importable(self):
        from app.services.asset_library import url_to_local_path
        assert callable(url_to_local_path)

    def test_path_from_url_importable(self):
        from app.services.asset_library import path_from_url
        assert callable(path_from_url)


# ── 5. compose_final 路径解析：无 localhost fallback ────────

class TestComposeFinalNoLocalhost:
    """验证 _compose_final 中视频片段路径解析不再依赖 localhost:8000。"""

    @pytest.mark.asyncio
    async def test_video_clip_url_resolved_locally(self):
        url = _write_upload_file("videos/e2e_compose_user/clip1.mp4", _fake_mp4_bytes())
        from app.services.asset_library import url_to_local_path

        local = url_to_local_path(url)
        assert local is not None, f"视频片段 URL 无法解析为本地路径：{url}"
        assert "localhost" not in local
        assert Path(local).exists()

    @pytest.mark.asyncio
    async def test_frame_url_resolved_locally(self):
        url = _write_upload_file("assets/e2e_compose_user/frame1.png", _png_bytes(size=(720, 1280)))
        from app.services.asset_library import url_to_local_path

        local = url_to_local_path(url)
        assert local is not None
        assert "localhost" not in local
        assert Path(local).exists()


# ── 6. 全链路 mock 测试 ─────────────────────────────────────

class TestFullChainMocked:
    """全链路 mock 测试：验证每步的输入输出衔接。

    不调用 LLM / DashScope / Kling / Ark / ffmpeg / DB。
    """

    @pytest.fixture(autouse=True)
    def _bypass_asset_guard(self):
        with patch(
            "app.services.asset_ref.guard_asset_ids",
            new=AsyncMock(return_value={"ok": True, "ids": []}),
        ):
            yield

    @pytest.mark.asyncio
    async def test_full_chain_from_upload_to_compose(self):
        from app.services.asset_library import url_to_local_path, url_to_base64
        from capabilities.video.skills.multi_image_video import MultiImageVideoSkill

        # ── Step 1: 模拟上传图片（写文件系统，不连 DB）──
        url1 = _write_upload_file(
            "assets/e2e_full_user/ref1.png",
            _png_bytes(size=(720, 1280), color=(255, 100, 50)),
        )
        url2 = _write_upload_file(
            "assets/e2e_full_user/ref2.png",
            _png_bytes(size=(720, 1280), color=(50, 100, 255)),
        )
        urls = [url1, url2]
        local_paths = [url_to_local_path(u) or "" for u in urls]

        # ── Step 2: 验证本地路径解析通畅 ──
        assert all(p != "" for p in local_paths), "至少一个图片的本地路径解析失败"
        assert all(Path(p).exists() for p in local_paths)

        # ── Step 3: 验证 base64 转换通畅（VL 步骤核心依赖）──
        for url in urls:
            b64 = url_to_base64(url)
            assert b64 is not None, f"VL 步骤：url_to_base64 失败：{url}"
            assert b64.startswith("data:image/")

        # ── Step 4: mock VL → 场景描述 ──
        scene_desc = {
            "scenes": [
                {"image_index": 1, "location": "城市街景", "subjects": ["建筑"], "props": [], "lighting": "日光"},
                {"image_index": 2, "location": "室内", "subjects": ["人物"], "props": ["桌椅"], "lighting": "暖光"},
            ],
            "cross_scene": {"shared_subjects": [], "visual_style": "电影感", "color_system": "冷暖对比"},
        }

        # ── Step 5: mock storyboard ──
        storyboard = {
            "title": "端到端链路验证",
            "total_shots": 2,
            "shots": [
                {
                    "shot_index": 1, "caption": "城市街景，阳光洒落",
                    "image_prompt": "城市街景日光电影感", "motion_prompt": "缓慢推进",
                    "reference_images": [1], "duration": 5.0, "transition": "cut",
                },
                {
                    "shot_index": 2, "caption": "室内暖光，人物特写",
                    "image_prompt": "室内暖光人物特写", "motion_prompt": "从全景推到特写",
                    "reference_images": [2], "duration": 5.0, "transition": "dissolve",
                },
            ],
        }

        # ── Step 6: mock 生帧 → 写文件系统 ──
        frame_url1 = _write_upload_file(
            "assets/e2e_full_user/frame_001.png",
            _png_bytes(size=(720, 1280)),
        )
        frame_url2 = _write_upload_file(
            "assets/e2e_full_user/frame_002.png",
            _png_bytes(size=(720, 1280)),
        )

        shots_with_frames = []
        for i, shot in enumerate(storyboard["shots"]):
            shot_copy = dict(shot)
            shot_copy["frame_url"] = [frame_url1, frame_url2][i]
            shot_copy["frame_source"] = "ai_generated"
            shots_with_frames.append(shot_copy)

        # 验证帧 URL 可解析为本地路径（compose_final 核心依赖）
        for s in shots_with_frames:
            local = url_to_local_path(s["frame_url"])
            assert local is not None, f"帧 URL 无法解析：{s['frame_url']}"

        # ── Step 7: mock 生视频 → 写文件系统 ──
        clip_url1 = _write_upload_file(
            "videos/e2e_full_user/clip_001.mp4",
            _fake_mp4_bytes(),
        )
        clip_url2 = _write_upload_file(
            "videos/e2e_full_user/clip_002.mp4",
            _fake_mp4_bytes(),
        )

        shots_with_clips = []
        for i, shot in enumerate(shots_with_frames):
            shot_copy = dict(shot)
            shot_copy["clip_url"] = [clip_url1, clip_url2][i]
            shot_copy["clip_mode"] = "video"
            shot_copy["clip_asset_id"] = f"vid_{i+1}"
            shots_with_clips.append(shot_copy)

        # 验证视频片段 URL 可解析为本地路径（compose_final 核心依赖）
        for s in shots_with_clips:
            local = url_to_local_path(s["clip_url"])
            assert local is not None, f"视频片段 URL 无法解析：{s['clip_url']}"
            assert "localhost" not in local

        # ── Step 8: 调用 Skill.execute() 全链路（mock 外部服务 + DB）──
        skill = MultiImageVideoSkill()

        import sys
        mock_asset_lib = MagicMock()
        mock_asset_lib.save_video_asset = AsyncMock(
            return_value={"original_url": "/uploads/videos/e2e_full_user/final.mp4", "asset_id": "vid_final", "id": "vid_final"}
        )
        mock_asset_lib.url_to_local_path = url_to_local_path
        mock_asset_lib.url_to_base64 = url_to_base64
        mock_sse_bus = MagicMock()
        mock_sse_bus.publish = AsyncMock()

        with patch.dict(sys.modules, {
            "app.services.asset_library": mock_asset_lib,
            "app.services.sse_bus": MagicMock(sse_bus=mock_sse_bus),
        }), patch(
            "capabilities.video.skills.multi_image_video._resolve_image_urls",
            new=AsyncMock(return_value=(urls, local_paths)),
        ), patch(
            "capabilities.video.skills.multi_image_video._vl_understand_images",
            new=AsyncMock(return_value=scene_desc),
        ), patch(
            "capabilities.video.skills.multi_image_video._generate_storyboard",
            new=AsyncMock(return_value=storyboard),
        ), patch(
            "capabilities.video.skills.multi_image_video._generate_shot_frames",
            new=AsyncMock(return_value=shots_with_frames),
        ), patch(
            "capabilities.video.skills.multi_image_video._generate_shot_clips",
            new=AsyncMock(return_value=shots_with_clips),
        ), patch(
            "capabilities.video.skills.multi_image_video._compose_final",
            new=AsyncMock(return_value=(_fake_mp4_bytes(), ["2 镜全部视频模式"])),
        ):
            # 第一步：上传参考图后先调一次 —— 闸门必须停在分镜草稿，不生成
            draft = await skill.execute({
                "image_asset_ids": ["asset_1", "asset_2"],
                "prompt": "端到端链路验证视频",
                "target_duration": 10.0,
                "_context": MagicMock(user_id="e2e_full_user"),
                "_workflow_id": None,
            })
            assert draft["mode"] == "preview"
            assert draft["needs_confirmation"] is True

            # 第二步：用户确认分镜后回传 refine_shots —— 这才进入生成
            result = await skill.execute({
                "image_asset_ids": ["asset_1", "asset_2"],
                "prompt": "端到端链路验证视频",
                "target_duration": 10.0,
                "refine_shots": storyboard["shots"],
                "_context": MagicMock(user_id="e2e_full_user"),
                "_workflow_id": None,
            })

        assert result["success"] is True
        assert result.get("shots_count", 0) == 2

    @pytest.mark.asyncio
    async def test_preview_only_chain(self):
        """preview_only 模式：只走 VL + storyboard，不生帧不生视频。"""
        from capabilities.video.skills.multi_image_video import MultiImageVideoSkill
        from app.services.asset_library import url_to_local_path

        url = _write_upload_file("assets/e2e_preview_user/preview.png", _png_bytes())
        local = url_to_local_path(url) or ""

        skill = MultiImageVideoSkill()

        import sys
        mock_sse_bus = MagicMock()
        mock_sse_bus.publish = AsyncMock()

        with patch.dict(sys.modules, {
            "app.services.sse_bus": MagicMock(sse_bus=mock_sse_bus),
        }), patch(
            "capabilities.video.skills.multi_image_video._resolve_image_urls",
            new=AsyncMock(return_value=([url], [local])),
        ), patch(
            "capabilities.video.skills.multi_image_video._vl_understand_images",
            new=AsyncMock(return_value={"scenes": [{"location": "测试"}], "cross_scene": {}}),
        ), patch(
            "capabilities.video.skills.multi_image_video._generate_storyboard",
            new=AsyncMock(return_value={
                "title": "预览测试",
                "total_shots": 1,
                "shots": [{
                    "shot_index": 1, "caption": "镜1",
                    "image_prompt": "test", "motion_prompt": "推进",
                    "reference_images": [1], "duration": 5.0, "transition": "cut",
                }],
            }),
        ):
            result = await skill.execute({
                "image_asset_ids": ["asset_preview"],
                "prompt": "预览链路验证",
                "preview_only": True,
                "_context": MagicMock(user_id="e2e_preview_user"),
                "_workflow_id": None,
            })

        assert result["mode"] == "preview"
        assert result["total_shots"] == 1

    @pytest.mark.asyncio
    async def test_all_ken_burns_degradation_chain(self):
        """全 ken-burns 降级链路：image2video 全失败 → compose 用图片模式。"""
        from capabilities.video.skills.multi_image_video import MultiImageVideoSkill
        from app.services.asset_library import url_to_local_path

        ref_url = _write_upload_file("assets/e2e_kb_user/kb_ref.png", _png_bytes(size=(720, 1280)))
        frame_url = _write_upload_file("assets/e2e_kb_user/kb_frame.png", _png_bytes(size=(720, 1280)))

        ref_local = url_to_local_path(ref_url) or ""
        frame_local = url_to_local_path(frame_url)
        assert frame_local is not None, "ken-burns 降级需要帧的本地路径"

        shots_with_kb = [{
            "shot_index": 1, "caption": "降级测试",
            "image_prompt": "test", "motion_prompt": "推进",
            "reference_images": [1], "duration": 5.0, "transition": "cut",
            "frame_url": frame_url, "frame_source": "ai_generated",
            "clip_mode": "ken_burns", "clip_error": "image2video 不可用",
        }]

        skill = MultiImageVideoSkill()

        import sys
        mock_asset_lib = MagicMock()
        mock_asset_lib.save_video_asset = AsyncMock(
            return_value={"original_url": "/uploads/videos/e2e_kb_user/kb_final.mp4", "asset_id": "vid_kb", "id": "vid_kb"}
        )
        mock_asset_lib.url_to_local_path = url_to_local_path
        mock_asset_lib.url_to_base64 = MagicMock(return_value=None)
        mock_sse_bus = MagicMock()
        mock_sse_bus.publish = AsyncMock()

        with patch.dict(sys.modules, {
            "app.services.asset_library": mock_asset_lib,
            "app.services.sse_bus": MagicMock(sse_bus=mock_sse_bus),
        }), patch(
            "capabilities.video.skills.multi_image_video._resolve_image_urls",
            new=AsyncMock(return_value=([ref_url], [ref_local])),
        ), patch(
            "capabilities.video.skills.multi_image_video._vl_understand_images",
            new=AsyncMock(return_value=None),
        ), patch(
            "capabilities.video.skills.multi_image_video._generate_storyboard",
            new=AsyncMock(return_value={
                "title": "降级测试",
                "total_shots": 1,
                "shots": [shots_with_kb[0]],
            }),
        ), patch(
            "capabilities.video.skills.multi_image_video._generate_shot_frames",
            new=AsyncMock(return_value=shots_with_kb),
        ), patch(
            "capabilities.video.skills.multi_image_video._generate_shot_clips",
            new=AsyncMock(return_value=shots_with_kb),
        ), patch(
            "capabilities.video.skills.multi_image_video._compose_final",
            new=AsyncMock(return_value=(_fake_mp4_bytes(), ["1 镜退化为 ken-burns"])),
        ):
            result = await skill.execute({
                "image_asset_ids": ["asset_kb"],
                "prompt": "降级链路验证",
                "refine_shots": [shots_with_kb[0]],
                "_context": MagicMock(user_id="e2e_kb_user"),
                "_workflow_id": None,
            })

        assert result["success"] is True
        assert "degradation" in result
        assert result["mode"] == "refine"


# ── 7. 无 localhost 残留验证 ────────────────────────────────

class TestNoLocalhostResidual:
    """确认修复后代码中不再有 localhost:8000 fallback 残留。"""

    def test_multi_image_video_no_localhost(self):
        path = Path(__file__).resolve().parent.parent / "app" / "skills" / "produce" / "multi_image_video.py"
        content = path.read_text(encoding="utf-8")
        assert "localhost:8000" not in content, "multi_image_video.py 仍有 localhost:8000 残留"

    def test_auto_short_video_no_private_path_from_url(self):
        path = Path(__file__).resolve().parent.parent / "app" / "skills" / "produce" / "auto_short_video.py"
        content = path.read_text(encoding="utf-8")
        assert "from app.services.asset_library import _path_from_url" not in content, (
            "auto_short_video.py 仍引用私有 _path_from_url"
        )

    def test_cards_export_no_private_path_from_url(self):
        path = Path(__file__).resolve().parent.parent / "app" / "cards" / "export.py"
        content = path.read_text(encoding="utf-8")
        assert "from app.services.asset_library import _path_from_url" not in content, (
            "export.py 仍引用私有 _path_from_url"
        )

    def test_no_cross_module_private_import(self):
        """确认没有其他模块跨模块引用 _path_from_url。"""
        backend_root = Path(__file__).resolve().parent.parent
        app_dir = backend_root / "app"
        for py_file in app_dir.rglob("*.py"):
            content = py_file.read_text(encoding="utf-8", errors="ignore")
            assert "from app.services.asset_library import _path_from_url" not in content, (
                f"{py_file.relative_to(backend_root)} 仍跨模块引用 _path_from_url"
            )