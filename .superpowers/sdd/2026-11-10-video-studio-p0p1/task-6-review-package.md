dd4cc37 feat(studio): P0 spike 集成验证（分层场景+确定性渲染+质检）
diff --git a/backend/tests/fixtures/studio_spike/scene/index.html b/backend/tests/fixtures/studio_spike/scene/index.html
new file mode 100644
index 0000000..3edfedf
--- /dev/null
+++ b/backend/tests/fixtures/studio_spike/scene/index.html
@@ -0,0 +1,46 @@
+<!doctype html>
+<!-- Studio P0 spike 分层场景（Task 6 fixture）：四层合成，DOM 顺序即层序——
+     底帧(#base) → 身后动效标题条(#title) → 人物(#person) → 前景触发卡片(#card)。
+     运行时由测试拷入工作区 scene/，并生成 assets_manifest.js
+     （帧目录/数量/尺寸/节拍标题/触发卡片）。seekTo(t) 纯函数驱动全部画面状态；
+     渲染模式（URL 无参数）只暴露 seekTo 供逐帧截图，?preview 才启动 rAF 实时循环。 -->
+<html lang="zh">
+<head>
+<meta charset="utf-8">
+<title>studio spike scene</title>
+<style>
+  html, body { margin: 0; padding: 0; background: #000; }
+  #stage { position: relative; width: 1080px; height: 1920px; overflow: hidden; background: #101018; }
+  /* world = 原始帧坐标系（底帧/人物同几何），由 play.js 按 cover 比例缩放居中 */
+  #world { position: absolute; left: 50%; top: 50%; }
+  #base, #person { position: absolute; left: 0; top: 0; display: block; }
+  /* 身后层：标题条（人物之下，顶部落纯背景区不被人物遮挡），滑入手写插值 */
+  #title {
+    position: absolute; left: 0; top: 24px; box-sizing: border-box; width: 100%;
+    padding: 22px 44px 22px 40px; border-left: 14px solid #ff2442;
+    background: rgba(13, 15, 24, 0.78); color: #fff;
+    font: 700 58px/1.28 "Microsoft YaHei", "PingFang SC", sans-serif;
+    opacity: 0; transform: translateX(-108%);
+  }
+  /* 前景层：触发卡片（最上层，落人物躯干上方），弹入手写插值 */
+  #card {
+    position: absolute; left: 50%; bottom: 190px; box-sizing: border-box; max-width: 86%;
+    padding: 26px 42px; border-radius: 26px; background: #ff2442; color: #fff;
+    font: 700 54px/1.3 "Microsoft YaHei", "PingFang SC", sans-serif;
+    opacity: 0; transform: translate(-50%, 0) scale(0.8);
+  }
+</style>
+</head>
+<body>
+  <div id="stage">
+    <div id="world">
+      <img id="base" alt="" draggable="false">
+      <div id="title"><span id="title_text"></span></div>
+      <img id="person" alt="" draggable="false">
+      <div id="card"><span id="card_text"></span></div>
+    </div>
+  </div>
+  <script src="assets_manifest.js"></script>
+  <script src="play.js"></script>
+</body>
+</html>
diff --git a/backend/tests/fixtures/studio_spike/scene/play.js b/backend/tests/fixtures/studio_spike/scene/play.js
new file mode 100644
index 0000000..65fc128
--- /dev/null
+++ b/backend/tests/fixtures/studio_spike/scene/play.js
@@ -0,0 +1,113 @@
+/* Studio P0 spike 场景驱动（Task 6 fixture）。
+ *
+ * 契约：
+ *   - window.seekTo(t) 把画面设到第 t 秒的确切状态。纯函数：禁 Math.random /
+ *     Date / performance.now 参与画面计算。返回 Promise 等底帧/人物两层素材
+ *     decode 完成（Playwright evaluate 会 await 返回的 Promise，保证渲染器
+ *     逐帧截图不会拍到半解码的旧图）。
+ *   - 动效全部按 t 手写数学插值（easeOutCubic），禁用 CSS transition/animation
+ *     —— 渲染态下 transition 依赖真实流逝时间，逐帧截图会产生不确定帧。
+ *   - 渲染模式（URL 无参数）：只暴露 seekTo，不启动任何循环。
+ *   - 预览模式（URL 带 ?preview）：rAF 实时循环调 seekTo（仅人工预览用，
+ *     performance.now 只出现在预览循环里，不进 seekTo）。
+ */
+(function () {
+  "use strict";
+  var M = window.STUDIO_SCENE_MANIFEST;
+  var world = document.getElementById("world");
+  var baseEl = document.getElementById("base");
+  var personEl = document.getElementById("person");
+  var titleEl = document.getElementById("title");
+  var titleText = document.getElementById("title_text");
+  var cardEl = document.getElementById("card");
+  var cardText = document.getElementById("card_text");
+
+  var TITLE_IN = 0.4;  // 标题条滑入时长（秒），动效参数来自任务书
+  var CARD_IN = 0.3;   // 卡片弹入时长（秒）
+  var CARD_OUT = 0.3;  // 卡片弹出时长（秒）
+
+  // cover 缩放：人物与底帧同容器同几何，天然对齐无重影
+  var scale = Math.max(M.canvas.w / M.base.w, M.canvas.h / M.base.h);
+  world.style.width = M.base.w + "px";
+  world.style.height = M.base.h + "px";
+  world.style.transform = "translate(-50%, -50%) scale(" + scale + ")";
+  baseEl.style.width = M.base.w + "px";
+  baseEl.style.height = M.base.h + "px";
+  personEl.style.width = M.person.w + "px";
+  personEl.style.height = M.person.h + "px";
+
+  function clamp01(x) { return x < 0 ? 0 : (x > 1 ? 1 : x); }
+  function easeOutCubic(p) { var q = 1 - p; return 1 - q * q * q; }
+  function pad6(i) { return ("00000" + i).slice(-6); }
+  function frameSrc(dir, pattern, i) { return dir + "/" + pattern.replace("%06d", pad6(i)); }
+
+  var curBase = -1;
+  var curPerson = -1;
+  var curTitleBeat = null;
+  var curCard = null;
+
+  window.seekTo = function (t) {
+    if (t < 0) t = 0;
+    if (t > M.duration) t = M.duration;
+
+    // 1) 底帧层：index = floor(t*fps)，clamp 到 [0, count-1]
+    var bi = Math.floor(t * M.base.fps);
+    bi = Math.max(0, Math.min(M.base.count - 1, bi));
+    if (bi !== curBase) { baseEl.src = frameSrc(M.base.dir, M.base.pattern, bi); curBase = bi; }
+
+    // 2) 人物层：取与底帧同一视频时刻的抠像帧（round(bi*person.fps/base.fps)），
+    //    两层像素同一瞬 → 人物层与底帧同步无重影
+    var pi = Math.round(bi * M.person.fps / M.base.fps);
+    pi = Math.max(0, Math.min(M.person.count - 1, pi));
+    if (pi !== curPerson) { personEl.src = frameSrc(M.person.dir, M.person.pattern, pi); curPerson = pi; }
+
+    // 3) 身后层：当前 beat 的标题条，按 t 手写 easeOutCubic 滑入（无 CSS transition）
+    var beat = null;
+    for (var k = 0; k < M.beats.length; k++) {
+      if (t >= M.beats[k].t_start) beat = M.beats[k];
+    }
+    if (beat) {
+      if (curTitleBeat !== beat) { titleText.textContent = beat.title; curTitleBeat = beat; }
+      var e = easeOutCubic(clamp01((t - beat.t_start) / TITLE_IN));
+      titleEl.style.transform = "translateX(" + (-108 * (1 - e)) + "%)";
+      titleEl.style.opacity = String(e);
+    } else {
+      titleEl.style.opacity = "0";
+      curTitleBeat = null;
+    }
+
+    // 4) 前景层：触发卡片（t≥trigger.t 弹入，所属 beat 结束前弹出），同手写插值
+    var trig = null;
+    for (var j = 0; j < M.triggers.length; j++) {
+      if (t >= M.triggers[j].t && t < M.triggers[j].until) trig = M.triggers[j];
+    }
+    if (trig) {
+      if (curCard !== trig) { cardText.textContent = trig.text; curCard = trig; }
+      var pin = easeOutCubic(clamp01((t - trig.t) / CARD_IN));
+      var pout = clamp01((t - (trig.until - CARD_OUT)) / CARD_OUT);
+      var dy = 26 * (1 - pin) - 30 * pout;
+      cardEl.style.transform = "translate(-50%, " + dy + "px) scale(" + (0.8 + 0.2 * pin) + ")";
+      cardEl.style.opacity = String(pin * (1 - pout));
+    } else {
+      cardEl.style.opacity = "0";
+      curCard = null;
+    }
+
+    // 等两层素材 decode 完成（渲染器逐帧截图前必须拿到完整像素）
+    var d1 = baseEl.decode ? baseEl.decode() : Promise.resolve();
+    var d2 = personEl.decode ? personEl.decode() : Promise.resolve();
+    return Promise.all([d1, d2]);
+  };
+
+  window.seekTo(0);
+
+  // 预览模式（?preview）：rAF 实时循环，循环播放
+  if (new URLSearchParams(window.location.search).has("preview")) {
+    var t0 = performance.now();
+    var loop = function (now) {
+      window.seekTo(((now - t0) / 1000) % M.duration);
+      requestAnimationFrame(loop);
+    };
+    requestAnimationFrame(loop);
+  }
+})();
diff --git a/backend/tests/test_studio_spike.py b/backend/tests/test_studio_spike.py
new file mode 100644
index 0000000..18566f6
--- /dev/null
+++ b/backend/tests/test_studio_spike.py
@@ -0,0 +1,205 @@
+"""Task 6：P0 spike 集成验证（live，真实口播素材）。
+
+端到端链路：预切 15s 口播段 → 24fps 抽帧（底帧层）→ RVM 人物抠像（GPU，人物层）
+→ 分层场景（底帧/身后标题条/人物/前景卡片，seekTo 纯函数确定性渲染）→
+450 帧（15s×30fps）渲染 + 混音 → review_workspace 质检 → 本地预览服务打印 URL。
+
+门控：STUDIO_SPIKE=1 才运行（GPU 抠像 + 分钟级逐帧渲染，默认 skip）。
+skip 契约：matte_person 返回 None（GPU/torch/权重不可用）→ pytest.skip，
+不算 FAIL（抠像是增值步骤，降级契约见 person_matting）。
+
+红线遵守：
+  - whisper 禁止在本进程运行（ct2 会拉旧 cuDNN DLL，同进程后续 torch CUDA 必
+    fastfail，2026-10-29 实证，见 test_matting_live2.py）——speech 分段直接复用
+    既有转录产物 outputs/matting_live2/transcript.json（即本视频
+    live_person_talk.mp4 的 whisper 转录结果）。
+  - 动效确定性：场景 seekTo 按t手写插值（见 fixtures/studio_spike/scene/play.js），
+    渲染态无 CSS transition/animation。
+"""
+import asyncio
+import json
+import os
+import shutil
+import subprocess
+import sys
+import tempfile
+from pathlib import Path
+
+import pytest
+
+BACKEND = Path(__file__).resolve().parent.parent
+SRC_VIDEO = BACKEND / "uploads" / "videos" / "live_person_talk.mp4"
+TRANSCRIPT = BACKEND / "outputs" / "matting_live2" / "transcript.json"
+FIXTURE_SCENE = BACKEND / "tests" / "fixtures" / "studio_spike" / "scene"
+
+SEG_START = 22.0  # 22s 起为完整句首「第二大量除汗之后别立刻猛灌冰水」，人物正面清晰
+SEG_DUR = 15.0
+SEG_FILM_ID = "spike1"
+
+pytestmark = pytest.mark.skipif(
+    os.environ.get("STUDIO_SPIKE") != "1",
+    reason="P0 spike live 集成验证（GPU 抠像+分钟级逐帧渲染）：需 STUDIO_SPIKE=1 门控开启",
+)
+
+
+def _run_ffmpeg(cmd: list[str]) -> None:
+    """ffmpeg 执行（Windows 编码安全：str 参数列表禁 shell=True，输出落临时文件再读）。"""
+    from capabilities.video.adapters.html_to_video import resolve_ffmpeg
+
+    ffmpeg = resolve_ffmpeg()
+    assert ffmpeg, "ffmpeg not available (PATH / imageio-ffmpeg / FFMPEG_PATH)"
+    with tempfile.TemporaryDirectory() as td:
+        log = Path(td) / "ffmpeg.log"
+        with open(log, "wb") as f:
+            proc = subprocess.run(
+                [ffmpeg, *cmd], stdout=f, stderr=f, check=False,
+                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
+            )
+        text = log.read_text(encoding="utf-8", errors="replace")
+    assert proc.returncode == 0, f"ffmpeg failed (rc={proc.returncode}): {text[-600:]}"
+
+
+def _precut_segment(seg: Path) -> None:
+    """预切 15s 段：-c copy 优先（时长漂移 ≤0.3s 可接受），不行则重编码精确对齐。"""
+    copy_cmd = ["-y", "-loglevel", "error", "-ss", str(SEG_START), "-t", str(SEG_DUR),
+                "-i", str(SRC_VIDEO), "-c", "copy", str(seg)]
+    from capabilities.video.adapters.studio_assets import probe_duration
+
+    try:
+        _run_ffmpeg(copy_cmd)
+        if abs(probe_duration(str(seg)) - SEG_DUR) <= 0.3:
+            return
+    except AssertionError:
+        pass  # copy 失败/时长漂移超限 → 落重编码
+    _run_ffmpeg(["-y", "-loglevel", "error", "-ss", str(SEG_START), "-t", str(SEG_DUR),
+                 "-i", str(SRC_VIDEO), "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
+                 "-c:a", "aac", str(seg)])
+    assert abs(probe_duration(str(seg)) - SEG_DUR) <= 0.3, (
+        f"预切段时长偏差超限: {probe_duration(str(seg)):.3f}s")
+
+
+def _build_timeline() -> dict:
+    """手写 timeline（3 beat + 各 1 trigger）+ 真实 speech 分段（既有转录产物，段内偏移）。"""
+    segs = json.loads(TRANSCRIPT.read_text(encoding="utf-8"))
+    speech = []
+    for sgm in segs:
+        s0 = max(0.0, sgm["start"] - SEG_START)
+        s1 = min(SEG_DUR, sgm["end"] - SEG_START)
+        if s1 - s0 > 0.05:  # 越过切段边界的残段裁掉
+            speech.append({"start": round(s0, 3), "end": round(s1, 3), "text": sgm["text"]})
+
+    beats = [
+        {"id": "beat_cold", "t_start": 0.0, "t_end": 5.0,
+         "title": "第二件事：别猛灌冰水", "visual_task": "标题条滑入 + 讲解卡片弹入",
+         "triggers": [{"t": 1.6, "text": "大量出汗后，别立刻猛灌冰水"}]},
+        {"id": "beat_stomach", "t_start": 5.0, "t_end": 10.0,
+         "title": "肠胃最怕冷刺激", "visual_task": "标题条切换 + 提示卡片弹入",
+         "triggers": [{"t": 6.8, "text": "很容易腹痛、拉肚子"}]},
+        {"id": "beat_sun", "t_start": 10.0, "t_end": 15.0,
+         "title": "第三件事：做好防晒", "visual_task": "标题条切换 + 收尾卡片弹入",
+         "triggers": [{"t": 11.4, "text": "帽子、防晒衣、防晒霜都安排上"}]},
+    ]
+    return {"fps": 30, "duration": SEG_DUR, "canvas": {"w": 1080, "h": 1920},
+            "speech": speech, "beats": beats}
+
+
+def test_studio_spike_e2e():
+    from capabilities.video.adapters.person_matting import matte_person
+    from capabilities.video.adapters.studio_assets import extract_frames, probe_duration
+    from capabilities.video.adapters.studio_render import render_scene
+    from capabilities.video.services.studio_preview import StudioPreviewServer
+    from capabilities.video.services.studio_review import review_workspace
+    from capabilities.video.services.studio_workspace import (
+        create_workspace, save_timeline, validate_timeline,
+    )
+
+    assert SRC_VIDEO.is_file(), f"源素材缺失: {SRC_VIDEO}"
+    assert TRANSCRIPT.is_file(), f"既有转录产物缺失（禁止本进程跑 whisper）: {TRANSCRIPT}"
+
+    ws = create_workspace(SEG_FILM_ID)
+
+    # 1) 预切 15s 段
+    seg = ws["inputs"] / "seg.mp4"
+    _precut_segment(seg)
+
+    # 2) 底帧层素材：24fps 抽帧
+    base_info = extract_frames(str(seg), ws["assets"] / "base", fps=24.0)
+
+    # 3) 人物层素材：RVM 抠像（GPU；失败/None → skip 不算 FAIL）
+    person_raw = ws["assets"] / "person_raw"
+    if person_raw.exists():
+        shutil.rmtree(person_raw)  # 强制重抠：缓存只认目录，seg 变更会复用错帧
+    matte = asyncio.run(matte_person(str(seg), person_raw))
+    if matte is None:
+        pytest.skip("RVM 人物抠像不可用（torch/GPU/权重缺失，matte_person 返回 None）"
+                    "——人物层无法验证，P0 spike 按降级契约 skip 不算 FAIL")
+
+    # 4) 组织场景工程（fixture → 工作区，帧资产拷入 scene/assets 供 file:// 相对路径加载）
+    scene_dir = ws["scene"]
+    if scene_dir.exists():
+        shutil.rmtree(scene_dir)  # 清旧资产（重跑帧数量可能变化）
+    shutil.copytree(FIXTURE_SCENE, scene_dir)
+    base_dir = scene_dir / "assets" / "base"
+    person_dir = scene_dir / "assets" / "person"
+    base_dir.mkdir(parents=True)
+    person_dir.mkdir(parents=True)
+    for f in sorted((ws["assets"] / "base").glob("frame_*.jpg")):
+        shutil.copy2(f, base_dir / f.name)
+    for f in sorted(matte.frame_dir.glob("*.png")):
+        shutil.copy2(f, person_dir / f"person_{int(f.stem):06d}.png")
+
+    # 5) timeline：真实转录分段 + 手写 beats/triggers，先过 validate_timeline
+    tl = _build_timeline()
+    errs = validate_timeline(tl)
+    assert errs == [], f"timeline 校验失败: {errs}"
+    save_timeline(ws, tl)
+
+    # 6) 场景 manifest（测试侧生成，play.js 消费）
+    manifest = {
+        "duration": SEG_DUR, "canvas": {"w": 1080, "h": 1920},
+        "base": {"dir": "assets/base", "pattern": "frame_%06d.jpg",
+                 "fps": base_info.fps, "count": base_info.count,
+                 "w": base_info.width, "h": base_info.height},
+        "person": {"dir": "assets/person", "pattern": "person_%06d.png",
+                   "fps": matte.fps, "count": matte.count,
+                   "w": matte.width, "h": matte.height},
+        "beats": [{"t_start": b["t_start"], "title": b["title"]} for b in tl["beats"]],
+        "triggers": [{"t": g["t"], "until": b["t_end"], "text": g["text"]}
+                     for b in tl["beats"] for g in b["triggers"]],
+    }
+    (scene_dir / "assets_manifest.js").write_text(
+        "window.STUDIO_SCENE_MANIFEST = " + json.dumps(manifest, ensure_ascii=False) + ";",
+        encoding="utf-8")
+
+    # 7) 音频：seg → aac（渲染混音用）
+    audio = ws["out"] / "seg_audio.m4a"
+    _run_ffmpeg(["-y", "-loglevel", "error", "-i", str(seg), "-vn", "-c:a", "aac", str(audio)])
+
+    # 8) 确定性渲染：450 帧（15s×30fps）+ 混音 → final.mp4
+    final = ws["out"] / "final.mp4"
+    result = render_scene(scene_dir / "index.html", ws["frames"], duration=SEG_DUR, fps=30,
+                          width=1080, height=1920, audio_path=str(audio), out_video=final)
+    assert result.count == 450, f"渲染帧数不符: {result.count}"
+    assert final.is_file(), f"成片未生成: {final}"
+
+    # 9) 质检：errors 必须为空、每 beat 一张 contact sheet、时长 ≈15±0.05
+    review = review_workspace(ws, tl, str(final))
+    assert review["errors"] == [], f"质检 errors: {review['errors']}"
+    assert len(review["contact_sheets"]) == len(tl["beats"])
+    for p in review["contact_sheets"]:
+        assert Path(p).is_file(), f"contact sheet 缺失: {p}"
+    dur_final = probe_duration(str(final))
+    assert abs(dur_final - SEG_DUR) <= 0.05, f"成片时长偏差超 0.05s: {dur_final:.3f}s"
+
+    # 10) 本地预览服（人工观看用；测试结束即停，成片路径供直接播放）
+    server = StudioPreviewServer(ws)
+    url = server.start()
+    try:
+        print(f"\n[studio-spike] 预览 URL: {url}index.html?preview")
+        print(f"[studio-spike] 成片: {final}")
+        print(f"[studio-spike] 响度: {review['loudness']}")
+        print(f"[studio-spike] contact_sheets: {len(review['contact_sheets'])} 张")
+        print(f"[studio-spike] speech 来源: 既有转录产物 {TRANSCRIPT.name}"
+              f"（{len(tl['speech'])} 段真实时间，非占位）")
+    finally:
+        server.stop()
