def test_review_on_synthetic_frames(tmp_path):
    from PIL import Image

    from capabilities.video.services.studio_review import (
        completeness_check,
        contact_sheet,
        loudness_report,
    )

    fd = tmp_path / "frames"
    fd.mkdir()
    for i in range(8):
        Image.new("RGB", (320, 568), (i * 20 % 255, 0, 0)).save(fd / ("frame_%06d.png" % i))
    assert completeness_check(fd, 8) == []
    (fd / ("frame_%06d.png" % 3)).unlink()
    assert any("3" in e for e in completeness_check(fd, 8))
    sheet = contact_sheet(fd, [0.0, 0.5], fps=8, out_png=tmp_path / "sheet.png")
    assert sheet.is_file() and Image.open(sheet).size[0] >= 640
    import subprocess

    from capabilities.video.adapters.html_to_video import resolve_ffmpeg

    wav = tmp_path / "a.wav"
    subprocess.run(
        [resolve_ffmpeg(), "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1", str(wav)],
        check=True,
        capture_output=True,
    )
    rep = loudness_report(str(wav))
    assert rep["peak"] > -30 and "duration" in rep


def _make_frames(tmp_path, n):
    from PIL import Image

    fd = tmp_path / "frames"
    fd.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        Image.new("RGB", (320, 568), (i * 20 % 255, 0, 0)).save(fd / ("frame_%06d.png" % i))
    return fd


def _make_video(tmp_path, name, with_audio, duration=1):
    import subprocess

    from capabilities.video.adapters.html_to_video import resolve_ffmpeg

    src = tmp_path / name
    cmd = [
        resolve_ffmpeg(),
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=duration={duration}:size=320x568:rate=8",
    ]
    if with_audio:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-c:a", "aac"]
    cmd += ["-t", str(duration), "-pix_fmt", "yuv420p", str(src)]
    subprocess.run(cmd, check=True, capture_output=True)
    return src


_TL = {
    "fps": 8,
    "duration": 1.0,
    "canvas": {"w": 320, "h": 568},
    "speech": [],
    "beats": [{"id": "b1", "t_start": 0.0, "t_end": 1.0, "title": "开场", "visual_task": "画方块"}],
}


def test_review_workspace_happy_path(tmp_path):
    """有静音音轨 + 帧齐全 + 时长吻合 → errors 为空，产物齐备。"""
    from capabilities.video.services.studio_review import review_workspace

    fd = _make_frames(tmp_path, 8)
    src = _make_video(tmp_path, "withaudio.mp4", with_audio=True)
    ws = {"frames": fd, "review": tmp_path / "review"}
    res = review_workspace(ws, _TL, str(src))
    assert res["errors"] == [], res["errors"]
    assert len(res["contact_sheets"]) == 1
    assert (tmp_path / "review" / "beat_b1_0.00.png").is_file()
    assert res["loudness"]["peak"] <= 0 and "duration" in res["loudness"]


def test_review_workspace_missing_audio_pin(tmp_path):
    """静音轨回归钉（Task 2 补欠账）：无音轨视频必须报「无音轨」。"""
    from capabilities.video.services.studio_review import review_workspace

    fd = _make_frames(tmp_path, 8)
    src = _make_video(tmp_path, "noaudio.mp4", with_audio=False)
    ws = {"frames": fd, "review": tmp_path / "review"}
    res = review_workspace(ws, _TL, str(src))
    assert any("音轨" in e for e in res["errors"]), res["errors"]


def test_review_workspace_duration_deviation(tmp_path):
    """timeline.duration 与实测偏差 > 1/fps+0.05 → 报「时长偏差」。"""
    from capabilities.video.services.studio_review import review_workspace

    fd = _make_frames(tmp_path, 16)
    src = _make_video(tmp_path, "v2s.mp4", with_audio=False, duration=2)
    ws = {"frames": fd, "review": tmp_path / "review"}
    res = review_workspace(ws, _TL, str(src))  # _TL.duration=1.0，实测≈2.0s
    assert any("时长偏差" in e for e in res["errors"]), res["errors"]


def test_review_workspace_defensive_garbage():
    """防御式：ws/timeline/video 全是垃圾也不抛异常，错误进 errors。"""
    from capabilities.video.services.studio_review import review_workspace

    res = review_workspace({}, {}, "nonexistent.mp4")
    assert isinstance(res["errors"], list) and res["errors"]


