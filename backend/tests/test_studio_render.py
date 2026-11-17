def test_frame_count_matches_review_ceil(tmp_path):
    """销账：渲染侧帧数公式必须与 studio_review 的 math.ceil(duration*fps) 一致。

    旧公式 int(x+0.999) 在 duration*fps 略高于整数（如 4.0005）时得 4，
    review 侧 ceil 得 5 → 误报缺帧打断自查闭环。
    """
    from capabilities.video.adapters.studio_render import render_scene

    scene = tmp_path / "scene_ceil"
    scene.mkdir()
    (scene / "index.html").write_text(
        '<div id="p" style="position:absolute;left:0;top:0;width:10px;height:10px"></div>'
        '<script>window.seekTo=(t)=>{document.getElementById("p").style.left=(t*10)+"px";};'
        "window.seekTo(0);</script>",
        encoding="utf-8",
    )
    r = render_scene(
        scene / "index.html", tmp_path / "f_ceil", duration=4.0005 / 8, fps=8, width=320, height=568
    )
    assert r.count == 5


def test_render_scene_deterministic_and_mux(tmp_path):
    from capabilities.video.adapters.studio_render import render_scene

    scene = tmp_path / "scene"
    scene.mkdir()
    # 最小场景：seekTo 驱动方块位移（相对路径引用 assets/point.png）
    (scene / "assets").mkdir()
    from PIL import Image

    Image.new("RGB", (40, 40), (255, 0, 0)).save(scene / "assets" / "point.png")
    (scene / "index.html").write_text(
        '<img id="p" src="assets/point.png" style="position:absolute;left:0;top:0">'
        '<script>window.seekTo=(t)=>{document.getElementById("p").style.left=(t*100)+"px";};'
        "window.seekTo(0);</script>",
        encoding="utf-8",
    )
    r1 = render_scene(
        scene / "index.html", tmp_path / "f1", duration=0.5, fps=8, width=320, height=568
    )
    r2 = render_scene(
        scene / "index.html", tmp_path / "f2", duration=0.5, fps=8, width=320, height=568
    )
    assert r1.count == 4
    assert (r1.frames_dir / ("frame_%06d.png" % 0)).read_bytes() == (
        r2.frames_dir / ("frame_%06d.png" % 0)
    ).read_bytes()  # 确定性
    from PIL import Image as I2

    # brief 原文探测帧 4（t=0.5s），但其自身约定 count=ceil(0.5*8)=4、帧号 0 起，
    # 帧 4 必不存在（off-by-one 笔误，与括号笔误同类已裁决）→ 改探测末帧 3
    # （t=0.375s，方块位移至 37.5px，(50,10) 落在方块内）
    im = I2.open(r1.frames_dir / ("frame_%06d.png" % 3))
    assert im.getpixel((50, 10))[0] > 200  # 方块已位移且资产非空白
    # 混音：1s 静音音轨
    import subprocess

    from capabilities.video.adapters.html_to_video import resolve_ffmpeg

    wav = tmp_path / "a.wav"
    subprocess.run(
        [
            resolve_ffmpeg(),
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=mono",
            "-t",
            "0.5",
            str(wav),
        ],
        check=True,
        capture_output=True,
    )
    r3 = render_scene(
        scene / "index.html",
        tmp_path / "f3",
        duration=0.5,
        fps=8,
        width=320,
        height=568,
        audio_path=str(wav),
        out_video=tmp_path / "out.mp4",
    )
    assert r3.video_path.is_file()
    from capabilities.video.adapters.studio_assets import probe_duration

    assert abs(probe_duration(str(r3.video_path)) - 0.5) < 1 / 8 + 0.05  # 音画时长偏差 ≤1帧
