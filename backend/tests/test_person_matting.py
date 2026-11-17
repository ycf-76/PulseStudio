"""10.20 人物抠像（RVM matting）测试 — TDD 先行。

覆盖：
  - adapter matte_person：正常产出帧序列 / 模型加载失败 / torch 未安装 /
    空帧 / 推理异常 / 缓存命中不推理 / RGBA 直通 alpha 写入
  - skill _execute_pip matting 分支：alpha 帧序列融合命令 /
    matting 失败回退矩形 PIP / deck_images 幻灯片主画面
"""

from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from capabilities.video.adapters.person_matting import MatteResult, matte_person

# ---------------------------------------------------------------------------
# adapter: matte_person
# ---------------------------------------------------------------------------


def _fake_frame(w=64, h=128):
    return np.full((h, w, 3), 128, dtype=np.uint8)


class TestMattePerson:
    @pytest.mark.asyncio
    async def test_normal_path_produces_frames(self, tmp_path):
        """正常路径：同步管线落盘帧序列 → MatteResult 元信息 + meta.json。"""
        frame_dir = tmp_path / "matte" / "frames"

        def _fake_sync(video_path, model, fd):
            from capabilities.video.adapters.person_matting import _write_frame

            assert fd == frame_dir
            fgr = np.full((128, 64, 3), 255, dtype=np.uint8)
            pha = np.full((128, 64), 0.5, dtype=np.float32)
            _write_frame(fgr, pha, fd / "000000.png")
            _write_frame(fgr, pha, fd / "000001.png")
            return (2, 64, 128)

        with (
            patch(
                "capabilities.video.adapters.person_matting._load_model",
                return_value=MagicMock(),
            ),
            patch(
                "capabilities.video.adapters.person_matting._probe_fps",
                return_value=30.0,
            ),
            patch(
                "capabilities.video.adapters.person_matting._matte_sync",
                side_effect=_fake_sync,
            ),
        ):
            result = await matte_person("fake.mp4", tmp_path / "matte")

        assert result is not None
        assert isinstance(result, MatteResult)
        assert result.count == 2 and result.fps == 30.0
        assert result.width == 64 and result.height == 128
        # 帧文件按 %06d.png 命名，可被 image2 demuxer 消费
        pngs = sorted(frame_dir.glob("*.png"))
        assert [p.name for p in pngs] == ["000000.png", "000001.png"]
        # meta.json 已落（缓存复用依据）
        assert (tmp_path / "matte" / "meta.json").is_file()

    @pytest.mark.asyncio
    async def test_load_model_failure_degrades_to_none(self, tmp_path):
        """模型加载失败（权重缺失/无 GPU 抛异常 或 返回 None）→ None 不抛。"""
        with patch(
            "capabilities.video.adapters.person_matting._load_model",
            return_value=None,
        ):
            result = await matte_person("fake.mp4", tmp_path / "matte")
        assert result is None

        with patch(
            "capabilities.video.adapters.person_matting._load_model",
            side_effect=RuntimeError("CUDA unavailable"),
        ):
            result = await matte_person("fake.mp4", tmp_path / "matte")
        assert result is None

    @pytest.mark.asyncio
    async def test_torch_import_error_degrades_to_none(self, tmp_path):
        """torch 未安装（ImportError）→ None 不抛。"""
        with patch(
            "capabilities.video.adapters.person_matting._load_model",
            side_effect=ImportError("No module named 'torch'"),
        ):
            result = await matte_person("fake.mp4", tmp_path / "matte")
        assert result is None

    @pytest.mark.asyncio
    async def test_zero_frames_degrades_to_none(self, tmp_path):
        """视频 0 帧 → None。"""
        with (
            patch(
                "capabilities.video.adapters.person_matting._load_model",
                return_value=MagicMock(),
            ),
            patch(
                "capabilities.video.adapters.person_matting._probe_fps",
                return_value=30.0,
            ),
            patch(
                "capabilities.video.adapters.person_matting._matte_sync",
                return_value=(0, 0, 0),
            ),
        ):
            result = await matte_person("fake.mp4", tmp_path / "matte")
        assert result is None

    @pytest.mark.asyncio
    async def test_infer_exception_degrades_to_none(self, tmp_path):
        """推理中途异常（CUDA OOM 等）→ None，绝不阻断主管线。"""
        with (
            patch(
                "capabilities.video.adapters.person_matting._load_model",
                return_value=MagicMock(),
            ),
            patch(
                "capabilities.video.adapters.person_matting._probe_fps",
                return_value=30.0,
            ),
            patch(
                "capabilities.video.adapters.person_matting._matte_sync",
                side_effect=RuntimeError("CUDA OOM"),
            ),
        ):
            result = await matte_person("fake.mp4", tmp_path / "matte")
        assert result is None

    @pytest.mark.asyncio
    async def test_cache_hit_skips_inference(self, tmp_path):
        """缓存命中（有效 meta + 帧目录存在）→ 不加载模型不推理直接返回。"""
        cache_dir = tmp_path / "matte"
        frame_dir = cache_dir / "frames"
        frame_dir.mkdir(parents=True)
        for i in range(2):
            (frame_dir / f"{i:06d}.png").write_bytes(b"png")
        (cache_dir / "meta.json").write_text(
            '{"count": 2, "fps": 30.0, "width": 64, "height": 128}',
            encoding="utf-8",
        )

        with patch(
            "capabilities.video.adapters.person_matting._load_model",
            side_effect=AssertionError("缓存命中不应加载模型"),
        ):
            result = await matte_person("fake.mp4", cache_dir)

        assert result is not None
        assert result.count == 2 and result.fps == 30.0
        assert result.frame_dir == frame_dir

    @pytest.mark.asyncio
    async def test_cache_invalid_meta_reruns(self, tmp_path):
        """meta.json 损坏/帧数不符 → 视为缓存未命中，走重抠。"""
        cache_dir = tmp_path / "matte"
        (cache_dir / "frames").mkdir(parents=True)
        (cache_dir / "meta.json").write_text("not json", encoding="utf-8")

        with (
            patch(
                "capabilities.video.adapters.person_matting._load_model",
                return_value=MagicMock(),
            ),
            patch(
                "capabilities.video.adapters.person_matting._probe_fps",
                return_value=30.0,
            ),
            patch(
                "capabilities.video.adapters.person_matting._matte_sync",
                return_value=(0, 0, 0),  # 重抠 0 帧 → None
            ),
        ):
            result = await matte_person("fake.mp4", cache_dir)
        assert result is None

    def test_rgba_write_straight_alpha(self, tmp_path):
        """RGBA 写入：fgr 直通 RGB 通道、pha×255 直通 A 通道（straight alpha，
        ffmpeg overlay 直接消费，不做预乘）。"""
        import cv2

        fgr = np.zeros((4, 4, 3), dtype=np.uint8)
        fgr[..., 2] = 200  # RGB 的 R 通道
        pha = np.full((4, 4), 0.5, dtype=np.float32)

        from capabilities.video.adapters.person_matting import _write_frame

        path = _write_frame(fgr, pha, tmp_path / "000000.png")

        # Windows 中文路径：cv2.imread 静默失败 → fromfile + imdecode
        import cv2

        data = np.fromfile(str(path), dtype=np.uint8)
        img = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
        assert img.shape == (4, 4, 4)
        assert (img[..., 0] == 200).all()  # cv2 BGR：B 位 = 原 R
        assert (img[..., 3] == 127).all()  # 0.5*255 → 127


# ---------------------------------------------------------------------------
# skill: _execute_pip matting 分支
# ---------------------------------------------------------------------------


def _matte_result():
    return MatteResult(
        frame_dir=Path("D:/fake/frames"),
        count=660,
        fps=30.0,
        width=1080,
        height=1920,
    )


class TestPipMattingBranch:
    def _patch_env(self, matte_return, captured_cmds, tmp_path):
        """公共 patch 组：分辨率/ffmpeg/抠像/临时目录 全部 mock。"""

        async def _fake_probe(path):
            return 1080, 1920

        async def _fake_ffmpeg(cmd):
            captured_cmds.append(cmd)

        @asynccontextmanager
        async def _fake_tmp():
            yield tmp_path / "work"

        return (
            patch(
                "capabilities.video.skills.talking_head_overlay."
                "TalkingHeadOverlaySkill._probe_resolution",
                new=AsyncMock(return_value=(1080, 1920)),
            ),
            patch(
                "capabilities.video.skills.smart_cut._run_ffmpeg",
                new=AsyncMock(side_effect=_fake_ffmpeg),
            ),
            patch(
                "capabilities.video.adapters.person_matting.matte_person",
                new=AsyncMock(return_value=matte_return),
            ),
            patch(
                "capabilities.video.skills.talking_head_overlay.managed_temp_dir",
                new=lambda: _fake_tmp(),
            ),
        )

    async def _run_skill(self, tmp_path, params):
        from capabilities.video.skills.talking_head_overlay import (
            TalkingHeadOverlaySkill,
        )

        # execute() 开头检查 is_file → 空文件即可（probe_duration 失败兜底 0.0）
        (tmp_path / "x.mp4").write_bytes(b"")
        skill = TalkingHeadOverlaySkill()
        return [
            ev
            async for ev in skill.execute(
                {
                    "video_path": str(tmp_path / "x.mp4"),
                    "gate_mode": "auto_confirm",
                    **params,
                }
            )
        ]

    @pytest.mark.asyncio
    async def test_matting_success_uses_alpha_frames_command(self, tmp_path):
        """matting=True 且抠像成功 → ffmpeg 命令消费 %06d.png 帧序列
        （overlay alpha 融合 + 原视频音频源），而非缩放原视频矩形窗。"""
        (tmp_path / "work").mkdir()
        cmds: list = []
        patches = self._patch_env(_matte_result(), cmds, tmp_path)
        with patches[0], patches[1], patches[2], patches[3]:
            events = await self._run_skill(
                tmp_path,
                {
                    "pip": True,
                    "matting": True,
                    "bg_color": "#112233",
                },
            )

        assert cmds, "应产生 ffmpeg 合成命令"
        joined = " ".join(str(c) for c in cmds[-1])
        assert "%06d.png" in joined, "应消费 alpha 帧序列"
        assert "overlay=" in joined, "应走 overlay 融合"
        # 10.20 优化：-t 硬限输出时长（-shortest 在 muxer 层会被
        # x264 lookahead 多带 1-2s，真机实证成片 56.07 vs 源 54.1）
        assert "-t" in [str(c) for c in cmds[-1]], "应用 -t 硬限时长"
        # ffmpeg 为 mock → 成片文件不存在，结尾必有 "output file not created"；
        # 除此之外不应有任何报错
        errors = [ev.get("error", "") for ev in events if ev.get("success") is False]
        assert all("not created" in e for e in errors), f"不应有其他报错: {errors}"

    @pytest.mark.asyncio
    async def test_matting_failure_falls_back_to_rect_pip(self, tmp_path):
        """matte_person 返回 None → 降级走现有矩形 PIP 分支（缩放原视频），
        如实广播降级事件，不静默。"""
        (tmp_path / "work").mkdir()
        cmds: list = []
        patches = self._patch_env(None, cmds, tmp_path)
        with patches[0], patches[1], patches[2], patches[3]:
            events = await self._run_skill(
                tmp_path,
                {
                    "pip": True,
                    "matting": True,
                },
            )

        assert cmds, "降级后仍应出矩形 PIP 合成命令"
        joined = " ".join(str(c) for c in cmds[-1])
        assert "%06d.png" not in joined, "降级命令不应消费帧序列"
        assert any("fallback" in str(ev.get("event", "")) for ev in events), "应有降级广播事件"

    @pytest.mark.asyncio
    async def test_deck_images_bypasses_html_capture(self, tmp_path):
        """deck_images 指定幻灯片图 → 跳过 deck_html 截图，直接进
        slides_to_video 管线。"""
        (tmp_path / "work").mkdir()
        cmds: list = []
        patches = self._patch_env(_matte_result(), cmds, tmp_path)
        imgs = [str(tmp_path / "s1.png"), str(tmp_path / "s2.png")]
        # 配图必须真实存在（is_file 过滤 + _probe_image_size 读取）；
        # Windows 中文路径 cv2.imwrite 静默失败 → imencode + write_bytes
        import cv2 as _cv2

        ok, buf = _cv2.imencode(".png", np.zeros((4, 4, 3), dtype=np.uint8))
        for p in imgs:
            Path(p).write_bytes(buf.tobytes())

        async def _fake_capture(deck_path, tmp, w, h):
            return []

        slides_mock = AsyncMock(return_value=None)
        skill_patches = patches + (
            patch(
                "capabilities.video.skills.talking_head_overlay."
                "TalkingHeadOverlaySkill._slides_to_video",
                new=slides_mock,
            ),
        )
        with (
            skill_patches[0],
            skill_patches[1],
            skill_patches[2],
            skill_patches[3],
            skill_patches[4],
        ):
            await self._run_skill(
                tmp_path,
                {
                    "pip": True,
                    "matting": True,
                    "deck_images": imgs,
                },
            )

        slides_mock.assert_awaited_once()
        called_shots = slides_mock.call_args.args[0]
        assert [Path(s) for s in called_shots] == [Path(i) for i in imgs]


class TestOverlayInputMatting:
    def test_matting_default_false(self):
        from capabilities.video.skills.talking_head_overlay import OverlayInput

        parsed = OverlayInput(video_path="/tmp/x.mp4")
        assert parsed.matting is False
        assert parsed.deck_images is None

    def test_matting_and_deck_images_accepted(self):
        from capabilities.video.skills.talking_head_overlay import OverlayInput

        parsed = OverlayInput(
            video_path="/tmp/x.mp4",
            matting=True,
            deck_images=["a.png", "b.png"],
        )
        assert parsed.matting is True
        assert parsed.deck_images == ["a.png", "b.png"]


class TestOverlayInputAutoDeck:
    """10.21 auto_deck 一键：输入契约三字段。"""

    def test_auto_deck_defaults(self):
        from capabilities.video.skills.talking_head_overlay import OverlayInput

        parsed = OverlayInput(video_path="/tmp/x.mp4")
        assert parsed.auto_deck is False
        assert parsed.deck_theme == "midnight"
        assert parsed.deck_max_slides == 6

    def test_auto_deck_fields_accepted(self):
        from capabilities.video.skills.talking_head_overlay import OverlayInput

        parsed = OverlayInput(
            video_path="/tmp/x.mp4",
            auto_deck=True,
            deck_theme="cream",
            deck_max_slides=8,
        )
        assert parsed.auto_deck is True
        assert parsed.deck_theme == "cream"
        assert parsed.deck_max_slides == 8


def _write_png(path, w=4, h=4):
    """真写 PNG 文件（auto_deck 产物 is_file 校验 + 画布探测用）。"""
    import cv2 as _cv2

    ok, buf = _cv2.imencode(".png", np.zeros((h, w, 3), dtype=np.uint8))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(buf.tobytes())
    return Path(path)


