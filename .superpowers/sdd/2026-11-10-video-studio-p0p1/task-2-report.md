# Task 2 报告：确定性场景渲染 adapter（studio_render）

**状态：DONE_WITH_CONCERNS**
**提交：`8350754cb5cd1ddd774c7389886d063460e6e1e3`**（`feat(studio): 确定性场景渲染 adapter（逐帧截图+编码混音）`，父提交 d298d9b = Task 1，3 文件 +286 行）

## 一、做了什么

### 文件清单

| 文件 | 角色 |
| --- | --- |
| `backend/capabilities/video/adapters/studio_render_worker.py` | 子进程入口（新建，74 行） |
| `backend/capabilities/video/adapters/studio_render.py` | 父进程封装（新建，180 行） |
| `backend/tests/test_studio_render.py` | 测试（新建，32 行，brief 逐字 + 两处已裁决笔误修正） |

### 对外接口（与任务书逐字一致）

```python
@dataclass
class RenderResult:
    frames_dir: Path
    count: int
    fps: float
    elapsed: float
    video_path: Path | None

def render_scene(
    scene_html: Path, frames_dir: Path, duration: float,
    fps: int = 30, width: int = 1080, height: int = 1920,
    audio_path: str | None = None, out_video: Path | None = None,
) -> RenderResult
```

失败语义：worker 超时/失败/帧数不符/ffmpeg 编码失败 → `RuntimeError`（与 studio_assets 房法一致）；未请求 out_video 时 `video_path=None`。

### worker 协议（studio_render_worker.py）

- argv：`<scene_html> <frames_dir> <w> <h> <duration> <fps>`（与 _render_worker.py 同构）
- `page.goto(Path(scene_html).resolve().as_uri(), wait_until="load")`（file:// 打开磁盘场景文件，相对资产可用；**禁止 set_content**）+ `wait_for_timeout(300)` 字体/资产余量
- 逐帧：`page.evaluate("(t) => window.seekTo(t)", t)` + `screenshot(frame_%06d.png, omit_background=False)`，帧数 `n_frames = max(1, int(duration*fps + 0.999))`
- 末尾写 `_worker_status.json`：成功 `{ok: true, count, elapsed}`；异常 `{ok: false, count: 0, error: "TypeName: msg"}`；随后 `os._exit(0)`（成功）/ `os._exit(1)`（异常）硬退出——本机 `browser.close()` 会挂起，绝不优雅关闭
- 无风格常量（颜色/字体/动效均不在本模块）

### 父进程协议（studio_render.py）

- `subprocess.Popen([sys.executable, worker, ...] 全 str 参数列表, stdout/stderr=PIPE, creationflags=CREATE_NO_WINDOW)`，无 shell=True
- 超时（`60s 启动预算 + 0.6s/帧`）：`taskkill /PID <pid> /T /F` 杀进程树 → 再 `communicate(15s)` 收尸 → 抛 RuntimeError；正常退出也在 finally 里 taskkill 清扫 chromium 孙进程（仿 html_to_video）
- 成败判定：**读 `_worker_status.json`**（ok / count==n_frames / error）并核对返回码；stderr 尾部 600 字符附在报错里
- 启动前清 `frame_*.png` 与旧状态文件（复用目录重渲时防残留高号帧被 image2 顺序读误编入成片——同 Task 1 extract_frames 的理由）
- 编码混音 `_encode_video`：`ffmpeg -r <fps> -i frame_%06d.png`（`-r` 按任务书逐字，实测正常）+ `-c:v libx264 -pix_fmt yuv420p`；有 audio → `-i <audio> -c:a aac`；无 audio → 静音轨 `-f lavfi -t <视频时长> -i anullsrc=r=44100:cl=mono`；`-shortest` 收尾
- ffmpeg 输出落临时文件再读（errors="replace"，Windows 编码安全，同 studio_assets）；ffmpeg 路径一律 `resolve_ffmpeg()`，无硬编码

## 二、测试输出关键行原文（TDD 全程）

**RED**（实现前，`backend/.venv/Scripts/python -m pytest backend/tests/test_studio_render.py -v`）：

