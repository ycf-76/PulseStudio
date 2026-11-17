"""逐帧透明渲染器测试 — html_to_video v2（ADR-001）。

v2 契约：
  - Playwright 逐帧截图（omit_background=True）替代 page.video 录屏
  - 每帧调用 window.seekTo(t) 求值确定性动画状态
  - PNG 帧序列 → ffmpeg rgba 定帧率合成（替代 chromakey）
"""

from __future__ import annotations

from capabilities.video.adapters.html_to_video import build_frame_times


class TestBuildFrameTimes:
    def test_常规时长_30fps(self):
        """2.5s @30fps → 75 帧，首帧 0，末帧 < duration。"""
        times = build_frame_times(duration=2.5, fps=30)
        assert len(times) == 75
        assert times[0] == 0.0
        assert times[-1] < 2.5
        assert all(b > a for a, b in zip(times, times[1:]))

    def test_零时长_仅一帧(self):
        times = build_frame_times(duration=0.0, fps=30)
        assert times == [0.0]

    def test_负时长_不崩溃(self):
        times = build_frame_times(duration=-1.0, fps=30)
        assert times == [0.0]

    def test_帧间隔等于fps倒数(
        self,
    ):
        times = build_frame_times(duration=1.0, fps=10)
        assert abs(times[1] - times[0] - 0.1) < 1e-9


class TestEncodePreviewFrameConservation:
    """回归：_encode_preview 曾用 lavfi color 无限源 + overlay=shortest=1，
    在本机 ffmpeg 上提前 EOF（36 帧只编出 8-11 帧）→ 成片里 full_card
    等长驻留叠加被截成 ~0.3s。修复：color 源定长定率。"""

    def test_encode_帧数守恒(self, tmp_path):
        import asyncio

        import av
        from PIL import Image

        from capabilities.video.adapters.html_to_video import _encode_preview

        frames_dir = tmp_path / "frames"
        frames_dir.mkdir()
        n_frames = 36
        for i in range(n_frames):
            Image.new("RGBA", (270, 480), (255, 77, 77, 255)).save(
                frames_dir / ("frame_%06d.png" % i)
            )
        out = tmp_path / "preview.mp4"

        ok = asyncio.run(
            _encode_preview(
                frames_dir,
                out,
                fps=30,
                width=270,
                height=480,
            )
        )

        assert ok and out.is_file()
        with av.open(str(out)) as c:
            decoded = sum(1 for _ in c.decode(video=0))
        assert decoded == n_frames, f"encode 丢帧: {decoded}/{n_frames}"