def _deck_pngs(tmp_path):
    return [
        _write_png(tmp_path / "deck" / "slide_01.png"),
        _write_png(tmp_path / "deck" / "slide_02.png"),
    ]


def _deck_transcript():
    return [
        {"index": 0, "start": 0.0, "end": 2.0, "text": "第一句", "words": []},
        {"index": 1, "start": 2.0, "end": 4.0, "text": "第二句", "words": []},
    ]


async def _run_skill(tmp_path, params, patches):
    import contextlib
    from unittest.mock import patch

    from capabilities.video.skills.talking_head_overlay import (
        TalkingHeadOverlaySkill,
    )

    (tmp_path / "x.mp4").write_bytes(b"")
    skill = TalkingHeadOverlaySkill()
    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        # 测试卫生：deck 产物落盘（slides.json / transcript_rows.json）
        # 隔离到 tmp_path —— 此前单测把 2 句 rows 写进真实
        # outputs/talking_head_overlay/deck_auto/ 覆盖了 live5 真机 24 句产物。
        stack.enter_context(
            patch(
                "capabilities.video.skills.talking_head_overlay.scoped_dir",
                new=lambda base, *a: tmp_path / "scope",
            )
        )
        return [
            ev
            async for ev in skill.execute(
                {
                    "video_path": str(tmp_path / "x.mp4"),
                    "gate_mode": "auto_confirm",
                    **params,
                }
            )
        ]


def _deck_patches(tmp_path, gen_return, captured_cmds, gen_mock=None):
    """patch 组：分辨率/ffmpeg/抠像/画布探测/临时目录/LLM 生成/LLM 实例。"""
    import os
    from unittest.mock import AsyncMock, MagicMock, patch

    from capabilities.video.adapters.person_matting import MatteResult

    async def _fake_ffmpeg(cmd):
        captured_cmds.append(cmd)

    @asynccontextmanager
    async def _fake_tmp():
        yield tmp_path / "work"

    if gen_mock is None:
        gen_mock = AsyncMock(return_value=gen_return)

    return (
        patch(
            "capabilities.video.skills.talking_head_overlay."
            "TalkingHeadOverlaySkill._probe_resolution",
            new=AsyncMock(return_value=(1080, 1920)),
        ),
        patch(
            "capabilities.video.skills.smart_cut._run_ffmpeg",
            new=AsyncMock(side_effect=_fake_ffmpeg),
        ),
        patch(
            "capabilities.video.adapters.person_matting.matte_person",
            new=AsyncMock(
                return_value=MatteResult(
                    frame_dir=Path("D:/fake/frames"),
                    count=660,
                    fps=30.0,
                    width=1080,
                    height=1920,
                )
            ),
        ),
        patch(
            "capabilities.video.adapters.person_matting._probe_image_size",
            new=MagicMock(return_value=(1080, 1920)),  # 同步函数（to_thread 调用）
        ),
        patch(
            "capabilities.video.skills.talking_head_overlay.managed_temp_dir",
            new=lambda: _fake_tmp(),
        ),
        patch(
            "capabilities.video.services.deck_orchestrator." "generate_deck_slides",
            new=gen_mock,
        ),
        patch(
            "capabilities.video.skills.talking_head_overlay."
            "TalkingHeadOverlaySkill._resolve_llm",
            return_value=MagicMock(),
        ),
    ), gen_mock


class TestAutoDeckPip:
    """10.21 auto_deck 一键分支：无 deck_images 时转录→LLM 幻灯片→主画面。

    降级契约（绝不阻断出片）：
      - transcript 有 → 直接拼原文（不调 whisper）
      - 无 transcript → whisper 兜底子进程（ct2 构造即污染进程
        DLL，RVM 同进程必崩 — 10.20 实证）
      - LLM 失败/空 → deck_fallback 事件 + 纯色底主画面

    基建（_deck_pngs/_deck_transcript/_run_skill/_deck_patches）为模块级，
    与 TestDeckGate（10.22）共用。
    """

    def _patches(self, tmp_path, gen_return, captured_cmds, gen_mock=None):
        return _deck_patches(tmp_path, gen_return, captured_cmds, gen_mock)

    async def _run(self, tmp_path, params, patches):
        return await _run_skill(tmp_path, params, patches)

    @pytest.mark.asyncio
    async def test_auto_deck_prefers_transcript_text(self, tmp_path):
        """transcript 已知文本 → 不调 whisper，LLM 收到拼接原文 + theme 透传。"""
        (tmp_path / "work").mkdir()
        cmds: list = []
        deck_pngs = _deck_pngs(tmp_path)
        patches, gen_mock = _deck_patches(tmp_path, deck_pngs, cmds)
        events = await _run_skill(
            tmp_path,
            {
                "pip": True,
                "matting": True,
                "auto_deck": True,
                "deck_theme": "cream",
                "transcript": _deck_transcript(),
            },
            patches,
        )

        # whisper 必须没被调（patch 未启用 — 若被调会真跑转录报错/慢）
        assert gen_mock.await_count == 1
        assert gen_mock.call_args.args[0] == "第一句\n第二句"
        assert gen_mock.call_args.kwargs.get("theme") == "cream"
        ready = [ev for ev in events if ev.get("event") == "deck_ready"]
        assert ready, "应有 deck_ready 事件"
        assert ready[0]["data"]["count"] == 2
        assert ready[0]["data"]["theme"] == "cream"

    @pytest.mark.asyncio
    async def test_auto_deck_whisper_fallback_uses_subprocess(self, tmp_path):
        """无 transcript → whisper 兜底走子进程帮助函数（ct2 构造即污染
        进程 DLL，10.20 实证 — 绝不与 RVM 同进程），文本进 LLM 生成。"""
        from unittest.mock import MagicMock, patch

        (tmp_path / "work").mkdir()
        cmds: list = []
        deck_pngs = _deck_pngs(tmp_path)
        sub_mock = MagicMock(
            return_value=[
                {"start": 0.0, "end": 3.0, "text": "whisper转的"},
            ]
        )

        patches, gen_mock = _deck_patches(tmp_path, deck_pngs, cmds)
        patches = patches + (
            patch(
                "capabilities.video.skills.talking_head_overlay." "_transcribe_cpu_subprocess",
                new=sub_mock,
            ),
        )
        import os

        before = os.environ.get("WHISPER_DEVICE")
        await _run_skill(
            tmp_path,
            {
                "pip": True,
                "matting": True,
                "auto_deck": True,
            },
            patches,
        )

        sub_mock.assert_called_once_with(str(tmp_path / "x.mp4"))
        assert gen_mock.call_args.args[0] == "whisper转的"
        # 父进程环境不得被污染（CPU 隔离在子进程 env 内完成）
        assert os.environ.get("WHISPER_DEVICE") == before

    @pytest.mark.asyncio
    async def test_auto_deck_llm_empty_falls_back_to_solid_bg(self, tmp_path):
        """LLM 生成空 → deck_fallback 事件 + 纯色 lavfi 底主画面，不阻断。"""
        (tmp_path / "work").mkdir()
        cmds: list = []
        patches, _ = _deck_patches(tmp_path, [], cmds)
        events = await _run_skill(
            tmp_path,
            {
                "pip": True,
                "matting": True,
                "auto_deck": True,
                "transcript": _deck_transcript(),
            },
            patches,
        )

        assert any(ev.get("event") == "deck_fallback" for ev in events), "应广播 deck_fallback"
        assert cmds, "降级后仍应出合成命令"
        joined = " ".join(str(c) for c in cmds[-1])
        assert "lavfi" in joined and "color=c=0x" in joined, "主画面应为纯色底"

    @pytest.mark.asyncio
    async def test_auto_deck_success_feeds_slides_pipeline(self, tmp_path):
        """deck 生成成功 → 产物按序进 _slides_to_video 管线（同 deck_images）。"""
        (tmp_path / "work").mkdir()
        cmds: list = []
        deck_pngs = _deck_pngs(tmp_path)
        patches, _ = _deck_patches(tmp_path, deck_pngs, cmds)
        slides_mock = AsyncMock(return_value=None)
        from unittest.mock import patch

        patches = patches + (
            patch(
                "capabilities.video.skills.talking_head_overlay."
                "TalkingHeadOverlaySkill._slides_to_video",
                new=slides_mock,
            ),
        )
        events = await _run_skill(
            tmp_path,
            {
                "pip": True,
                "matting": True,
                "auto_deck": True,
                "deck_theme": "sunset",
                "transcript": _deck_transcript(),
            },
            patches,
        )

        slides_mock.assert_awaited_once()
        assert [Path(p) for p in slides_mock.call_args.args[0]] == deck_pngs
        ready = [ev for ev in events if ev.get("event") == "deck_ready"]
        assert ready and ready[0]["data"]["theme"] == "sunset"


class TestTranscribeCpuSubprocess:
    """_transcribe_cpu_subprocess 帮助函数契约。"""

    def test_forces_cpu_in_child_env(self):
        """子进程 env 必须显式 WHISPER_DEVICE=cpu（GPU 只留给 RVM）。"""
        import json
        import subprocess as sp
        import sys
        from unittest.mock import MagicMock, patch

        from capabilities.video.skills.talking_head_overlay import (
            _transcribe_cpu_subprocess,
        )

        captured = {}

        def _fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["env"] = kwargs.get("env")
            return MagicMock(
                returncode=0,
                stdout=json.dumps(
                    [{"start": 0.0, "end": 1.0, "text": "子进程"}],
                    ensure_ascii=False,
                ).encode("utf-8"),
                stderr=b"",
            )

        with patch("subprocess.run", new=_fake_run):
            rows = _transcribe_cpu_subprocess("fake.mp4")

        assert captured["env"]["WHISPER_DEVICE"] == "cpu"
        assert captured["cmd"][0] == sys.executable
        assert "fake.mp4" in captured["cmd"]
        assert rows == [{"start": 0.0, "end": 1.0, "text": "子进程"}]

    def test_subprocess_failure_returns_empty(self):
        """子进程非零退出/输出不可解析 → []（不阻断，降级 deck_fallback）。"""
        from unittest.mock import MagicMock, patch

        from capabilities.video.skills.talking_head_overlay import (
            _transcribe_cpu_subprocess,
        )

        with patch(
            "subprocess.run",
            new=MagicMock(
                return_value=MagicMock(
                    returncode=1,
                    stdout=b"",
                    stderr=b"boom",
                )
            ),
        ):
            assert _transcribe_cpu_subprocess("fake.mp4") == []

        with patch(
            "subprocess.run",
            new=MagicMock(
                return_value=MagicMock(
                    returncode=0,
                    stdout=b"not json",
                    stderr=b"",
                )
            ),
        ):
            assert _transcribe_cpu_subprocess("fake.mp4") == []


