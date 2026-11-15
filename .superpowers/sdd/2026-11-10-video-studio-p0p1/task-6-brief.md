# Task 6: P0 spike 集成验证（live，真实口播素材）

**Files:**
- Create: `backend/tests/fixtures/studio_spike/scene/index.html` + `play.js`（最小分层场景工程，运行时由测试拷贝到工作区）
- Create: `backend/tests/test_studio_spike.py`（live 风格：`STUDIO_SPIKE=1` 环境变量开启，默认 skip）

**Interfaces:**
- Consumes:
  - Task 1 `extract_frames(video_path: str, out_dir: Path, fps: float = 24.0) -> FrameSeqInfo`（studio_assets）
  - Task 2 `render_scene(scene_html: Path, frames_dir: Path, duration: float, fps: int = 30, width: int = 1080, height: int = 1920, audio_path: str | None = None, out_video: Path | None = None) -> RenderResult`（studio_render）
  - Task 3 `create_workspace / save_timeline / validate_timeline`（studio_workspace）
  - Task 4 `review_workspace(ws, timeline, video_path) -> dict`（studio_review）
  - Task 5 `StudioPreviewServer(ws).start() -> url`（studio_preview）
  - 既有 `async def matte_person(video_path: str, out_dir: Path) -> MatteResult | None`（person_matting；MatteResult = {frame_dir, count, fps, width, height}，RGBA 帧序列）——**async，测试里用 asyncio.run**
  - 既有 `transcribe(video_path, 'small', 'zh')`（whisper_transcribe）——**已在别的测试进程跑过会占 GPU/cuDNN，spike 里禁止同进程跑 whisper；词级时间用既有转录产物，找不到就用均匀占位并在 review errors 里标注「占位时间轴」**
- 源素材：`backend/uploads/videos/live_person_talk.mp4`（真实口播，人像正面）
- Produces: 成片 `backend/outputs/studio/spike1/out/final.mp4` + review 产物（contact sheets/响度报告）——交付用户肉眼验收

**场景工程要求（spike 验管线不验美观，样式从简但动效必须真实可见）：**
- `index.html` + `play.js`，暴露 `window.seekTo(t)` 驱动四层：
  1. 底帧层 `<img id="base">`：src 按 t 换 `assets/base/frame_%06d.jpg`（index = floor(t*24)，clamp 到 [0, count-1]；帧路径用相对路径，scene 拷入工作区后 assets 软链或直接把 base/person 帧目录放 scene/assets/ 下——你选实现简单且 file:// 可用的方案）
  2. 身后层：动效标题条（进入 beat 1 时 0.4s CSS transition 滑入；seekTo 需确定性——用「根据 t 计算已进行时长并直接设终态+过渡被禁用」或「transition 由 seekTo 置类名且截图前等待」——禁止依赖真实流逝时间产生不确定帧；推荐：seekTo 内直接按 t 计算该元素当前应处的插值位置，手写 easeOutCubic 数学插值，不用 CSS transition 做渲染态）
  3. 人物层 `<img id="person">`：RGBA 帧序列（`assets/person/` 内帧，命名从 matte_person.frame_dir 拷入后统一为 person_%06d.png），铺满或按原始尺寸贴底
  4. 前景层：触发卡片（trigger.t 后 0.3s 内弹入，同样手写插值；卡片文字用 timeline 里 trigger.text）
- `play.js`：URL 带 `?preview` 时 rAF 循环调 seekTo（实时预览模式）；渲染模式（无参数）下不启动循环，只暴露 seekTo
- seekTo 纯函数性：禁 Math.random、禁 Date/performance.now 影响画面

**测试骨架（STUDIO_SPIKE=1 才跑；skipif + 原因）：**
1. 预切 15s 段：`ffmpeg -ss <选人物正面清晰段，如 20> -t 15 -i 源` → `ws["inputs"]/seg.mp4`（-c copy 若可行，不行则重编码）
2. `extract_frames(seg, ws["assets"]/base, fps=24)`
3. `asyncio.run(matte_person(seg, ws["assets"]/person_raw))` → 把 RGBA 帧统一改名 person_%06d.png 拷到 `scene/assets/person/`（matte 失败/None → 测试 skip 并说明 GPU 不可用，不算 FAIL）
4. 手写 timeline dict（3 个 beat + 1 个 trigger；词级时间：先在 backend/outputs 下搜 live_person_talk 相关转录 json，有就用段内偏移，无则均匀占位 + errors 标注；validate_timeline 必须先通过）
5. 音频：`ffmpeg -i seg.mp4 -vn -c:a aac seg_audio.m4a`
6. `render_scene(scene, ws["frames"], duration=15, fps=30, audio_path=..., out_video=ws["out"]/final.mp4)`（450 帧，Chromium 逐帧预计几十秒到几分钟，测试要给够超时）
7. `review_workspace` → 断言 errors==[]、final.mp4 存在、probe_duration≈15±0.05
8. `StudioPreviewServer` 起服，把预览 URL 与成片路径 print 到 stdout（-s 可见，人工观看用）；测试结束 stop
9. 断言 review contact_sheets 文件存在（每 beat 一张）

**Run（live）:**
```
set STUDIO_SPIKE=1
backend/.venv/Scripts/python -m pytest backend/tests/test_studio_spike.py -v -s
```
Expected: PASS（或 GPU 缺失时 SKIP+说明）；stdout 打印预览 URL 与成片路径

**Commit:**
```bash
git add backend/tests/test_studio_spike.py backend/tests/fixtures/studio_spike/scene
git commit -m "feat(studio): P0 spike 集成验证（分层场景+确定性渲染+质检）"
```
（backend/outputs 不提交。）

**人工验收（设计文档 P0 出口，由用户执行）：** 看 final.mp4 或开预览 URL，确认：动效真实可见（标题滑入/卡片弹入）、音画对齐、人物层与底帧同步无重影。