def test_non_integer_duration_frames(tmp_path):
    """非整除时长（7.35s×30fps）帧数契约：ceil 为权威公式。

    渲染侧 studio_render 的 n_frames = ceil(duration*fps) = 221（覆盖到
    最后一帧）；review 的 expected 曾用 round = 220（银行家舍入），会把
    渲染正常的 frame_00220 误报「多余帧」，打断 P2 自查闭环。此测试钉住
    统一后的 ceil 契约（ceil 与 round 在该时长上确有分歧）。
    """
    import math

    from capabilities.video.services.studio_review import completeness_check

    assert math.ceil(7.35 * 30) == 221 and round(7.35 * 30) == 220
    fd = _make_frames(tmp_path, 221)
    # 契约正向：ceil 帧数齐全 → 无错误（整除时长测试从未覆盖的 seam）
    assert completeness_check(fd, 221) == []
    # 钉住 ceil 权威：少一帧（只剩 round 的 220 帧）按缺号报错，不放行
    (fd / "frame_000220.png").unlink()
    errs = completeness_check(fd, 221)
    assert any("缺帧" in e and "00220" in e for e in errs), errs
    # 反向证据：旧 round 契约（expected=220）会把渲染正常的第 221 帧报成多余
    fd2 = _make_frames(tmp_path / "full", 221)
    errs = completeness_check(fd2, 220)
    assert any("多余帧" in e and "00220" in e for e in errs), errs


# ── 素材使用检查（P2 验收实验回灌：真人全程没上屏的成片必须被 review 拦下） ──────


_SCENE_CSS_ONLY = (
    '<html><body><div id="p"></div>'
    "<script>window.seekTo=(t)=>{document.getElementById('p').style.left=(t*100)+'px';};"
    "window.seekTo(0);</script></body></html>"
)
_SCENE_WITH_ASSETS = (
    '<html><body><img src="../assets/matting/frame_000000.png">'
    "<script>window.seekTo=(t)=>{};window.seekTo(0);</script></body></html>"
)
_SCENE_WITH_FRAMES = (
    '<html><body><img src="../frames/frame_000000.png">'
    "<script>window.seekTo=(t)=>{};window.seekTo(0);</script></body></html>"
)
# 命名猜测错误（P2 验收实证：agent 猜 %05d，实际 %06d）→ 断链
_SCENE_WITH_WRONG_PATTERN = (
    '<html><body><img src="../frames/frame_%05d.png">'
    "<script>window.seekTo=(t)=>{};window.seekTo(0);</script></body></html>"
)
# 命名一致（%06d）→ 采样存在，合法
_SCENE_WITH_PATTERN = (
    '<html><body><img src="../frames/frame_%06d.png">'
    "<script>window.seekTo=(t)=>{};window.seekTo(0);</script></body></html>"
)
# 固定文件名但不存在 → 断链
_SCENE_WITH_MISSING_FRAME = (
    '<html><body><img src="../frames/frame_999999.png">'
    "<script>window.seekTo=(t)=>{};window.seekTo(0);</script></body></html>"
)
# JS 拼接（静态不可解析）→ 放过不误报
_SCENE_WITH_CONCAT = (
    "<html><body><script>"
    "var u='../frames/frame_'+pad(i)+'.png';window.seekTo=(t)=>{};window.seekTo(0);"
    "</script></body></html>"
)


def _make_scene(tmp_path, html, name="index.html"):
    sd = tmp_path / "scene"
    sd.mkdir(parents=True, exist_ok=True)
    (sd / name).write_text(html, encoding="utf-8")
    return sd


def test_review_flags_scene_without_real_assets(tmp_path):
    """scene 纯 CSS 零引用（frames 目录存在）→ 报「未使用任何真实素材」。"""
    from capabilities.video.services.studio_review import review_workspace

    fd = _make_frames(tmp_path, 8)
    scene = _make_scene(tmp_path, _SCENE_CSS_ONLY)
    assets = tmp_path / "assets"
    assets.mkdir()  # 目录存在但场景零引用：素材躺着不用 = 没用
    src = _make_video(tmp_path, "css_only.mp4", with_audio=True)
    ws = {"frames": fd, "review": tmp_path / "review", "scene": scene, "assets": assets}
    res = review_workspace(ws, _TL, str(src))
    assert any("未使用任何真实素材" in e for e in res["errors"]), res["errors"]


def test_review_flags_unprepared_asset_dirs(tmp_path):
    """frames/ 与 assets/ 目录均不存在（agent 未调 prepare_assets）→
    报「未准备任何素材」，文案与「零引用」区分。"""
    from capabilities.video.services.studio_review import review_workspace

    scene = _make_scene(tmp_path, _SCENE_WITH_ASSETS)
    src = _make_video(tmp_path, "nodirs.mp4", with_audio=True)
    ws = {
        "frames": tmp_path / "frames_missing",  # 键在，目录不存在
        "review": tmp_path / "review",
        "scene": scene,
    }
    res = review_workspace(ws, _TL, str(src))
    assert any("未准备任何素材" in e for e in res["errors"]), res["errors"]


