def test_extract_frames_counts_and_meta(tmp_path):
    # 用 ffmpeg lavfi 造 2s 30fps 测试视频
    import subprocess

    from capabilities.video.adapters.html_to_video import resolve_ffmpeg
    from capabilities.video.adapters.studio_assets import extract_frames, probe_duration

    src = tmp_path / "src.mp4"
    subprocess.run(
        [
            resolve_ffmpeg(),
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=2:size=320x568:rate=30",
            "-pix_fmt",
            "yuv420p",
            str(src),
        ],
        check=True,
        capture_output=True,
    )
    info = extract_frames(str(src), tmp_path / "frames", fps=24.0)
    assert info.count == 48 and abs(info.fps - 24.0) < 0.01
    assert info.width == 320 and info.height == 568
    assert (info.frame_dir / ("frame_%06d.jpg" % 0)).is_file()
    assert info.pattern == "frame_%06d.jpg"  # 命名模式透传（agent 断链检查前置依赖）
    assert abs(probe_duration(str(src)) - 2.0) < 0.1


# ── 底部烧录元素探测（36.9s 实跑回灌：源片自带烧录字幕与 karaoke 字幕打架）───


def _synth_frame(path, with_subtitle: bool, size=(320, 568)) -> None:
    """合成夹具帧：纯灰背景；with_subtitle=True 时底部画黑带+白色字幕条
    （模拟烧录字幕——高对比密集文本贴底）。"""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", size, (120, 120, 120))
    d = ImageDraw.Draw(img)
    if with_subtitle:
        h = size[1]
        d.rectangle([0, int(h * 0.84), size[0], int(h * 0.96)], fill=(30, 30, 30))
        d.rectangle([40, int(h * 0.87), 280, int(h * 0.895)], fill=(255, 255, 255))
        d.rectangle([60, int(h * 0.91), 260, int(h * 0.935)], fill=(255, 255, 255))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "JPEG", quality=92)


def test_probe_bottom_flags_burned_subtitle(tmp_path):
    """底部白字字幕条 → 边缘密度显著高于中部 → suspected=True。"""
    from capabilities.video.adapters.studio_assets import probe_bottom_elements

    for i in range(5):
        _synth_frame(tmp_path / ("frame_%06d.jpg" % i), with_subtitle=True)
    out = probe_bottom_elements(str(tmp_path))
    assert out["suspected"] is True
    assert out["error"] is None
    assert len(out["sampled"]) == 5
    assert out["ratio_stats"]["mean"] > 1.5


def test_probe_bottom_clean_frames_not_suspected(tmp_path):
    """纯色背景无文字 → 底部/中部密度同量级 → suspected=False。"""
    from capabilities.video.adapters.studio_assets import probe_bottom_elements

    for i in range(4):
        _synth_frame(tmp_path / ("frame_%06d.jpg" % i), with_subtitle=False)
    out = probe_bottom_elements(str(tmp_path))
    assert out["suspected"] is False
    assert out["error"] is None


def test_probe_bottom_mixed_votes_require_majority(tmp_path):
    """多帧投票：仅少数帧带烧录字幕（<60%）→ 不判疑似。"""
    from capabilities.video.adapters.studio_assets import probe_bottom_elements

    for i in range(5):
        _synth_frame(tmp_path / ("frame_%06d.jpg" % i), with_subtitle=(i < 2))
    out = probe_bottom_elements(str(tmp_path))
    assert out["suspected"] is False
    assert out["error"] is None


def test_probe_bottom_empty_dir_returns_error_not_raise(tmp_path):
    """帧轨为空 → {"suspected": False, "error": ...}，不抛异常。"""
    from capabilities.video.adapters.studio_assets import probe_bottom_elements

    out = probe_bottom_elements(str(tmp_path / "not_exist"))
    assert out["suspected"] is False
    assert isinstance(out.get("error"), str) and out["error"]
