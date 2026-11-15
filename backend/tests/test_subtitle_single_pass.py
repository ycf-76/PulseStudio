"""字幕只烧一次（2026-10-17 P0）。

背景：ken-burns 中间片段以前把 shot caption 传进 compose_video，触发 _auto_srt
烧了一层字幕；这些片段再进成片，任何后续带 caption 的合成都会叠第二层，
出现两行错位的重字。规则改为：**中间产物不带字幕，成片烧一次**。
"""

import shutil
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from capabilities.video.skills.multi_image_video import _render_kenburns_clips

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


class TestKenburnsClipsCarryNoSubtitle:
    @pytest.mark.asyncio
    async def test_intermediate_clip_has_empty_caption(self, tmp_path, monkeypatch):
        """中间片段必须不带 caption —— 否则 compose_video 会自动烧一层字幕"""
        monkeypatch.setattr(
            "capabilities.video.skills.multi_image_video._OUTPUT_DIR", tmp_path
        )
        shots = [{"caption": "开场引入", "frame_url": "/uploads/a.png", "duration": 5.0}]

        with patch(
            "app.services.asset_library.url_to_local_path", return_value=str(tmp_path / "a.png")
        ), patch(
            "capabilities.video.adapters.video_compose.compose_video", new=AsyncMock()
        ) as compose:
            await _render_kenburns_clips(shots, "9:16", "u1", {"9:16": "1080x1920"})

        assert compose.called
        storyboard = compose.call_args.kwargs["storyboard"]
        assert storyboard["shots"][0]["caption"] == ""

    @pytest.mark.asyncio
    async def test_real_video_shots_are_skipped(self, tmp_path, monkeypatch):
        """已有真视频片段的镜头不参与 ken-burns 渲染，避免重复占时间轴"""
        monkeypatch.setattr(
            "capabilities.video.skills.multi_image_video._OUTPUT_DIR", tmp_path
        )
        shots = [{"clip_mode": "video", "clip_url": "/uploads/v.mp4", "caption": "c"}]

        with patch(
            "capabilities.video.adapters.video_compose.compose_video", new=AsyncMock()
        ) as compose:
            result = await _render_kenburns_clips(shots, "9:16", "u1", {"9:16": "1080x1920"})

        compose.assert_not_called()
        assert result == {}


@pytest.mark.asyncio
class TestBurnSubtitles:
    async def test_no_caption_copies_without_reencoding(self, tmp_path):
        """没有任何 caption 时不做无谓重编码，原样复制"""
        from capabilities.video.adapters.video_compose import burn_subtitles

        src = tmp_path / "in.mp4"
        src.write_bytes(b"\x00\x00\x00\x18ftypmp42")
        out = tmp_path / "out.mp4"

        result = await burn_subtitles(src, [{"caption": ""}], [5.0], out)

        assert result == out
        assert out.read_bytes() == src.read_bytes()

    async def test_missing_source_raises(self, tmp_path):
        from capabilities.video.adapters.video_compose import ComposeError, burn_subtitles

        with pytest.raises(ComposeError):
            await burn_subtitles(
                tmp_path / "nope.mp4", [{"caption": "x"}], [5.0], tmp_path / "o.mp4"
            )

    async def test_same_path_no_caption_is_noop(self, tmp_path):
        """in-place 且无字幕时不能把文件删掉"""
        from capabilities.video.adapters.video_compose import burn_subtitles

        f = tmp_path / "in.mp4"
        f.write_bytes(b"original")

        await burn_subtitles(f, [{"caption": ""}], [5.0], f)

        assert f.read_bytes() == b"original"

    @pytest.mark.skipif(not HAS_FFMPEG, reason="需要本机 ffmpeg/ffprobe")
    async def test_burns_once_with_real_ffmpeg(self, tmp_path):
        """真烧一次：产出文件存在、时长与源一致，且 Σ(durations) 与实际不符时会等比缩放"""
        import capabilities.video.adapters.video_compose as vc
        from capabilities.video.adapters.video_compose import burn_subtitles

        src = tmp_path / "in.mp4"
        await vc._run([
            "ffmpeg", "-y", "-f", "lavfi", "-i",
            "color=c=black:s=360x640:d=6",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src),
        ])
        assert src.is_file()

        out = tmp_path / "out.mp4"
        shots = [{"caption": "第一句"}, {"caption": "第二句"}]
        # 故意让 Σ(durations)=12 远大于实际 6s，触发等比缩放分支
        await burn_subtitles(src, shots, [6.0, 6.0], out, size="360x640")

        assert out.is_file() and out.stat().st_size > 0
        dur_in = await vc._probe_duration(str(src))
        dur_out = await vc._probe_duration(str(out))
        assert abs(dur_in - dur_out) < 0.5