def _write_slides_json(tmp_path, theme="midnight"):
    """闸门回调轮的 slides.json + 同目录 PNG（approve 消费的真实产物布局）。"""
    import json

    d = tmp_path / "deck_work"
    d.mkdir(exist_ok=True)
    _write_png(d / "slide_01.png")
    _write_png(d / "slide_02.png")
    jp = d / "slides.json"
    jp.write_text(
        json.dumps(
            {
                "theme": theme,
                "transcript": "第一句\n第二句",
                "slides": [
                    {"title": "开场", "bullets": ["a", "b"]},
                    {"title": "结尾", "bullets": ["c"]},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return jp


class TestDeckGate:
    """10.22 deck 确认闸门：deck 生成完先停出图 → approve/retheme/
    regenerate 回调轮；AUTO_CONFIRM 直通、fallback 不进闸门、轮数护栏。"""

    def _gate_patches(self, tmp_path, rendered, gen_mock=None):
        """闸门测试 patch 组：auto_deck 关（不转录），deck 线用回调数据。

        render_deck_from_slides 是同步函数（实现经 asyncio.to_thread
        调用，必须 MagicMock）；_slides_to_video mock 副作用写真文件
        （合成段 main_video_path.is_file 校验需要）。
        返回 (patches, gen_mock, render_mock) — patch 作用域限于
        _run_skill 内部 ExitStack，断言必须持有 mock 引用而非事后 import。
        """
        from unittest.mock import AsyncMock, MagicMock, patch

        from capabilities.video.adapters.person_matting import MatteResult

        async def _fake_ffmpeg(cmd):
            # 副作用产"输出文件"（命令最后一个参数）— 合成末尾
            # is_file 校验需要，否则 'output file not created' 提前终止
            try:
                Path(str(cmd[-1])).write_bytes(b"fake out")
            except OSError:
                pass

        def _fake_slides_video(images, out, **kw):
            Path(out).write_bytes(b"fake mp4")

        @asynccontextmanager
        async def _fake_tmp():
            yield tmp_path / "work"

        if gen_mock is None:
            gen_mock = AsyncMock(return_value=[])

        render_mock = MagicMock(return_value=rendered)

        return (
            (
                patch(
                    "capabilities.video.skills.talking_head_overlay."
                    "TalkingHeadOverlaySkill._probe_resolution",
                    new=AsyncMock(return_value=(1080, 1920)),
                ),
                patch(
                    "capabilities.video.skills.smart_cut._run_ffmpeg",
                    new=AsyncMock(side_effect=_fake_ffmpeg),
                ),
                patch(
                    "capabilities.video.adapters.person_matting.matte_person",
                    new=AsyncMock(
                        return_value=MatteResult(
                            frame_dir=Path("D:/fake/frames"),
                            count=660,
                            fps=30.0,
                            width=1080,
                            height=1920,
                        )
                    ),
                ),
                patch(
                    "capabilities.video.adapters.person_matting." "_probe_image_size",
                    new=MagicMock(return_value=(1080, 1920)),
                ),
                patch(
                    "capabilities.video.skills.talking_head_overlay." "managed_temp_dir",
                    new=lambda: _fake_tmp(),
                ),
                patch(
                    "capabilities.video.services.deck_orchestrator." "generate_deck_slides",
                    new=gen_mock,
                ),
                patch(
                    "capabilities.video.services.deck_orchestrator." "render_deck_from_slides",
                    new=render_mock,
                ),
                patch(
                    "capabilities.video.skills.talking_head_overlay."
                    "TalkingHeadOverlaySkill._slides_to_video",
                    new=AsyncMock(side_effect=_fake_slides_video),
                ),
            ),
            gen_mock,
            render_mock,
        )

    def test_input_deck_gate_fields(self):
        """10.22 新字段默认值与接受：deck_confirm/deck_slides_json/
        deck_revision/deck_revision_round。"""
        from capabilities.video.skills.talking_head_overlay import OverlayInput

        p = OverlayInput(video_path="x.mp4")
        assert p.deck_confirm is None
        assert p.deck_slides_json is None
        assert p.deck_revision is None
        assert p.deck_revision_round == 0

        q = OverlayInput(
            video_path="x.mp4",
            deck_confirm="retheme",
            deck_slides_json="D:/a/slides.json",
            deck_revision="标题太正式",
            deck_revision_round=2,
        )
        assert q.deck_confirm == "retheme"
        assert q.deck_revision_round == 2

    @pytest.mark.asyncio
    async def test_gate_stops_before_compose(self, tmp_path):
        """首轮生成成功（gate_mode 默认 standalone）→ deck_preview_ready
        停点 needs_confirmation=True，不进合成。"""
        (tmp_path / "work").mkdir()
        _write_slides_json(tmp_path)  # mock 产物布局参考（实际产物在 perm_dir/deck_auto）
        patches, _, _ = self._gate_patches(
            tmp_path,
            [],
            gen_mock=AsyncMock(return_value=_deck_pngs(tmp_path)),
        )
        events = await _run_skill(
            tmp_path,
            {
                "pip": True,
                "auto_deck": True,
                "transcript": _deck_transcript(),
                "gate_mode": "standalone",
            },
            patches,
        )

        names = [ev.get("event") for ev in events]
        assert "deck_preview_ready" in names
        gate = next(ev for ev in events if ev.get("event") == "deck_preview_ready")
        assert gate["data"]["needs_confirmation"] is True
        assert gate["data"]["theme"] == "midnight"
        assert gate["data"]["count"] == 2
        # slides.json 指向持久化目录（跨 execute 可达）
        assert gate["data"]["slides_json_path"].replace("\\", "/").endswith("deck_auto/slides.json")
        assert "pip_done" not in names, "闸门停点后不得合成"

    @pytest.mark.asyncio
    async def test_gate_auto_confirm_skips_pause(self, tmp_path):
        """AUTO_CONFIRM（auto_short_video 内嵌契约）→ 不停直合成。"""
        from unittest.mock import AsyncMock

        (tmp_path / "work").mkdir()
        _write_slides_json(tmp_path)
        patches, _, _ = self._gate_patches(
            tmp_path,
            [],
            gen_mock=AsyncMock(return_value=_deck_pngs(tmp_path)),
        )
        events = await _run_skill(
            tmp_path,
            {
                "pip": True,
                "auto_deck": True,
                "transcript": _deck_transcript(),
                "gate_mode": "auto_confirm",
            },
            patches,
        )

        names = [ev.get("event") for ev in events]
        assert "deck_preview_ready" in names
        gate = next(ev for ev in events if ev.get("event") == "deck_preview_ready")
        assert gate["data"]["needs_confirmation"] is False
        assert "pip_done" in names

    @pytest.mark.asyncio
    async def test_approve_composes_without_llm(self, tmp_path):
        """approve 回调轮 → 直接消费已落盘 PNG 合成，LLM 零调用，
        且不再进闸门。"""
        (tmp_path / "work").mkdir()
        jp = _write_slides_json(tmp_path)
        patches, gen_mock, render_mock = self._gate_patches(tmp_path, [])
        events = await _run_skill(
            tmp_path,
            {
                "pip": True,
                "deck_confirm": "approve",
                "deck_slides_json": str(jp),
            },
            patches,
        )

        gen_mock.assert_not_awaited()
        names = [ev.get("event") for ev in events]
        assert "pip_done" in names
        assert "deck_preview_ready" not in names, "回调轮不再进闸门"
        assert "deck_ready" in names

    @pytest.mark.asyncio
    async def test_approve_stale_extra_png_rerenders(self, tmp_path):
        """10.25 live7 实证：deck_auto 残留旧 PNG 张数不符（上一轮多页）
        → 弃 glob 按 slides.json 权威张数确定性重渲染（零 LLM）。"""
        (tmp_path / "work").mkdir()
        jp = _write_slides_json(tmp_path)  # 2 页 + slide_01/02
        _write_png(jp.parent / "slide_03.png")  # 旧残留多一张
        rerendered = [
            _write_png(tmp_path / "rerender_01.png"),
            _write_png(tmp_path / "rerender_02.png"),
        ]
        patches, gen_mock, render_mock = self._gate_patches(
            tmp_path,
            rendered=rerendered,
        )
        events = await _run_skill(
            tmp_path,
            {
                "pip": True,
                "deck_confirm": "approve",
                "deck_slides_json": str(jp),
            },
            patches,
        )

        gen_mock.assert_not_awaited()
        render_mock.assert_called_once()
        sv_mock = patches[-1].new  # _slides_to_video mock（patches 末位）
        imgs = [str(p) for p in sv_mock.call_args.args[0]]
        assert imgs == [
            str(p) for p in rerendered
        ], "进合成的是重渲染产物（2 张）而非含残留的 glob 结果"
        assert "pip_done" in [ev.get("event") for ev in events]

    @pytest.mark.asyncio
    async def test_retheme_rerenders_zero_llm(self, tmp_path):
        """retheme 回调轮 → render_deck_from_slides(theme=新主题) 重渲染，
        LLM 零调用，deck_ready 报新主题后照常合成。"""
        (tmp_path / "work").mkdir()
        jp = _write_slides_json(tmp_path)
        new_pngs = [
            _write_png(tmp_path / "work" / "slide_01.png"),
            _write_png(tmp_path / "work" / "slide_02.png"),
        ]
        patches, gen_mock, render_mock = self._gate_patches(tmp_path, new_pngs)
        events = await _run_skill(
            tmp_path,
            {
                "pip": True,
                "deck_confirm": "retheme",
                "deck_theme": "cream",
                "deck_slides_json": str(jp),
            },
            patches,
        )

        gen_mock.assert_not_awaited()
        render_mock.assert_called_once()
        assert render_mock.call_args.args[0] == jp
        assert render_mock.call_args.args[2] == "cream"
        ready = next(ev for ev in events if ev.get("event") == "deck_ready")
        assert ready["data"]["theme"] == "cream"
        names = [ev.get("event") for ev in events]
        assert "pip_done" in names

    @pytest.mark.asyncio
    async def test_regenerate_with_revision(self, tmp_path):
        """regenerate 回调轮 → LLM 带用户反馈重生成（revision 原话进
        feedback 参数），文本来源 = JSON 里的首轮 transcript（不转录）。"""
        (tmp_path / "work").mkdir()
        jp = _write_slides_json(tmp_path)
        new_pngs = [
            _write_png(tmp_path / "work" / "slide_01.png"),
            _write_png(tmp_path / "work" / "slide_02.png"),
        ]
        patches, gen_mock, render_mock = self._gate_patches(tmp_path, new_pngs)
        await _run_skill(
            tmp_path,
            {
                "pip": True,
                "deck_confirm": "regenerate",
                "deck_revision": "标题太正式，口语一点",
                "deck_slides_json": str(jp),
            },
            patches,
        )

        gen_mock.assert_awaited_once()
        assert gen_mock.call_args.kwargs.get("feedback") == "标题太正式，口语一点"
        assert gen_mock.call_args.args[0] == "第一句\n第二句"

    @pytest.mark.asyncio
    async def test_gate_round_limit_rejects(self, tmp_path):
        """regenerate 轮数超护栏（>5）→ deck_rejected 事件终止，不生成
        不合成。"""
        (tmp_path / "work").mkdir()
        jp = _write_slides_json(tmp_path)
        patches, gen_mock, render_mock = self._gate_patches(tmp_path, [])
        events = await _run_skill(
            tmp_path,
            {
                "pip": True,
                "deck_confirm": "regenerate",
                "deck_revision": "再改",
                "deck_revision_round": 6,
                "deck_slides_json": str(jp),
            },
            patches,
        )

        gen_mock.assert_not_awaited()
        names = [ev.get("event") for ev in events]
        assert "deck_rejected" in names
        assert "pip_done" not in names

    @pytest.mark.asyncio
    async def test_fallback_bypasses_gate(self, tmp_path):
        """LLM 空（deck_fallback）→ 无物可审，不进闸门直接纯色底出片。"""
        (tmp_path / "work").mkdir()
        patches, _, _ = self._gate_patches(tmp_path, [])
        events = await _run_skill(
            tmp_path,
            {
                "pip": True,
                "auto_deck": True,
                "transcript": _deck_transcript(),
            },
            patches,
        )

        names = [ev.get("event") for ev in events]
        assert "deck_fallback" in names
        assert "deck_preview_ready" not in names, "降级无物可审"
        assert "pip_done" in names

    @pytest.mark.asyncio
    async def test_pip_key_distinguishes_deck_vs_fallback(self, tmp_path):
        """10.23-C：同输入参数下 deck 形态与 fallback 形态产物路径
        必须不同（live3b 曾覆盖 live3 同路径）。"""
        (tmp_path / "work").mkdir()
        # deck 形态：LLM 产图
        patches_d, _, _ = self._gate_patches(
            tmp_path,
            [],
            gen_mock=AsyncMock(return_value=_deck_pngs(tmp_path)),
        )
        ev_d = await _run_skill(
            tmp_path,
            {
                "pip": True,
                "auto_deck": True,
                "transcript": _deck_transcript(),
            },
            patches_d,
        )
        # fallback 形态：LLM 空
        patches_f, _, _ = self._gate_patches(tmp_path, [])
        ev_f = await _run_skill(
            tmp_path,
            {
                "pip": True,
                "auto_deck": True,
                "transcript": _deck_transcript(),
            },
            patches_f,
        )

        out_d = next(e for e in ev_d if e.get("event") == "pip_done")["data"]["output_path"]
        out_f = next(e for e in ev_f if e.get("event") == "pip_done")["data"]["output_path"]
        assert out_d != out_f, f"形态指纹未区分：{out_d}"

    @pytest.mark.asyncio
    async def test_auto_deck_passes_boundary_durations(self, tmp_path):
        """10.23-D：auto_deck 走句边界映射 → _slides_to_video 收到
        durations 列表（长度 == 页数）。"""
        (tmp_path / "work").mkdir()
        _write_slides_json(tmp_path)
        patches, _, _ = self._gate_patches(
            tmp_path,
            [],
            gen_mock=AsyncMock(return_value=_deck_pngs(tmp_path)),
        )
        from unittest.mock import MagicMock, patch

        captured: dict = {}

        def _cap_video(images, out, **kw):
            captured.update(kw)
            Path(out).write_bytes(b"fake mp4")

        patches = patches + (
            patch(
                "capabilities.video.skills.talking_head_overlay."
                "TalkingHeadOverlaySkill._slides_to_video",
                new=MagicMock(side_effect=_cap_video),
            ),
        )
        await _run_skill(
            tmp_path,
            {
                "pip": True,
                "auto_deck": True,
                "transcript": _deck_transcript(),
                "gate_mode": "auto_confirm",
            },
            patches,
        )

        durs = captured.get("durations")
        assert durs is not None, "auto_deck 必须传句边界 durations"
        assert len(durs) == 2


class TestSlideBoundaryAlignment:
    """10.23-D：句边界映射函数 + _slides_to_video per-page durations。"""

    def test_map_durations_blocks_and_clamp(self):
        """4 句 → 2 页：块均分映射；首页 start 夹 0、末页 end 夹 total。"""
        from capabilities.video.skills.talking_head_overlay import (
            _slide_durations_from_transcript,
        )

        rows = [
            {"start": 0.0, "end": 2.0},
            {"start": 2.0, "end": 4.0},
            {"start": 4.0, "end": 6.0},
            {"start": 6.0, "end": 8.0},
        ]
        durs = _slide_durations_from_transcript(rows, 2, 8.5)
        assert len(durs) == 2
        assert durs[0] == pytest.approx(4.0)
        assert durs[1] == pytest.approx(4.5)  # 8.5 - 4.0（末页夹到片尾）

    def test_map_durations_empty_returns_empty(self):
        from capabilities.video.skills.talking_head_overlay import (
            _slide_durations_from_transcript,
        )

        assert _slide_durations_from_transcript([], 3, 9.0) == []

    # 10.25-F：durations+多页语义已由 concat 硬切升级为 xfade 转场链，
    # 本处旧 concat 断言由 TestXfadeTransitions.test_slides_video_boundary_uses_xfade 接管。

    @pytest.mark.asyncio
    async def test_slides_video_uniform_fallback(self, tmp_path):
        """durations=None → 旧行为（slide_duration 均分 + 交叉余量）。"""
        from unittest.mock import AsyncMock, patch

        from capabilities.video.skills.talking_head_overlay import (
            TalkingHeadOverlaySkill,
        )

        pngs = [_write_png(tmp_path / f"s{i}.png") for i in range(2)]
        captured: dict = {}

        async def fake_ff(cmd):
            concat_p = Path(cmd[cmd.index("-i") + 1])
            captured["concat"] = concat_p.read_text(encoding="utf-8")

        with patch(
            "capabilities.video.skills.smart_cut._run_ffmpeg",
            new=AsyncMock(side_effect=fake_ff),
        ):
            await TalkingHeadOverlaySkill()._slides_to_video(
                pngs,
                tmp_path / "out.mp4",
                slide_duration=5.0,
            )

        assert "duration 5.0" in captured["concat"]
        assert "duration 5.8" in captured["concat"]


class TestXfadeTransitions:
    """10.25-F：句边界模式 xfade 转场链（6 样式轮换 0.5s，切换点=句边界零漂移）。"""

    def test_xfade_cmd_math(self):
        """e_i=d_i+td 全页；offset_i=前缀和(d)[i]；转场区间 [B_i, B_i+td]。"""
        from capabilities.video.skills.talking_head_overlay import (
            _build_xfade_cmd,
        )

        pngs = [Path(f"/x/s{i}.png") for i in range(3)]
        cmd, meta = _build_xfade_cmd(
            pngs,
            Path("/x/out.mp4"),
            durations=[5.8, 11.5, 7.6],
        )
        assert meta["e"] == pytest.approx([6.3, 12.0, 8.1])
        assert meta["offsets"] == pytest.approx([5.8, 17.3])
        # 总长 = sum(e) - (n-1)*td = sum(d) + td（末页多给 td 保完整显示，
        # 尾部 0.5s 由下游合成 -t 硬截，句边界不漂移）
        total = sum(meta["e"]) - 2 * 0.5
        assert total == pytest.approx(5.8 + 11.5 + 7.6 + 0.5)

    def test_xfade_styles_rotation(self):
        from capabilities.video.skills.talking_head_overlay import (
            _XFADE_STYLES,
            _build_xfade_cmd,
        )

        pngs = [Path(f"/x/s{i}.png") for i in range(4)]
        _, meta = _build_xfade_cmd(pngs, Path("/x/o.mp4"), durations=[2.0] * 4)
        assert meta["styles"] == [_XFADE_STYLES[i % len(_XFADE_STYLES)] for i in range(3)]
        assert meta["styles"][0] == "fade"

    def test_xfade_cmd_shape(self):
        """命令形态：多输入 -loop 1 -t e_i + filter_complex 链 + [vout] 映射。"""
        from capabilities.video.skills.talking_head_overlay import (
            _build_xfade_cmd,
        )

        pngs = [Path(f"/x/s{i}.png") for i in range(2)]
        cmd, _meta = _build_xfade_cmd(
            pngs,
            Path("/x/out.mp4"),
            durations=[5.0, 6.0],
        )
        s = " ".join(cmd)
        assert s.count("-loop 1") == 2
        assert "-t 5.50" in s and "-t 6.50" in s
        assert "xfade=transition=fade:duration=0.50:offset=5.00" in s
        assert "-map [vout]" in s
        assert "scale=1920:1080" in s and "fps=30" in s

    @pytest.mark.asyncio
    async def test_slides_video_boundary_uses_xfade(self, tmp_path):
        """durations 给定 n>1 → xfade 链（替换 concat 硬切）。"""
        from unittest.mock import AsyncMock, patch

        from capabilities.video.skills.talking_head_overlay import (
            TalkingHeadOverlaySkill,
        )

        pngs = [_write_png(tmp_path / f"s{i}.png") for i in range(2)]
        captured: dict = {}

        async def fake_ff(cmd):
            captured["cmd"] = cmd

        with patch(
            "capabilities.video.skills.smart_cut._run_ffmpeg",
            new=AsyncMock(side_effect=fake_ff),
        ):
            await TalkingHeadOverlaySkill()._slides_to_video(
                pngs,
                tmp_path / "out.mp4",
                slide_duration=5.0,
                durations=[3.0, 6.0],
            )

        s = " ".join(captured["cmd"])
        assert "xfade=transition=fade:duration=0.50:offset=3.00" in s
        assert not (tmp_path / "concat.txt").exists(), "xfade 路不落 concat.txt"

    @pytest.mark.asyncio
    async def test_slides_video_single_page_and_uniform_stay_concat(self, tmp_path):
        """单页 / durations=None → 旧 concat 硬切（回归保护）。"""
        from unittest.mock import AsyncMock, patch

        from capabilities.video.skills.talking_head_overlay import (
            TalkingHeadOverlaySkill,
        )

        for shots, kwargs in (
            (
                [_write_png(tmp_path / f"s{i}.png") for i in range(2)],
                {"slide_duration": 5.0},
            ),  # None 均分
            (
                [_write_png(tmp_path / "single.png")],
                {"slide_duration": 5.0, "durations": [9.0]},
            ),  # 单页
        ):
            captured: dict = {}

            async def fake_ff(cmd):
                captured["cmd"] = cmd

            with patch(
                "capabilities.video.skills.smart_cut._run_ffmpeg",
                new=AsyncMock(side_effect=fake_ff),
            ):
                await TalkingHeadOverlaySkill()._slides_to_video(
                    shots,
                    tmp_path / "out.mp4",
                    **kwargs,
                )
            assert "xfade=" not in " ".join(captured["cmd"]), kwargs


class TestChainedCut:
    """10.27 串联断链修复：kept_segments 只映射词戳，视频本体从不被裁
    （live9-2 实证成片 54.08s ≈ 源而非裁后 53.8s）。修复：
    cut_video_path 提供 → 本体/时长切裁后轴，转录仍跑原视频
    （词戳原轴 → TimeMapper 映射）；句边界 rows 同步映射。"""

    def test_remap_transcript_rows(self):
        """纯函数：原轴 rows → 裁后轴；删除区内的句丢弃。"""
        from capabilities.video.skills.talking_head_overlay import (
            _remap_transcript_rows,
        )

        kept = [[0.0, 2.68], [3.48, 54.1]]
        rows = [
            {"start": 1.0, "end": 2.0, "text": "a"},  # 保留区内 → 不变
            {"start": 4.0, "end": 6.0, "text": "b"},  # 删除区后 → -0.8
            {"start": 3.0, "end": 3.2, "text": "c"},  # 删除区内 → 丢弃
        ]
        out = _remap_transcript_rows(rows, kept)
        assert [(r["start"], r["end"]) for r in out] == [(1.0, 2.0), (3.2, 5.2)]

    @pytest.mark.asyncio
    async def test_cut_video_used_for_body(self, tmp_path):
        """cut_video_path → _execute_pip 收裁后视频 + 裁后时长。"""
        from unittest.mock import AsyncMock, patch

        cv = tmp_path / "cut.mp4"
        cv.write_bytes(b"x")
        (tmp_path / "work").mkdir()
        base, _, _ = TestDeckGate()._gate_patches(tmp_path, [])

        captured: dict = {}

        async def fake_probe(p):
            return 53.8 if p.endswith("cut.mp4") else 54.1

        async def fake_pip(self_, parsed, vp, dur, scope):
            captured["vp"] = Path(vp)
            captured["dur"] = dur
            yield {"event": "pip_done", "data": {"output_path": "o.mp4"}}

        extra = [
            patch("capabilities.video.adapters.silence_detect.probe_duration", new=fake_probe),
            patch(
                "capabilities.video.skills.talking_head_overlay."
                "TalkingHeadOverlaySkill._execute_pip",
                new=fake_pip,
            ),
        ]
        jp = _write_slides_json(tmp_path)
        events = await _run_skill(
            tmp_path,
            {
                "pip": True,
                "deck_confirm": "approve",
                "deck_slides_json": str(jp),
                "cut_video_path": str(cv),
                "kept_segments": [[0.0, 2.68], [3.48, 54.1]],
            },
            list(base) + extra,
        )

        assert captured.get("vp") == cv, captured
        assert captured.get("dur") == pytest.approx(53.8), captured

    @pytest.mark.asyncio
    async def test_cut_video_missing_rejects(self, tmp_path):
        """cut_video_path 不存在 → 明确报错不静默吞掉。"""
        from unittest.mock import AsyncMock, patch

        (tmp_path / "work").mkdir()
        base, _, _ = TestDeckGate()._gate_patches(tmp_path, [])

        async def fake_probe(p):
            return 54.1

        async def reject_pip(self_, parsed, vp, dur, scope):
            raise AssertionError("不应进合成")
            yield  # pragma: no cover

        extra = [
            patch("capabilities.video.adapters.silence_detect.probe_duration", new=fake_probe),
            patch(
                "capabilities.video.skills.talking_head_overlay."
                "TalkingHeadOverlaySkill._execute_pip",
                new=reject_pip,
            ),
        ]
        jp = _write_slides_json(tmp_path)
        events = await _run_skill(
            tmp_path,
            {
                "pip": True,
                "deck_confirm": "approve",
                "deck_slides_json": str(jp),
                "cut_video_path": str(tmp_path / "ghost_cut.mp4"),
                "kept_segments": [[0.0, 2.68], [3.48, 54.1]],
            },
            list(base) + extra,
        )

        assert any(
            ev.get("success") is False and "cut video not found" in str(ev.get("error", ""))
            for ev in events
        ), events


class TestCrossGapRows:
    """10.29 live11 实证：句 start 落删除区内、end 跨到保留区（whisper
    幻听碎词）→ 中点判定保留 → 映射后页边界错位 → 5 页只出 4 durs →
    xfade 命令畸形 ffmpeg 挂。契约：句 start/end 任一端落删除区 → 丢弃；
    _build_xfade_cmd 前置 N==N 断言（fail fast）。"""

    def test_cross_gap_sentence_dropped(self):
        from capabilities.video.skills.talking_head_overlay import (
            _remap_transcript_rows,
        )

        kept = [[0.0, 2.68], [3.48, 54.1]]
        rows = [
            {"start": 1.0, "end": 2.0, "text": "a"},
            {"start": 3.4, "end": 4.5, "text": "d"},  # start 在 gap 内 → 丢
            {"start": 4.0, "end": 6.0, "text": "b"},
        ]
        out = _remap_transcript_rows(rows, kept)
        assert [(r["start"], r["end"]) for r in out] == [
            (1.0, 2.0),
            (3.2, 5.2),
        ], out

    def test_xfade_requires_equal_lengths(self):
        import pytest as _pytest

        from capabilities.video.skills.talking_head_overlay import (
            _build_xfade_cmd,
        )

        pngs = [Path(f"s{i}.png") for i in range(5)]
        with _pytest.raises(ValueError, match="durations"):
            _build_xfade_cmd(pngs, Path("o.mp4"), [1.0, 2.0, 3.0, 4.0])


class TestDursChunkCount:
    """10.29 live11 实证：M=11 句 N=5 页时 ceil(11/5)=3 → 只 4 块 ≠
    5 页 → xfade mismatch。契约：返回恰 n_slides 个时长。"""

    def test_eleven_spans_five_slides(self):
        from capabilities.video.skills.talking_head_overlay import (
            _slide_durations_from_transcript,
        )

        rows = [{"start": float(i), "end": float(i) + 0.8, "text": "s"} for i in range(11)]
        durs = _slide_durations_from_transcript(rows, 5, 12.0)
        assert len(durs) == 5, durs
        assert all(d >= 0.5 for d in durs) and sum(durs) <= 12.0, durs

    def test_fewer_spans_than_slides(self):
        from capabilities.video.skills.talking_head_overlay import (
            _slide_durations_from_transcript,
        )

        rows = [{"start": 0.0, "end": 1.0, "text": "a"}, {"start": 2.0, "end": 3.0, "text": "b"}]
        durs = _slide_durations_from_transcript(rows, 4, 6.0)
        assert len(durs) == 4 and all(d >= 0.5 for d in durs), durs


class TestOverlayDecor:
    """10.30：第三种合成形态 — 原片全屏 + 素材卡按句边界叠加 + fade 特效。
    用户需求：不抠人、不遮五官、原片为底。"""

    SLIDES = [
        {
            "title": "老板推单，是你让他待不下去",
            "bullets": [],
            "slide_type": "quote",
            "image": None,
        },
        {
            "title": "问题在服务模式",
            "bullets": [
                {"text": "群里发需求，半天没人理", "emphasis": "none"},
                {"text": "点了单，管理不拉群不接待", "emphasis": "keyword"},
            ],
            "slide_type": "points",
            "image": None,
        },
    ]

    def test_decor_card_html_dimensions(self):
        """P0-3 轻装饰卡：720 宽；金句海报 64px > 要点标题 46px。"""
        from capabilities.video.services.deck_orchestrator import (
            _decor_card_html,
        )

        h_quote = _decor_card_html(self.SLIDES[0], theme="midnight")
        h_points = _decor_card_html(self.SLIDES[1], theme="midnight")
        assert "720" in h_quote
        assert h_points.count("群里发需求") == 1
        assert "font-size:64px" in h_quote and "font-size:46px" in h_points

    def test_render_decor_cards_png(self, tmp_path):
        """Playwright 透明截图：PNG 720×340 统一高度带且有 alpha 通道。"""
        from PIL import Image

        from capabilities.video.services.deck_orchestrator import (
            render_decor_cards,
        )

        paths = render_decor_cards(self.SLIDES, tmp_path, theme="midnight")
        assert len(paths) == 2
        for p in paths:
            img = Image.open(p)
            assert img.width == 720 and img.height == 340
            assert img.mode == "RGBA"

    def test_build_decor_cmd(self):
        """命令构造：fade 窗口=句边界、overlay 顶部居中、-t 总长。"""
        from capabilities.video.skills.talking_head_overlay import (
            _build_decor_cmd,
        )

        cards = [Path(f"c{i}.png") for i in range(3)]
        bounds = [(0.0, 10.0), (10.0, 20.0), (20.0, 32.62)]
        cmd, meta = _build_decor_cmd(
            Path("v.mp4"),
            cards,
            bounds,
            32.62,
            720,
            1280,
        )
        assert cmd.count("-i") == 4  # 视频 + 3 卡
        joined = " ".join(cmd)
        assert "overlay" in joined and "-t" in joined
        # fade in st=0 / fade out st=10-0.4（首卡）
        assert "fade=t=in:st=0.0" in joined.replace(":alpha=1", "")
        assert "fade=t=out:st=9.6" in joined.replace(":alpha=1", "")
        assert meta["windows"] == bounds

    @pytest.mark.asyncio
    async def test_execute_decor_synthesizes(self, tmp_path):
        """_execute_decor：渲染 N 卡 + 跑 ffmpeg + decor_done 事件。"""
        from unittest.mock import patch

        from capabilities.video.skills.talking_head_overlay import (
            _execute_decor,
        )

        cards = [tmp_path / "c1.png", tmp_path / "c2.png"]
        for c in cards:
            c.write_bytes(b"x")

        async def fake_ff(cmd):
            out = Path(cmd[-1])
            out.write_bytes(b"x")
            return ""

        with patch(
            "capabilities.video.skills.smart_cut._run_ffmpeg",
            side_effect=fake_ff,
        ):
            events = []
            async for ev in _execute_decor(
                {"decor_scale": 0.85, "decor_effect": "fade"},
                tmp_path / "v.mp4",
                20.0,
                tmp_path,
                cards=[cards[0], cards[1]],
                bounds=[(0.0, 12.0), (12.0, 20.0)],
            ):
                events.append(ev)
        assert any(e.get("event") == "decor_done" for e in events), events


class TestKaraoke:
    """P0-1 词级 karaoke 字幕 — 帧计划纯函数 + 渲染 + ffconcat + 命令接入。"""

    ROWS = [
        {
            "start": 0.0,
            "end": 2.0,
            "text": "老板推单，",
            "words": [
                {"text": "老板", "start": 0.0, "end": 0.6},
                {"text": "推单", "start": 0.6, "end": 1.4},
                {"text": "，", "start": 1.4, "end": 1.8},
            ],
        },
        {
            "start": 2.5,
            "end": 5.0,
            "text": "你却说不赚钱",
            "words": [
                {"text": "你", "start": 2.5, "end": 2.8},
                {"text": "却说", "start": 2.8, "end": 3.4},
                {"text": "不赚钱", "start": 3.4, "end": 4.6},
            ],
        },
    ]

    def test_karaoke_frame_plan_boundaries(self):
        """每词一帧：帧窗=[词start, 下一词start)；句末词延伸到下一句首词。"""
        from capabilities.video.skills.talking_head_overlay import (
            _karaoke_frame_plan,
        )

        frames = _karaoke_frame_plan(self.ROWS, 6.0)
        assert len(frames) == 6
        assert frames[0]["start"] == 0.0 and frames[0]["end"] == 0.6
        assert frames[1]["end"] == 1.4
        # 句1 末词帧延伸到下一句首词 start=2.5（字幕常驻到换句）
        assert frames[2]["end"] == 2.5
        # 全片末帧延伸到 duration
        assert frames[5]["start"] == 3.4 and frames[5]["end"] == 6.0
        # 帧携带句/词索引供渲染定位
        assert frames[3]["si"] == 1 and frames[3]["wi"] == 0

    def test_karaoke_frame_plan_degrade(self):
        """词戳缺失/空转录 → 空计划（字幕层整体跳过，不阻断）。"""
        from capabilities.video.skills.talking_head_overlay import (
            _karaoke_frame_plan,
        )

        assert (
            _karaoke_frame_plan(
                [{"start": 0, "end": 2, "text": "无词级"}],
                2.0,
            )
            == []
        )
        assert _karaoke_frame_plan([], 2.0) == []

    def test_render_karaoke_frames_png(self, tmp_path):
        """单页 evaluate 驱动逐帧截图：PNG 数=帧数，RGBA 透明底。"""
        from PIL import Image

        from capabilities.video.skills.talking_head_overlay import (
            _render_karaoke_frames,
        )

        frames = [
            {"si": 0, "wi": 0, "start": 0.0, "end": 0.6},
            {"si": 0, "wi": 1, "start": 0.6, "end": 1.4},
            {"si": 1, "wi": 2, "start": 3.4, "end": 6.0},
        ]
        pngs = _render_karaoke_frames(frames, self.ROWS, tmp_path)
        assert len(pngs) == 3
        for p in pngs:
            img = Image.open(p)
            assert img.mode == "RGBA" and img.width == 720

    def test_write_ffconcat_format(self, tmp_path):
        """ffconcat：版本头 + duration 行 + 末帧重复（concat 末帧陷阱）。"""
        from capabilities.video.skills.talking_head_overlay import (
            _write_ffconcat,
        )

        frames = [
            {"si": 0, "wi": 0, "start": 0.0, "end": 0.6},
            {"si": 0, "wi": 1, "start": 0.6, "end": 1.4},
        ]
        pngs = [tmp_path / "a.png", tmp_path / "b.png"]
        lst = _write_ffconcat(frames, pngs, tmp_path)
        txt = lst.read_text(encoding="utf-8")
        assert txt.startswith("ffconcat version 1.0")
        assert "duration 0.600" in txt and "duration 0.800" in txt
        file_lines = [l for l in txt.splitlines() if l.startswith("file")]
        assert file_lines[-1] == file_lines[-2]

    def test_build_decor_cmd_with_subtitle(self):
        """字幕流接入：concat 输入 + fps/format + 70% 高度带 overlay。"""
        from capabilities.video.skills.talking_head_overlay import (
            _build_decor_cmd,
        )

        cmd, meta = _build_decor_cmd(
            Path("v.mp4"),
            [Path("c0.png")],
            [(0.0, 5.0)],
            5.0,
            720,
            1280,
            subtitle_ffconcat=Path("kao/list.txt"),
        )
        joined = " ".join(cmd)
        assert "concat" in joined and "-safe" in joined
        # 字幕 overlay 在素材卡 overlay 之后（链条末端）
        assert joined.index("overlay") < joined.rindex("overlay")
        assert "896" in joined  # int(1280*0.70)
        assert meta["subtitle"] is True

    def test_karaoke_display_simplifies(self):
        """whisper 词流繁体 → 展示层简体（字幕直出观众必须简体）。"""
        from capabilities.video.skills.talking_head_overlay import (
            _karaoke_display_data,
        )

        data = _karaoke_display_data(
            [{"text": "的時候沒有", "words": [{"text": "時候"}, {"text": "沒有"}]}]
        )
        assert [w["text"] for w in data[0]["words"]] == ["时候", "没有"]

    def test_remap_transcript_rows_shifts_words(self):
        """串联裁剪：句内词戳随句平移，落在删除区的词丢弃。"""
        from capabilities.video.skills.talking_head_overlay import (
            _remap_transcript_rows,
        )

        rows = [
            {
                "start": 10.0,
                "end": 14.0,
                "text": "留词删词留词",
                "words": [
                    {"text": "留", "start": 10.0, "end": 10.5},
                    {"text": "删", "start": 11.0, "end": 11.5},
                    {"text": "留2", "start": 12.5, "end": 13.0},
                ],
            }
        ]
        out = _remap_transcript_rows(rows, [[0.0, 10.5], [12.5, 20.0]])
        assert len(out) == 1
        ws = out[0]["words"]
        assert [w["text"] for w in ws] == ["留", "留2"]
        assert ws[0]["start"] == 10.0  # gap 前不动
        assert ws[1]["start"] == 10.5  # gap 长 2s → 12.5-2


class TestLightDecor:
    """P0-3 轻装饰卡 — 无底框白字黑描边 + 渐变压暗 + 宽度降 60%。"""

    def test_decor_card_html_no_panel(self):
        """退役大横幅：无主题实心底框/投影，改描边文字+渐变压暗。"""
        from capabilities.video.services.deck_orchestrator import (
            _DECK_THEMES,
            _decor_card_html,
        )

        html = _decor_card_html(TestOverlayDecor.SLIDES[1], theme="midnight")
        assert _DECK_THEMES["midnight"]["body_bg"] not in html
        assert "text-shadow" in html
        assert "linear-gradient" in html
        assert "box-shadow" not in html

    def test_decor_card_html_keyword_accent(self):
        """keyword 要点用品牌高亮色（与 karaoke 当前词同一强调色）。"""
        from capabilities.video.services.deck_orchestrator import (
            _decor_card_html,
        )

        html = _decor_card_html(TestOverlayDecor.SLIDES[1], theme="midnight")
        assert "#39E508" in html

    def test_decor_card_html_fits(self):
        """金句页带要点：标题降 46px + 要点封顶 2 条 + 溢出硬护栏。

        live13 实证：quote 64px 标题 + 3 要点超出 340px 高度带，
        第 3 条拦腰截断成幽灵字。
        """
        from capabilities.video.services.deck_orchestrator import (
            _decor_card_html,
        )

        slide = {
            "title": "老板推单，是你让他忍不下去",
            "slide_type": "quote",
            "image": None,
            "bullets": [
                {"text": "要点一", "emphasis": "none"},
                {"text": "要点二", "emphasis": "none"},
                {"text": "要点三", "emphasis": "none"},
            ],
        }
        html = _decor_card_html(slide, theme="midnight")
        assert "font-size:46px" in html  # 带要点不再用 64px 海报字
        assert "要点一" in html and "要点二" in html
        assert "要点三" not in html  # 封顶 2 条
        assert "overflow:hidden" in html

    def test_decor_scale_default_60(self):
        """卡宽默认从 85% 降到 60%。"""
        from capabilities.video.skills.talking_head_overlay import OverlayInput

        assert OverlayInput.model_fields["decor_scale"].default == 0.6


class TestTriggerDecor:
    """M2-P0-2 内容触发式素材卡 — deck trigger 契约 + 词流搜索 +
    触发时刻卡窗 + pop 入场（说到才弹卡，消灭 PPT 感）。"""

    ROWS = [
        {
            "start": 0.0,
            "end": 2.0,
            "text": "老板推单，",
            "words": [
                {"text": "老板", "start": 0.0, "end": 0.6},
                {"text": "推单", "start": 0.6, "end": 1.4},
                {"text": "，", "start": 1.4, "end": 1.8},
            ],
        },
        {
            "start": 2.5,
            "end": 5.0,
            "text": "你却说不赚钱",
            "words": [
                {"text": "你", "start": 2.5, "end": 2.8},
                {"text": "却说", "start": 2.8, "end": 3.4},
                {"text": "不赚钱", "start": 3.4, "end": 4.6},
            ],
        },
    ]

    def test_deck_prompt_trigger_contract(self):
        """deck LLM 契约：每页输出 trigger 触发词，且要求取转录原文。"""
        from capabilities.video.services.deck_orchestrator import _DECK_PROMPT

        assert "trigger" in _DECK_PROMPT
        assert "转录" in _DECK_PROMPT

    def test_parse_deck_json_trigger(self):
        """trigger 契约解析：dict.text 收录、裸 str 容错、无效丢弃、
        旧格式无 trigger 不报错。"""
        import json

        from capabilities.video.services.deck_orchestrator import (
            parse_deck_json,
        )

        raw = json.dumps(
            [
                {
                    "title": "T1",
                    "bullets": [{"text": "b", "emphasis": "none"}],
                    "trigger": {"text": "老板推单"},
                },
                {"title": "T2", "bullets": [{"text": "b"}], "trigger": "點單"},
                {"title": "T3", "bullets": [{"text": "b"}], "trigger": {"text": "   "}},
                {"title": "T4", "bullets": [{"text": "b"}], "trigger": {"text": 123}},
                {"title": "說句扎心的", "bullets": [{"text": "老闆推單"}]},
            ],
            ensure_ascii=False,
        )
        slides = parse_deck_json(raw)
        assert len(slides) == 5
        assert slides[0]["trigger"] == {"text": "老板推单"}
        assert slides[1]["trigger"] == {"text": "点单"}  # 裸 str + 繁→简
        assert "trigger" not in slides[2]
        assert "trigger" not in slides[3]
        assert "trigger" not in slides[4]
        # 繁体标题/要点 → 简体（live14 实证 LLM 照抄 whisper 繁体）
        assert slides[4]["title"] == "说句扎心的"
        assert slides[4]["bullets"][0]["text"] == "老板推单"

    def test_find_trigger_start(self):
        """词流搜触发词：跨词拼接匹配 → 首字符所在词 start；
        繁体词流可命中简体触发词；搜不到/空输入 → None。"""
        from capabilities.video.skills.talking_head_overlay import (
            _find_trigger_start,
        )

        assert _find_trigger_start(self.ROWS, "老板推单") == 0.0
        assert _find_trigger_start(self.ROWS, "不赚钱") == 3.4
        rows_t = [{"words": [{"text": "老闆推單", "start": 1.0, "end": 2.0}]}]
        assert _find_trigger_start(rows_t, "老板推单") == 1.0
        assert _find_trigger_start(self.ROWS, "不存在词") is None
        assert _find_trigger_start(self.ROWS, "") is None
        assert _find_trigger_start([], "老板") is None
        assert _find_trigger_start([{"text": "无词级"}], "老板") is None

    def test_trigger_bounds(self):
        """触发时刻卡窗：trigger 命中 → 卡从说到那一刻弹入
        （不囿于页区间），停留 min(页时长, 2.5s)；搜不到/无 trigger
        → 回退句边界页区间。"""
        from capabilities.video.skills.talking_head_overlay import (
            _trigger_bounds,
        )

        slides = [
            {"title": "A", "bullets": [], "trigger": {"text": "老板推单"}},
            {"title": "B", "bullets": [], "trigger": {"text": "不赚钱"}},
            {"title": "C", "bullets": [], "trigger": {"text": "不存在"}},
            {"title": "D", "bullets": []},
        ]
        page = [(0.0, 2.2), (2.2, 5.0), (5.0, 7.0), (7.0, 9.0)]
        b = _trigger_bounds(slides, self.ROWS, page, 9.0)
        assert b[0] == (0.0, 2.2)  # @0.0 → +min(2.2, 2.5)=2.2
        assert b[1] == (3.4, 5.9)  # @3.4 → +min(2.8, 2.5)=2.5
        assert b[2] == (5.0, 7.0)  # 搜不到 → 回退
        assert b[3] == (7.0, 9.0)  # 旧格式无 trigger → 回退

    def test_trigger_bounds_overlap_cap_and_fallback(self):
        """双触发贴脸：前卡 end 封顶所有其它更晚的触发时刻（含页序
        颠倒的卡），停留 <0.8s → 回退页区间（防同位叠卡 + 防闪现）。"""
        from capabilities.video.skills.talking_head_overlay import (
            _trigger_bounds,
        )

        rows = [
            {
                "words": [
                    {"text": "甲乙", "start": 1.0, "end": 1.4},
                    {"text": "丙", "start": 1.6, "end": 2.0},
                    {"text": "丁", "start": 5.0, "end": 5.4},
                ]
            }
        ]
        slides = [
            {"trigger": {"text": "甲乙"}},
            {"trigger": {"text": "丁"}},
            {"trigger": {"text": "丙"}},  # 页序与触发顺序颠倒
        ]
        page = [(0.0, 4.0), (4.0, 8.0), (8.0, 12.0)]
        b = _trigger_bounds(slides, rows, page, 12.0)
        # 卡0 @1.0 原停留至 3.5，被 1.6 封顶 → 0.6s <0.8 回退
        assert b[0] == (0.0, 4.0)
        assert b[1] == (5.0, 7.5)  # 丁 @5.0 → +min(4.0, 2.5)
        assert b[2] == (1.6, 4.1)  # 丙 @1.6（被 5.0 封顶不生效）

    def test_build_decor_cmd_pop(self):
        """pop 入场：0.5x/1.08x/1.0x 三档 + 0.06s 快切 enable 窗 +
        末档 fade out 退场，无 fade in。"""
        from capabilities.video.skills.talking_head_overlay import (
            _build_decor_cmd,
        )

        cmd, meta = _build_decor_cmd(
            Path("v.mp4"),
            [Path("c0.png")],
            [(2.0, 8.0)],
            8.0,
            720,
            1280,
            scale=0.6,
            effect="pop",
        )
        joined = " ".join(cmd)
        assert "scale=216:" in joined  # 432*0.5
        assert "scale=466:" in joined  # 432*1.08
        assert "scale=432:" in joined  # 1.0x
        assert "enable='between(t,2.00,2.06)'" in joined
        assert "enable='between(t,2.06,2.12)'" in joined
        assert "enable='gte(t,2.12)'" in joined
        assert "fade=t=out:st=7.6" in joined.replace(":alpha=1", "")
        assert "fade=t=in" not in joined
        assert meta["effect"] == "pop"

    def test_decor_effect_default_pop(self):
        """decor_effect 默认从 fade 升级为 pop（M2 触发式弹卡）。"""
        from capabilities.video.skills.talking_head_overlay import OverlayInput

        assert OverlayInput.model_fields["decor_effect"].default == "pop"

    @pytest.mark.asyncio
    async def test_finalize_decor_trigger_and_sfx(self, tmp_path):
        """_finalize_decor 集成：触发式卡窗 + SFX 混音接进合成命令。"""
        import json
        from unittest.mock import patch

        from capabilities.video.skills.talking_head_overlay import (
            OverlayInput,
            TalkingHeadOverlaySkill,
        )

        slides = [
            {
                "title": "A",
                "slide_type": "points",
                "bullets": [],
                "trigger": {"text": "老板推单"},
                "image": None,
            },
            {
                "title": "B",
                "slide_type": "points",
                "bullets": [],
                "trigger": {"text": "不赚钱"},
                "image": None,
            },
        ]
        sj = tmp_path / "slides.json"
        sj.write_text(
            json.dumps({"theme": "midnight", "slides": slides}, ensure_ascii=False),
            encoding="utf-8",
        )
        imgs = []
        for i in range(2):
            p = tmp_path / f"img{i}.png"
            p.write_bytes(b"x")
            imgs.append(str(p))

        async def fake_probe(self_, path):
            return 720, 1280

        async def fake_audio(self_, path):
            return True

        def fake_cards(slides_, out_dir, theme=None):
            out_dir = Path(out_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            ps = []
            for i in range(len(slides_)):
                q = out_dir / f"c{i}.png"
                q.write_bytes(b"x")
                ps.append(q)
            return ps

        def fake_kao(frames, rows_, out_dir, width=720):
            out_dir = Path(out_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            ps = []
            for i in range(len(frames)):
                q = out_dir / f"k{i}.png"
                q.write_bytes(b"x")
                ps.append(q)
            return ps

        async def fake_ff(cmd):
            Path(cmd[-1]).write_bytes(b"x")
            return ""

        with (
            patch.object(
                TalkingHeadOverlaySkill,
                "_probe_resolution",
                fake_probe,
            ),
            patch.object(
                TalkingHeadOverlaySkill,
                "_probe_has_audio",
                fake_audio,
            ),
            patch(
                "capabilities.video.services.deck_orchestrator." "render_decor_cards",
                fake_cards,
            ),
            patch(
                "capabilities.video.skills.talking_head_overlay." "_render_karaoke_frames",
                fake_kao,
            ),
            patch(
                "capabilities.video.skills.smart_cut._run_ffmpeg",
                side_effect=fake_ff,
            ),
        ):
            events = []
            async for ev in TalkingHeadOverlaySkill()._finalize_decor(
                OverlayInput(video_path="v.mp4", overlay_decor=True),
                tmp_path / "v.mp4",
                5.0,
                tmp_path,
                tmp_path,
                images=imgs,
                slides_json=sj,
                transcript_rows=self.ROWS,
            ):
                events.append(ev)
        done = next(e for e in events if e.get("event") == "decor_done")
        wins = [tuple(w) for w in done["data"]["windows"]]
        assert wins[0] == (0.0, 2.0)  # 老板推单 @0.0
        # 不赚钱 @3.4 → +min(2.5, 2.5)=2.5，end 夹 duration=5.0
        assert wins[1] == (3.4, 5.0)
        assert done["data"]["sfx"]  # 音效已混（pop@3.4）


class TestSfx:
    """M2-P1-4 SFX 音效层 — 免版权短音效库 + adelay/amix 混音 +
    缺文件降级直通。"""

    def test_sfx_assets_exist(self):
        """6 个免版权音效资产在库（ffmpeg 合成，零外部版权）。"""
        from capabilities.video.skills.talking_head_overlay import (
            _SFX_DIR,
            _SFX_LIBRARY,
        )

        assert _SFX_LIBRARY == [
            "pop",
            "whoosh",
            "ding",
            "switch",
            "riser",
            "impact",
        ]
        for n in _SFX_LIBRARY:
            p = _SFX_DIR / f"{n}.wav"
            assert p.is_file() and p.stat().st_size > 0, n

    def test_plan_sfx_rotation(self):
        """卡入场时刻 → SFX 事件：库轮换不重复、≤6 个、t=0 首卡不弹。"""
        from capabilities.video.skills.talking_head_overlay import _plan_sfx

        bounds = [(0.0, 2.0), (1.5, 3.0), (2.5, 5.0)]
        evs = _plan_sfx(bounds, 5.0)
        assert [e["t"] for e in evs] == [1.5, 2.5]
        assert len({e["name"] for e in evs}) == len(evs)
        many = [(float(i), float(i + 1)) for i in range(10)]
        assert len(_plan_sfx(many, 20.0)) <= 6

    def test_build_sfx_mix(self):
        """混音命令：aformat 48k 立体声 + adelay=ms + volume=0.18 +
        amix normalize=0（主音量不被均分衰减）。"""
        from capabilities.video.skills.talking_head_overlay import (
            _SFX_DIR,
            _build_sfx_mix,
        )

        events = [{"name": "pop", "t": 1.5}, {"name": "ding", "t": 3.0}]
        paths, parts, label, meta = _build_sfx_mix(events, _SFX_DIR)
        assert len(paths) == 2
        j = ";".join(parts)
        assert "adelay=1500" in j and "adelay=3000" in j
        assert "volume=0.18" in j
        assert "amix=inputs=3:duration=first:normalize=0" in j
        assert "sample_rates=48000" in j
        assert label == "[aout]"
        assert meta["mixed"] == 2

    def test_build_sfx_mix_missing_file_degrades(self, tmp_path):
        """缺文件/空事件 → 无混音输出（调用方音频直通，不阻断）。"""
        from capabilities.video.skills.talking_head_overlay import (
            _SFX_DIR,
            _build_sfx_mix,
        )

        p, parts, label, meta = _build_sfx_mix([{"name": "pop", "t": 1.0}], tmp_path)
        assert p == [] and label is None
        p2, _p2, l2, _m2 = _build_sfx_mix([], _SFX_DIR)
        assert p2 == [] and l2 is None

    def test_build_decor_cmd_with_sfx(self):
        """SFX 接进合成命令：输入排在字幕流之后，-map 切 [aout]。"""
        from capabilities.video.skills.talking_head_overlay import (
            _SFX_DIR,
            _build_decor_cmd,
        )

        cmd, meta = _build_decor_cmd(
            Path("v.mp4"),
            [Path("c0.png")],
            [(1.0, 5.0)],
            5.0,
            720,
            1280,
            subtitle_ffconcat=Path("k/list.txt"),
            sfx_events=[{"name": "pop", "t": 1.0}],
            sfx_dir=_SFX_DIR,
        )
        assert cmd.count("-i") == 4  # 视频+卡+字幕+sfx
        joined = " ".join(cmd)
        assert "adelay=1000" in joined
        assert "-map" in joined and "[aout]" in joined
        assert meta["sfx"]["mixed"] == 1

    def test_build_decor_cmd_without_sfx_passthrough(self):
        """无 SFX 事件 → 音频直通 0:a?（M1 行为不变）。"""
        from capabilities.video.skills.talking_head_overlay import (
            _build_decor_cmd,
        )

        cmd, meta = _build_decor_cmd(
            Path("v.mp4"),
            [Path("c0.png")],
            [(1.0, 5.0)],
            5.0,
            720,
            1280,
        )
        assert "[aout]" not in " ".join(cmd)
        assert meta.get("sfx") in (None, {}) or not meta["sfx"]


class TestTriggerSlides:
    """N1-P0：触发翻页 — 顺序扫描 + 单页句边界降级（10.31 live16
    实证改契约：miss 页兜底而非整体回退；全 miss 才 None）。"""

    ROWS = [
        {
            "start": 0.0,
            "end": 1.2,
            "words": [
                {"text": "今天", "start": 0.0, "end": 0.4},
                {"text": "讲讲", "start": 0.5, "end": 1.2},
            ],
        },
        {
            "start": 2.0,
            "end": 3.0,
            "words": [
                {"text": "老板", "start": 2.0, "end": 2.4},
                {"text": "推单", "start": 2.5, "end": 2.9},
            ],
        },
        {
            "start": 4.0,
            "end": 5.0,
            "words": [
                {"text": "体验", "start": 4.0, "end": 4.4},
                {"text": "崩了", "start": 4.5, "end": 4.9},
            ],
        },
    ]

    def test_trigger_slide_starts_all_hit(self):
        from capabilities.video.skills.talking_head_overlay import (
            _trigger_slide_starts,
        )

        slides = [
            {"trigger": {"text": "今天讲讲"}},
            {"trigger": {"text": "老板推单"}},
            {"trigger": {"text": "体验崩了"}},
        ]
        t0, durs = _trigger_slide_starts(self.ROWS, slides, 12.0)
        assert t0 == 0.0
        assert durs == [2.0, 2.0, 8.0]  # [0→2, 2→4, 4→12]

    def test_trigger_slide_starts_fallback(self):
        from capabilities.video.skills.talking_head_overlay import (
            _trigger_slide_starts,
        )

        # 中间页 miss → 该页句边界兜底（spans[1].start=2.0，结果与
        # 全命中一致）；其余页仍走触发点
        slides = [
            {"trigger": {"text": "今天讲讲"}},
            {},
            {"trigger": {"text": "体验崩了"}},
        ]
        assert _trigger_slide_starts(self.ROWS, slides, 12.0) == (
            0.0,
            [2.0, 2.0, 8.0],
        )
        # 乱序命中（顺序扫描后 miss）→ 句边界兜底 + 单调钳制
        slides = [
            {"trigger": {"text": "体验崩了"}},
            {"trigger": {"text": "老板推单"}},
        ]
        assert _trigger_slide_starts(self.ROWS, slides, 12.0) == (
            4.0,
            [0.5, 7.5],
        )
        # live16 场景：末页触发词只在前文（页 0 命中区）出现过 →
        # 顺序扫描 miss → 末页句边界兜底，计划仍成立
        slides = [
            {"trigger": {"text": "老板推单"}},
            {"trigger": {"text": "今天讲讲"}},
        ]
        hit = _trigger_slide_starts(self.ROWS, slides, 12.0)
        assert hit is not None and hit[0] == 2.0
        # 全部页 miss → None（回退句边界）
        slides = [{"trigger": {"text": "不存在词"}}]
        assert _trigger_slide_starts(self.ROWS, slides, 12.0) is None
        assert _trigger_slide_starts(self.ROWS, [], 12.0) is None


class TestHandoff:
    """N1-P0：开场全屏 → t0 缩小让位（纯函数三件套）。"""

    def test_handoff_plan(self):
        from capabilities.video.skills.talking_head_overlay import (
            _handoff_plan,
        )

        plan = _handoff_plan(5.0, 32.0)
        assert plan == {
            "t0": 5.0,
            "td": 0.4,
            "segA": (0.0, 5.9),  # 多截 0.5s 余量防 xfade 末帧差
            "segB": (5.0, 32.0),
        }
        # t0 过早 / 段B 过短（> dur-2）→ None（走现行为）
        assert _handoff_plan(0.0, 32.0) is None
        assert _handoff_plan(31.0, 32.0) is None

    def test_handoff_cmd(self):
        from capabilities.video.skills.talking_head_overlay import (
            _handoff_cmd,
        )

        argv = _handoff_cmd(
            Path("a.mp4"),
            Path("b.mp4"),
            5.0,
            0.4,
            Path("o.mp4"),
        )
        s = " ".join(argv)
        assert "xfade" in s and "offset=5.000" in s and "duration=0.4" in s
        assert "acrossfade=d=0.4" in s
        assert "a.mp4" in s and "b.mp4" in s and "o.mp4" in s


class TestZoom:
    """M3-P1-5 强调 zoom 系统 — emphasis 契约 → 词流定位 → zoompan 链。

    量化规则（方案）：1.08-1.10x / 0.35-0.45s / ≤5 次 / 数字优先；
    曲线 sqrt ease-in 30% 峰值 + quadratic 回落；找不到绝不阻断。
    """

    ROWS = [
        {
            "start": 0.0,
            "end": 12.0,
            "text": "成本降了三倍，体验稳定转化率翻番",
            "words": [
                {"text": "成本", "start": 0.0, "end": 0.4},
                {"text": "降了", "start": 0.4, "end": 0.9},
                {"text": "三倍", "start": 1.0, "end": 1.6},
                {"text": "体验", "start": 2.0, "end": 2.4},
                {"text": "稳定", "start": 2.5, "end": 3.0},
                {"text": "转化率", "start": 5.0, "end": 5.9},
                {"text": "翻番", "start": 6.0, "end": 6.4},
            ],
        }
    ]

    def test_find_trigger_span_cross_word(self):
        """span：跨词拼接命中 → (首词 start, 末词 end)；未命中 None。"""
        from capabilities.video.skills.talking_head_overlay import (
            _find_trigger_span,
        )

        assert _find_trigger_span(self.ROWS, "三倍") == (1.0, 1.6)
        assert _find_trigger_span(self.ROWS, "成本降了三倍") == (0.0, 1.6)
        assert _find_trigger_span(self.ROWS, "不存在词") is None

    def test_zoom_windows_number_priority_sorted_dedup(self):
        """number 优先、keyword 补位、同起点去重、按时间排序。"""
        from capabilities.video.skills.talking_head_overlay import (
            _zoom_windows,
        )

        slides = [
            {
                "bullets": [
                    {"text": "成本降了三倍", "emphasis": "number"},
                    {"text": "体验稳定", "emphasis": "keyword"},
                ]
            },
            {
                "bullets": [
                    {"text": "转化率翻番", "emphasis": "number"},
                    {"text": "不存在词", "emphasis": "keyword"},
                ]
            },
            {
                "bullets": [
                    {"text": "成本降了三倍", "emphasis": "keyword"},  # 重复起点
                ]
            },
        ]
        wins = _zoom_windows(slides, self.ROWS, 12.0)
        starts = [w[0] for w in wins]
        assert starts == sorted(starts)
        assert len(starts) == len(set(starts))
        assert wins[0] == (0.0, 0.45)  # 「成本降了三倍」整句 span
        assert wins[0][0] == 0.0  # number 优先
        assert (5.0, 5.45) in wins  # 转化率翻番（整句 span）
        assert (2.0, 2.45) in wins  # 体验稳定（keyword 补位）

    def test_relaxed_span_ngram_fallback(self):
        """live15 实证：提炼语措辞漂移 → 整句失手后 4 字滑窗容错。"""
        from capabilities.video.skills.talking_head_overlay import (
            _relaxed_span,
        )

        # 换尾词：「成本降了呀」整句不在词流，滑窗「成本降了」命中
        assert _relaxed_span(self.ROWS, "成本降了呀") == (0.0, 0.9)
        # 标点包裹的 4 字片段直接命中
        assert _relaxed_span(self.ROWS, "，体验稳定。") == (2.0, 3.0)
        # 全新措辞（插词/换词）→ None（该候选放弃，绝不阻断）
        assert _relaxed_span(self.ROWS, "完全不存在词") is None

    def test_zoom_windows_uses_relaxed(self):
        """_zoom_windows 消费 relaxed 命中 — 提炼语也能弹 zoom。"""
        from capabilities.video.skills.talking_head_overlay import (
            _zoom_windows,
        )

        slides = [
            {
                "bullets": [
                    {"text": "成本降了呀", "emphasis": "keyword"},
                    {"text": "完全不存在词", "emphasis": "number"},  # miss 跳过
                ]
            }
        ]
        assert _zoom_windows(slides, self.ROWS, 12.0) == [(0.0, 0.45)]

    def test_zoom_windows_window_clamp(self):
        """窗长：短词 ≥ normal(0.4)，长词 ≤ _ZOOM_MAX(0.45)。"""
        from capabilities.video.skills.talking_head_overlay import (
            _zoom_windows,
        )

        slides = [{"bullets": [{"text": "三倍", "emphasis": "number"}]}]
        wins = _zoom_windows(slides, self.ROWS, 12.0)
        assert wins == [(1.0, 1.45)]  # 跨度 0.6 → min(0.45) 封顶
        rows2 = [{"words": [{"text": "稳", "start": 3.0, "end": 3.1}]}]
        slides2 = [{"bullets": [{"text": "稳", "emphasis": "keyword"}]}]
        wins2 = _zoom_windows(slides2, rows2, 12.0)
        assert wins2 == [(3.0, 3.4)]  # 短词 → normal 0.4 基准

    def test_zoom_windows_nearby_dedup(self):
        """相邻窗间隔 <0.5s → 保首个（杜绝连环推近）。"""
        from capabilities.video.skills.talking_head_overlay import (
            _zoom_windows,
        )

        rows2 = [
            {
                "words": [
                    {"text": "三倍", "start": 1.0, "end": 1.6},
                    {"text": "四倍", "start": 1.7, "end": 2.0},
                ]
            }
        ]
        slides = [
            {
                "bullets": [
                    {"text": "三倍", "emphasis": "keyword"},
                    {"text": "四倍", "emphasis": "keyword"},
                ]
            }
        ]
        wins = _zoom_windows(slides, rows2, 12.0)
        assert wins == [(1.0, 1.45)]

    def test_zoom_windows_cap5_and_empty(self):
        """≤5 处上限；全部搜不到 → []（绝不阻断）。"""
        from capabilities.video.skills.talking_head_overlay import (
            _zoom_windows,
        )

        assert (
            _zoom_windows(
                [{"bullets": [{"text": "不存在词", "emphasis": "keyword"}]}],
                self.ROWS,
                12.0,
            )
            == []
        )
        rows2 = [
            {
                "words": [
                    {"text": f"w{i}", "start": float(i) * 3.0, "end": float(i) * 3.0 + 0.8}
                    for i in range(8)
                ]
            }
        ]
        slides = [{"bullets": [{"text": f"w{i}", "emphasis": "keyword"} for i in range(8)]}]
        assert len(_zoom_windows(slides, rows2, 40.0)) == 5

    def test_zoom_curve_math(self):
        """曲线：sqrt ease-in 至 30% 峰值(0.08)，quadratic 回落，边界归零。"""
        from capabilities.video.skills.talking_head_overlay import (
            _zoom_curve,
        )

        assert _zoom_curve(0.0) == 0.0
        assert _zoom_curve(1.0) == 0.0
        assert abs(_zoom_curve(0.3) - 0.08) < 1e-9
        assert abs(_zoom_curve(0.65) - 0.08 * (1 - 0.5) ** 2) < 1e-9
        assert 0 < _zoom_curve(0.15) < _zoom_curve(0.3)
        assert _zoom_curve(0.8) < _zoom_curve(0.5) < _zoom_curve(0.3)

    def test_zoompan_expr_frames_and_centering(self):
        """zoompan 参数：帧号 S/E 换算、多窗求和、居中 x/y、显式 fps。"""
        from capabilities.video.skills.talking_head_overlay import (
            _zoompan_expr,
        )

        assert _zoompan_expr([], 30, 720, 1280) is None
        e = _zoompan_expr([(1.0, 1.45)], 30, 720, 1280)
        assert e is not None
        assert "between(in,30,44)" in e  # round(1.0*30), round(1.45*30)
        assert "sqrt(" in e and "pow(" in e
        assert "x='iw/2-(iw/zoom/2)'" in e
        assert "y='ih/2-(ih/zoom/2)'" in e
        assert "s=720x1280" in e and "fps=30" in e and "d=1" in e
        e2 = _zoompan_expr([(1.0, 1.45), (6.0, 6.45)], 30, 720, 1280)
        assert "between(in,30,44)" in e2 and "between(in,180,194)" in e2

    def test_build_decor_cmd_zoom_chain(self):
        """zoom_windows → [0:v]zoompan 在所有 overlay 之前；meta 记录。"""
        from capabilities.video.skills.talking_head_overlay import (
            _build_decor_cmd,
        )

        cmd, meta = _build_decor_cmd(
            Path("v.mp4"),
            [Path("c.png")],
            [(1.0, 5.0)],
            5.0,
            720,
            1280,
            zoom_windows=[(1.0, 1.45)],
        )
        joined = " ".join(cmd)
        assert "[0:v]zoompan=" in joined
        assert joined.index("zoompan=") < joined.index("overlay")
        assert meta["zoom"] == [(1.0, 1.45)]

    def test_build_decor_cmd_no_zoom_passthrough(self):
        """无 zoom 窗 → 无 zoompan（M2 行为不变）。"""
        from capabilities.video.skills.talking_head_overlay import (
            _build_decor_cmd,
        )

        cmd, meta = _build_decor_cmd(
            Path("v.mp4"),
            [Path("c.png")],
            [(1.0, 5.0)],
            5.0,
            720,
            1280,
        )
        assert "zoompan" not in " ".join(cmd)
        assert meta["zoom"] == []

    @pytest.mark.asyncio
    async def test_probe_video_fps_fallback(self, tmp_path):
        """帧率探测：文件不存在/PyAV 失败 → 回退 30（绝不阻断）。"""
        from capabilities.video.skills.talking_head_overlay import (
            _probe_video_fps,
        )

        assert await _probe_video_fps(str(tmp_path / "nope.mp4")) == 30.0


class TestFitText:
    """M3-P2-6 fitText — 行宽预算反解字号（全角当量估算，两行制）。"""

    def test_short_text_caps(self):
        from capabilities.video.skills.talking_head_overlay import (
            _fit_font_px,
        )

        assert _fit_font_px("你好", 720) == 46

    def test_long_text_shrinks_two_lines(self):
        from capabilities.video.skills.talking_head_overlay import (
            _fit_font_px,
        )

        px = _fit_font_px("一" * 30, 720)
        assert px <= 46
        assert 30 * px <= 2 * (720 - 56)

    def test_floor_keeps_readable(self):
        from capabilities.video.skills.talking_head_overlay import (
            _fit_font_px,
        )

        assert _fit_font_px("一" * 60, 576) == 24

    def test_halfwidth_discount(self):
        from capabilities.video.skills.talking_head_overlay import (
            _fit_font_px,
        )

        assert _fit_font_px("1" * 30, 720) > _fit_font_px("一" * 30, 720)

    def test_display_data_uses_fit(self):
        from capabilities.video.skills.talking_head_overlay import (
            _fit_font_px,
            _karaoke_display_data,
        )

        text = "一" * 40
        data = _karaoke_display_data(
            [{"text": text, "words": [{"text": "一"}]}],
            width=576,
        )
        assert data[0]["px"] == _fit_font_px(text, 576)


class TestMotionTokens:
    """M3-P2-7 motion tokens 单点定义 — 动效时长同源引用。"""

    def test_values(self):
        from capabilities.video.skills.talking_head_overlay import _MOTION

        assert _MOTION == {"fast": 0.2, "normal": 0.4, "stagger": 0.13}

    def test_decor_fade_uses_token(self):
        from capabilities.video.skills.talking_head_overlay import (
            _DECOR_FADE,
            _MOTION,
        )

        assert _DECOR_FADE == _MOTION["normal"]


class TestSafeMargin:
    """M3-P2-6 安全区常量 — 80px @1080 宽折算，下限 24。"""

    def test_reference_scale(self):
        from capabilities.video.skills.talking_head_overlay import (
            _safe_margin,
        )

        assert _safe_margin(1080) == 80
        assert _safe_margin(576) == 43

    def test_floor(self):
        from capabilities.video.skills.talking_head_overlay import (
            _safe_margin,
        )

        assert _safe_margin(100) == 24

    def test_decor_cmd_uses_safe_margin(self):
        from capabilities.video.skills.talking_head_overlay import (
            _build_decor_cmd,
        )

        cmd, _ = _build_decor_cmd(
            Path("v.mp4"),
            [Path("c.png")],
            [(1.0, 5.0)],
            5.0,
            1080,
            1920,
        )
        # max(_safe_margin(1080)=80, 1920*5%=96) = 96
        assert "overlay=(W-w)/2:96" in " ".join(cmd)


class TestDeckTopClearance:
    """N2：deck 主画面顶部右侧让位 — 小窗压标题修复（live16 用户
    裁决反馈）。top_right_clear_px>0 → h1/quote-title 右让 +
    页码 idx 移左上；缺省 0 行为不变。"""

    BULLETS = [
        {"text": "真没空：存单或直接送掉，不会退", "emphasis": "keyword"},
    ]

    def test_slide_html_top_clear(self):
        from capabilities.video.services.deck_orchestrator import _slide_html

        html = _slide_html(
            "真没空的老板只会存单",
            self.BULLETS,
            2,
            6,
            top_right_clear_px=350,
        )
        assert "margin-right:350px" in html  # h1 右让
        assert "left:110px" in html  # 页码移左上
        assert "right:110px" not in html  # 旧右上页码消失

    def test_slide_html_top_clear_quote(self):
        from capabilities.video.services.deck_orchestrator import _slide_html

        html = _slide_html(
            "金句标题",
            self.BULLETS,
            3,
            6,
            slide_type="quote",
            top_right_clear_px=350,
        )
        assert "margin-right:350px" in html  # quote-title 右让
        assert "left:110px" in html

    def test_slide_html_default_no_clear(self):
        from capabilities.video.services.deck_orchestrator import _slide_html

        html = _slide_html("标题", self.BULLETS, 1, 6)
        assert "margin-right:" not in html
        assert "right:110px" in html  # 页码仍在右上（现行为）

    def test_deck_top_clear_px_helper(self):
        from capabilities.video.skills.talking_head_overlay import (
            PipPosition,
            _deck_top_clear_px,
        )

        class P:  # 最小 parsed 桩
            pip_position = PipPosition.TOP_RIGHT
            pip_scale = 0.28

        # 1080*0.28=302 + 双边距 40 ≈ 342 → 上取整到 40 倍数 360
        assert _deck_top_clear_px(P()) == 360

        class PBottom(P):
            pip_position = PipPosition.BOTTOM_RIGHT

        assert _deck_top_clear_px(PBottom()) == 0  # 底部小窗不让位


class TestKenburns:
    """N4：Ken Burns 缓推 — 消幻灯片静止僵硬感（用户裁决反馈）。

    每页 xfade 链输入 pre 后追加：2x upscale 防 zoompan 抖动 →
    zoompan 居中缓推/拉远（振幅 6%，按页时长帧数线性）→ 奇偶页
    方向交替。默认关（kenburns=False 现行为不变）。"""

    def test_kenburns_filter_zoom_in(self):
        from capabilities.video.skills.talking_head_overlay import (
            _kenburns_filter,
        )

        f = _kenburns_filter(5.0, 30, 1080, 1920, zoom_in=True)
        assert "scale=iw*2:ih*2" in f  # 防抖 upscale
        assert "zoompan=" in f
        assert "1+0.06*on/150" in f  # 5s*30fps=150 帧线性推近
        assert "s=1080x1920" in f  # 输出回主画布尺寸
        assert "fps=30" in f

    def test_kenburns_filter_zoom_out(self):
        from capabilities.video.skills.talking_head_overlay import (
            _kenburns_filter,
        )

        f = _kenburns_filter(3.0, 30, 1080, 1920, zoom_in=False)
        assert "1.06-0.06*on/90" in f  # 拉远：1.06 → 1.00
        assert "zoompan=" in f

    def test_kenburns_filter_directional_center(self):
        from capabilities.video.skills.talking_head_overlay import (
            _kenburns_filter,
        )

        f = _kenburns_filter(2.0, 30, 720, 1280, zoom_in=True)
        assert "x='iw/2-(iw/zoom/2)'" in f  # 居中防漂移
        assert "y='ih/2-(ih/zoom/2)'" in f
        assert "s=720x1280" in f

    def test_xfade_cmd_kenburns_on(self):
        from pathlib import Path as _P

        from capabilities.video.skills.talking_head_overlay import (
            _build_xfade_cmd,
        )

        shots = [_P(f"s{i}.png") for i in range(3)]
        cmd, meta = _build_xfade_cmd(
            shots,
            _P("o.mp4"),
            [4.0, 4.0, 4.0],
            fps=30,
            width=1080,
            height=1920,
            kenburns=True,
        )
        fc = cmd[cmd.index("-filter_complex") + 1]
        assert fc.count("zoompan=") == 3  # 每输入一档
        assert meta["kb"] == [True, False, True]  # 方向交替（推/拉/推）

    def test_xfade_cmd_kenburns_default_off(self):
        from pathlib import Path as _P

        from capabilities.video.skills.talking_head_overlay import (
            _build_xfade_cmd,
        )

        shots = [_P(f"s{i}.png") for i in range(2)]
        cmd, _ = _build_xfade_cmd(
            shots,
            _P("o.mp4"),
            [4.0, 4.0],
            fps=30,
            width=1080,
            height=1920,
        )
        fc = cmd[cmd.index("-filter_complex") + 1]
        assert "zoompan=" not in fc  # 现行为不变


class TestDeckLayers:
    """N5：deck 元素分层动画 — 文字动起来（用户裁决：Ken Burns
    整页晃动否决；改为 bg/标题/逐条要点分层，淡入+36px 上滑落定，
    要点 stagger 0.35s 逐条入场；页间 xfade 保持）。"""

    SLIDE = {
        "title": "真没空的老板不会退单",
        "slide_type": "points",
        "bullets": [
            {"text": "真没空：存单或直接送掉", "emphasis": "keyword"},
            {"text": "会退：在你这待着难受", "emphasis": "none"},
        ],
    }

    def test_layers_plan_points(self):
        from capabilities.video.services.deck_orchestrator import (
            _slide_layers_plan,
        )

        plan = _slide_layers_plan(self.SLIDE)
        assert [l["name"] for l in plan] == ["bg", "head", "b0", "b1"]
        assert plan[1]["t0"] == 0.10  # 标题先落
        assert plan[2]["t0"] == 0.45  # 要点 stagger
        assert plan[3]["t0"] == 0.80
        assert all(l["y0"] == 36 for l in plan[1:])

    def test_layers_plan_non_points_single(self):
        from capabilities.video.services.deck_orchestrator import (
            _slide_layers_plan,
        )

        plan = _slide_layers_plan({**self.SLIDE, "slide_type": "quote"})
        assert [l["name"] for l in plan] == ["page"]  # 非要点页整页单层

    def test_slide_html_only_bg(self):
        from capabilities.video.services.deck_orchestrator import _slide_html

        html = _slide_html(
            self.SLIDE["title"],
            self.SLIDE["bullets"],
            2,
            5,
            only_layer="bg",
        )
        assert html.count("visibility:hidden") >= 3  # idx+title+bullets 隐
        assert 'class="total"' in html  # 页脚留 bg 层

    def test_slide_html_only_b1(self):
        from capabilities.video.services.deck_orchestrator import _slide_html

        html = _slide_html(
            self.SLIDE["title"],
            self.SLIDE["bullets"],
            2,
            5,
            only_layer="b1",
        )
        # body 透明（文字层透明底截图）
        assert "background:transparent" in html
        assert html.count("visibility:hidden") >= 3  # title+idx+另一条
        # 第二条要点可见（无 hidden）
        seg = html.split("会退：在你这待着难受")[0]
        assert seg.rfind("visibility:hidden") < seg.rfind("<p")

    def test_build_layers_cmd(self):
        from pathlib import Path as _P

        from capabilities.video.skills.talking_head_overlay import (
            _build_layers_cmd,
        )

        pages = [
            [
                {"png": _P(f"p0_{j}.png"), "t0": t0, "dur": 0.3, "y0": 36}
                for j, t0 in enumerate((0.0, 0.10, 0.45))
            ],
            [
                {"png": _P(f"p1_{j}.png"), "t0": t0, "dur": 0.3, "y0": 36}
                for j, t0 in enumerate((0.0, 0.10))
            ],
        ]
        cmd, meta = _build_layers_cmd(
            pages,
            _P("o.mp4"),
            [6.0, 4.0],
            fps=30,
            width=1080,
            height=1920,
        )
        assert cmd.count("-i") >= 5  # 每层一输入
        fc = cmd[cmd.index("-filter_complex") + 1]
        assert "fade=t=in:st=0.10" in fc  # 两页标题均页内 0.10 入场
        assert "fade=t=in:st=0.45" in fc  # 页 0 要点 stagger
        # 层 t0 = 页内相对时刻（xfade 重基第二输入到 offset — 输入 PTS
        # 0-based 即页内时间；live20 探针实证 page_start 锚定反而迟到
        # page_start，且短页 e_i < tg 时 fade 永不触发 → 整页无字）
        assert "fade=t=in:st=6.10" not in fc  # 不锚定页起点
        assert "1-(t-0.10)" in fc  # 页 1 标题 y 表达式（页内）
        assert "overlay=0:" in fc and "36*max(0" in fc  # 上滑落定
        assert "xfade=transition=" in fc  # 页间转场保持
        assert meta["layers_per_page"] == [3, 2]

    def test_pip_full_cmd_handoff_align(self):
        """N1 对齐：handoff 时 slides 前置 t0 纯色底 concat（页链 τ0 ↔ t0）。"""
        from pathlib import Path as _P

        from capabilities.video.skills.talking_head_overlay import _pip_full_cmd

        cmd = _pip_full_cmd(
            _P("slides.mp4"),
            30.0,
            _P("frames"),
            _P("src.mp4"),
            1080,
            1920,
            302,
            536,
            758,
            20,
            duration=30.19,
            bg_hex="1a1a2e",
            handoff_t0=7.0,
            output_path=_P("pip_full.mp4"),
        )
        joined = " ".join(cmd)
        assert "color=c=0x1a1a2e:s=1080x1920:d=7.000" in joined
        fc = cmd[cmd.index("-filter_complex") + 1]
        assert "concat=n=2:v=1:a=0" in fc
        assert "[main_v][pip]overlay=" in fc
        assert cmd[cmd.index("-map", cmd.index("-map") + 1) + 1] == "3:a?"
        # 无 handoff：slides 从 0 直放（现行为不回归）
        cmd2 = _pip_full_cmd(
            _P("slides.mp4"),
            30.0,
            _P("frames"),
            _P("src.mp4"),
            1080,
            1920,
            302,
            536,
            758,
            20,
            duration=30.19,
            bg_hex="1a1a2e",
            handoff_t0=None,
            output_path=_P("pip_full.mp4"),
        )
        assert "color=c=" not in " ".join(cmd2)
        fc2 = cmd2[cmd2.index("-filter_complex") + 1]
        assert "concat" not in fc2
        assert "[0:v][pip]overlay=" in fc2

    def test_build_layers_cmd_single_page(self):
        from pathlib import Path as _P

        from capabilities.video.skills.talking_head_overlay import (
            _build_layers_cmd,
        )

        pages = [
            [
                {"png": _P("p0_0.png"), "t0": 0.0, "dur": 0.0, "y0": 0},
                {"png": _P("p0_1.png"), "t0": 0.10, "dur": 0.3, "y0": 36},
            ]
        ]
        cmd, _ = _build_layers_cmd(
            pages,
            _P("o.mp4"),
            [5.0],
            fps=30,
            width=1080,
            height=1920,
        )
        fc = cmd[cmd.index("-filter_complex") + 1]
        assert "xfade" not in fc  # 单页无转场
        assert "overlay=0:" in fc


class TestTypewriter:
    """N6：文字流式输出（打字机）— 条间错峰节奏不变（与层计划 t0
    同源），条内文字逐字浮现（JS 确定性驱动 + Playwright 逐帧截图 →
    帧序列进 xfade 链）。降级链：打字机 → 层动画 → 整页。"""

    SLIDE = {
        "title": "真没空",
        "slide_type": "points",
        "bullets": [
            {"text": "第一条要点", "emphasis": "keyword"},
            {"text": "会退：在你这待着难受", "emphasis": "none"},
        ],
    }

    def test_typewriter_plan_points(self):
        from capabilities.video.services.deck_orchestrator import (
            _typewriter_plan,
        )

        plan = _typewriter_plan(self.SLIDE)
        assert plan["head"] == {"t0": 0.10, "chars": True}
        assert plan["b0"]["t0"] == 0.45 and plan["b1"]["t0"] == 0.80
        assert plan["b0"]["chars"] is True  # keyword/none 要点逐字
        # number 渐变字整块淡入（background-clip:text 逐字透明度失效）
        plan2 = _typewriter_plan(
            {
                "title": "T",
                "slide_type": "points",
                "bullets": [{"text": "30%", "emphasis": "number"}],
            }
        )
        assert plan2["b0"]["chars"] is False

    def test_typewriter_plan_non_points_none(self):
        from capabilities.video.services.deck_orchestrator import (
            _typewriter_plan,
        )

        assert (
            _typewriter_plan(
                {**self.SLIDE, "slide_type": "quote"},
            )
            is None
        )

    def test_typewriter_cps_short_page_speedup(self):
        """短页流不完 → 按比例提速（片尾文字不截断）；长页不提速。"""
        from capabilities.video.services.deck_orchestrator import (
            _typewriter_cps,
            _typewriter_plan,
            _typewriter_t_done,
        )

        slide = {
            "title": "T",
            "slide_type": "points",
            "bullets": [{"text": "一二三四五六七八九十十一十二十三十四", "emphasis": "none"}],
        }
        plan = _typewriter_plan(slide)
        assert _typewriter_cps(plan, slide, 10.0) == 9.0  # 长页基准速
        short = _typewriter_cps(plan, slide, 1.0)
        assert short > 9.0  # 短页提速
        assert _typewriter_t_done(plan, slide, short) <= 1.0 + 1e-6
        assert _typewriter_cps(None, slide, 0.5) == 9.0  # 非 points 页

    def test_typewriter_avail_last_page_tail_drain(self):
        """末页 avail 收紧：链路尾漂移（让位 xfade 0.5 + acrossfade 0.6，
        live24 实测视频流止于 sum(d)-1.06）→ 末页按 dur-_TAIL_DRAIN
        计算可用时长并提速；非末页公式不变。"""
        from capabilities.video.services.deck_orchestrator import (
            _TAIL_DRAIN,
            _typewriter_avail,
            _typewriter_cps,
            _typewriter_plan,
            _typewriter_t_done,
        )

        assert _TAIL_DRAIN >= 1.06  # 覆盖 live24 实测漂移
        # 非末页：原公式不变
        assert _typewriter_avail(12.46, False, 0.5) == 12.46 + 0.5 - 0.3
        # 末页：扣尾部漂移
        assert _typewriter_avail(3.02, True, 0.5) == 3.02 - _TAIL_DRAIN
        # live24 实际末页（3.02s / 3 条）：提速后 t_done ≤ 收紧 avail
        slide = {
            "title": "别让老板忍到推单",
            "slide_type": "points",
            "bullets": [
                {"text": "推单不是没空，是难受", "emphasis": "keyword"},
                {"text": "团队服务模式要自查", "emphasis": "none"},
                {"text": "及时响应，别晾着客户", "emphasis": "none"},
            ],
        }
        plan = _typewriter_plan(slide)
        avail = _typewriter_avail(3.02, True, 0.5)
        cps = _typewriter_cps(plan, slide, avail)
        assert cps > 9.0, "末页 3.02s 应触发提速"
        assert _typewriter_t_done(plan, slide, cps) <= avail + 1e-6

    def test_slide_html_typewriter_marks(self):
        from capabilities.video.services.deck_orchestrator import _slide_html

        html = _slide_html(
            "真没空",
            self.SLIDE["bullets"],
            2,
            5,
            typewriter=True,
        )
        assert "window.__twSet" in html  # JS 驱动入口
        assert 'id="tw-head"' in html  # 标题逐字
        assert '<span class="tw">' in html
        assert 'id="tw-b0"' in html and 'id="twc-b0"' in html
        assert 'id="tw-b1"' in html
        assert '"chars": true' in html  # 调度 JSON 嵌入
        # tw_cps 覆盖（短页提速）注入 JS
        html_tw = _slide_html(
            "真没空",
            self.SLIDE["bullets"],
            2,
            5,
            typewriter=True,
            tw_cps=15.0,
        )
        assert "__twCps=15.0" in html_tw
        # 缺省（层渲染/整页）不带打字机标记
        html2 = _slide_html("真没空", self.SLIDE["bullets"], 2, 5)
        assert "__twSet" not in html2
        # 非 points 页不流式（短页金句流式会截断）
        html3 = _slide_html(
            "金句",
            [],
            1,
            2,
            slide_type="quote",
            typewriter=True,
        )
        assert "__twSet" not in html3

    def test_build_typewriter_cmd(self):
        from pathlib import Path as _P

        from capabilities.video.skills.talking_head_overlay import (
            _build_typewriter_cmd,
        )

        dirs = [_P(f"D:/tw/page_{i:02d}") for i in range(2)]
        cmd, meta = _build_typewriter_cmd(
            dirs,
            _P("o.mp4"),
            [2.0, 3.0],
            fps=30,
            width=1080,
            height=1920,
        )
        joined = " ".join(cmd)
        assert joined.count("%06d.png") == 2  # 每页帧序列一输入
        assert "-framerate 30" in joined
        # e_i = d_i + td 硬截（offset 契约不因帧数取整漂移）
        assert "-t 2.50" in joined and "-t 3.50" in joined
        fc = cmd[cmd.index("-filter_complex") + 1]
        assert "fade=t=in" not in fc  # 动画已在帧里，无层 fade
        assert "xfade=transition=" in fc
        assert "offset=2.00" in fc  # 前缀和
        assert meta["offsets"] == [2.0]
        assert meta["styles"]

    @pytest.mark.asyncio
    async def test_slides_to_video_frames_first(self, tmp_path):
        """frames 给定 → 打字机帧序列路径优先（先于层/整页）。"""
        from unittest.mock import AsyncMock, patch

        from capabilities.video.skills.talking_head_overlay import (
            TalkingHeadOverlaySkill,
        )

        dirs = []
        for i in range(2):
            d = tmp_path / f"page_{i:02d}"
            d.mkdir()
            (d / "000000.png").write_bytes(b"x")
            dirs.append(d)
        captured: dict = {}

        async def fake_ff(cmd):
            captured["cmd"] = cmd

        with patch(
            "capabilities.video.skills.smart_cut._run_ffmpeg",
            new=AsyncMock(side_effect=fake_ff),
        ):
            await TalkingHeadOverlaySkill()._slides_to_video(
                [Path("a.png")],
                tmp_path / "out.mp4",
                durations=[2.0, 3.0],
                frames=dirs,
                width=1080,
                height=1920,
            )

        cmd = captured["cmd"]
        assert " ".join(cmd).count("%06d.png") == 2
        fc = cmd[cmd.index("-filter_complex") + 1]
        assert "fade=t=in" not in fc
        assert "xfade=transition=" in fc and "offset=2.00" in fc

    @pytest.mark.asyncio
    async def test_typewriter_wiring_preferred_and_fallback(self, tmp_path):
        """N6 接线：打字机渲染成功 → _slides_to_video 收 frames；
        渲染失败 → 降级收 layers（三级降级链，不阻断出片）。"""
        from unittest.mock import AsyncMock, MagicMock, patch

        (tmp_path / "work").mkdir()
        jp = _write_slides_json(tmp_path)

        async def _run_once(tw_mock, ly_mock):
            patches, _, _ = TestDeckGate()._gate_patches(
                tmp_path,
                [],
                gen_mock=AsyncMock(return_value=_deck_pngs(tmp_path)),
            )
            captured: dict = {}

            def _cap_video(images, out, **kw):
                captured.update(kw)
                Path(out).write_bytes(b"fake mp4")

            patches = patches + (
                patch(
                    "capabilities.video.skills.talking_head_overlay."
                    "TalkingHeadOverlaySkill._slides_to_video",
                    new=MagicMock(side_effect=_cap_video),
                ),
                patch(
                    "capabilities.video.services.deck_orchestrator." "render_deck_typewriter",
                    new=tw_mock,
                ),
                patch(
                    "capabilities.video.services.deck_orchestrator." "render_deck_layers",
                    new=ly_mock,
                ),
            )
            await _run_skill(
                tmp_path,
                {
                    "pip": True,
                    "auto_deck": True,
                    "transcript": _deck_transcript(),
                    "gate_mode": "auto_confirm",
                    "deck_slides_json": str(jp),
                },
                patches,
            )
            return captured

        tw_dirs = [Path(f"D:/tw/page_{i:02d}") for i in range(2)]
        cap = await _run_once(
            MagicMock(return_value=tw_dirs),
            MagicMock(return_value=[]),
        )
        assert cap.get("frames") == tw_dirs, cap
        assert cap.get("layers") is None, cap

        sentinel = [["page"]]
        cap2 = await _run_once(
            MagicMock(side_effect=RuntimeError("boom")),
            MagicMock(return_value=sentinel),
        )
        assert cap2.get("frames") is None, cap2
        assert cap2.get("layers") == sentinel, cap2

    def test_render_deck_typewriter_frames(self, tmp_path):
        """真 Playwright 小页渲染：帧数 ceil((d+td)*fps)；文字完成后
        帧直接复制末帧（字节一致）；流式期间帧不同。"""
        pytest.importorskip("playwright.sync_api")

        from capabilities.video.services.deck_orchestrator import (
            render_deck_typewriter,
        )

        slide = {
            "title": "你好",
            "slide_type": "points",
            "bullets": ["第一条要点"],
        }
        dirs = render_deck_typewriter(
            [slide],
            tmp_path / "tw",
            [3.0],
            td=0.5,
            fps=10,
        )
        assert len(dirs) == 1
        frames = sorted(dirs[0].glob("*.png"))
        assert [f.name for f in frames] == [f"{j:06d}.png" for j in range(35)]
        # t_done ≈ b0: 0.45 + 5/9 + 0.08 ≈ 1.09 → j>=11 复制（t=j/10）
        done = frames[10].read_bytes()
        assert frames[20].read_bytes() == done, "文字完成帧应复制末帧"
        assert frames[34].read_bytes() == done
        assert frames[0].read_bytes() != done, "流式期间帧应有差异"