```
E   ModuleNotFoundError: No module named 'capabilities.video.adapters.studio_render'
FAILED backend\tests\test_studio_render.py::test_render_scene_deterministic_and_mux - ModuleNotFoundError: No module named 'capabilities.video.adapters.studio_render'
============================== 1 failed in 0.42s ==============================
```

**中途失败**（实现后首轮，暴露 brief 自相矛盾，见「偏离 2」）：

```
backend\tests\test_studio_render.py:17: in test_render_scene_deterministic_and_mux
    im = I2.open(r1.frames_dir / ("frame_%06d.png" % 4))
E   FileNotFoundError: [Errno 2] No such file or directory: 'C:\\Temp\\pytest-of-杨成锋\\pytest-45\\test_render_scene_deterministi0\\f1\\frame_000004.png'
```

注：该轮已先通过 `assert r1.count == 4` 与跨两次 Chromium 启动的帧 0 字节级确定性断言，失败点仅剩帧 4 越界。

**GREEN**（裁决帧 4→3 后最终轮）：

```
backend\tests\test_studio_render.py .                                    [100%]
============================== 1 passed in 10.07s ==============================
```

**回归**（Task 1，共享 resolve_ffmpeg / probe_duration 链路）：

```
backend\tests\test_studio_assets.py .                                    [100%]
============================== 1 passed in 0.40s ==============================
```

## 三、自审发现

1. **静音轨过冲 bug（已修，已验证）**：brief 要求「无 audio → 静音轨合成」，初版按字面用无限 `anullsrc` + `-shortest`，自验脚本实测 0.5s 视频编出 **0.998s**（本机 ffmpeg 对无限源+shortest 过冲，与 html_to_video 模块注释记载的「无限 color 源提前 EOF」同族）。修复：给 anullsrc 输入加 `-t <视频时长>` 定长后再走 `-shortest`。修复后复测 `duration: 0.5`，正式测试同时复跑通过。该路径不在 brief 测试覆盖内，属自审补充验证（临时脚本跑完即删，未留仓）。
2. brief 测试注释「混音：1s 静音音轨」与实际 `-t 0.5` 不符——按「值逐字」原则保留原注释与 0.5 值，未改动。
3. brief 未说明 render_scene 失败语义，实现取 RuntimeError 抛出（studio_assets 房法），供上游裁决。

## 四、偏离任务书的点

1. **括号优先级笔误（上级已裁决）**：brief 原文 3 处同型笔误（帧 0 确定性断言 ×2、帧 4 探测 ×1），全部按裁决改为 `("frame_%06d.png" % n)` 正确形式；裁决注只点名了帧 0 两处，帧 4 一处属同类笔误一并修正，其余值逐字。
2. **帧 4→3（self 裁决，需上级知悉）**：brief 探测 `frame_000004.png`（t=0.5s）与其自身约定矛盾——count=ceil(0.5×8)=4、帧号 0 起，帧 4 必不存在（GREEN 首轮 FileNotFoundError 实证）。按断言自述意图「方块已位移且资产非空白」改探末帧 3（t=0.375s，方块位移至 37.5px，(50,10) 落在方块内，帧 0 时该像素为白，断言仍有效）。已在测试内注释裁决理由。
3. **提交方式**：`git add <pathspec>` + 普通 `git commit` 在树构建阶段被损坏索引卡死（`error: invalid object 100644 12bd4f2... for '_tmp_shot/inspect_shell.py' → Error building trees`，即任务书预告的 84 缺失 blob 问题；普通 commit 会触及全索引树构建，并非如预期可用）。改用 `git commit -F <UTF-8 消息文件> --only <任务书三文件 pathspec>`：以 HEAD 树+指定路径建提交、无视其余损坏索引项，未跑 git status / add -A / 任何索引全量命令。中文消息经 shell 传递会乱码（PowerShell 5.1），故走 UTF-8 文件 `-F`（临时文件已删）。提交产物已验证：8350754cb5cd1ddd774c7389886d063460e6e1e3，3 文件 +286 行，父提交 d298d9b，消息中文无乱码。