def test_review_accepts_scene_with_asset_reference(tmp_path):
    """引用 ../assets/ 或 ../frames/ 任一即合法（只用一种也行，且文件真实存在）
    → 无素材 error。"""
    from capabilities.video.services.studio_review import review_workspace

    for html, need_asset in ((_SCENE_WITH_ASSETS, True), (_SCENE_WITH_FRAMES, False)):
        fd = _make_frames(tmp_path, 8)
        if need_asset:
            ad = tmp_path / "assets" / "matting"
            ad.mkdir(parents=True, exist_ok=True)
            (ad / "frame_000000.png").write_bytes(b"png")  # 仅存在性验证用
        scene = _make_scene(tmp_path, html)
        src = _make_video(tmp_path, "ok.mp4", with_audio=True)
        ws = {"frames": fd, "review": tmp_path / "review", "scene": scene}
        res = review_workspace(ws, _TL, str(src))
        assert res["errors"] == [], (html, res["errors"])


def test_review_asset_check_skipped_without_scene(tmp_path):
    """ws 无 scene 键（旧 fixture 形状/场景未写）→ 素材检查跳过，不引入回归错误。"""
    from capabilities.video.services.studio_review import review_workspace

    fd = _make_frames(tmp_path, 8)
    src = _make_video(tmp_path, "noscene.mp4", with_audio=True)
    ws = {"frames": fd, "review": tmp_path / "review"}
    res = review_workspace(ws, _TL, str(src))
    assert res["errors"] == [], res["errors"]


def test_review_flags_broken_pattern_reference(tmp_path):
    """断链检查：scene 引用 frame_%05d（5 位）而实际是 %06d（6 位）→ 报「断链」。

    P2 验收实证：agent 猜 7 种命名 pattern 全部 404 → 黑底兜底、真人没上屏。"""
    from capabilities.video.services.studio_review import review_workspace

    fd = _make_frames(tmp_path, 8)
    scene = _make_scene(tmp_path, _SCENE_WITH_WRONG_PATTERN)
    src = _make_video(tmp_path, "broken_pat.mp4", with_audio=True)
    ws = {"frames": fd, "review": tmp_path / "review", "scene": scene}
    res = review_workspace(ws, _TL, str(src))
    assert any("断链" in e for e in res["errors"]), res["errors"]


def test_review_flags_broken_fixed_reference(tmp_path):
    """断链检查：固定文件名引用不存在（frame_999999）→ 报「断链」。"""
    from capabilities.video.services.studio_review import review_workspace

    fd = _make_frames(tmp_path, 8)
    scene = _make_scene(tmp_path, _SCENE_WITH_MISSING_FRAME)
    src = _make_video(tmp_path, "broken_fix.mp4", with_audio=True)
    ws = {"frames": fd, "review": tmp_path / "review", "scene": scene}
    res = review_workspace(ws, _TL, str(src))
    assert any("断链" in e for e in res["errors"]), res["errors"]


def test_review_accepts_exact_pattern_reference(tmp_path):
    """断链检查：pattern 与实际命名一致（%06d）→ i=0/i=count-1 采样存在 → 无 error。"""
    from capabilities.video.services.studio_review import review_workspace

    fd = _make_frames(tmp_path, 8)
    scene = _make_scene(tmp_path, _SCENE_WITH_PATTERN)
    src = _make_video(tmp_path, "exact.mp4", with_audio=True)
    ws = {"frames": fd, "review": tmp_path / "review", "scene": scene}
    res = review_workspace(ws, _TL, str(src))
    assert res["errors"] == [], res["errors"]


def test_review_passes_unverifiable_js_concatenation(tmp_path):
    """JS 拼接（../frames/frame_ + pad(i)）静态不可解析 → 放过，不误报断链。"""
    from capabilities.video.services.studio_review import review_workspace

    fd = _make_frames(tmp_path, 8)
    scene = _make_scene(tmp_path, _SCENE_WITH_CONCAT)
    src = _make_video(tmp_path, "concat.mp4", with_audio=True)
    ws = {"frames": fd, "review": tmp_path / "review", "scene": scene}
    res = review_workspace(ws, _TL, str(src))
    assert not any("断链" in e or "未使用" in e for e in res["errors"]), res["errors"]
