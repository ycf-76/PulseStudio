# Task 4 报告：质检工具（studio_review）

- 分支：codex/design-workflow-orchestration-cards
- 提交：`d1d7b2481f43c9c682fcbb99d4c1faa4e4ac087d`（feat(studio): 质检工具（contact sheet/响度/帧完整性/时长偏差））
- 执行方式：严格 TDD（RED → GREEN → 回归）

## 一、做了什么

### 1. `backend/capabilities/video/services/studio_review.py`（新建，360 行）

按任务书接口契约实现四个函数，签名与契约逐字一致：

| 函数 | 签名 | 说明 |
| --- | --- | --- |
| `contact_sheet` | `(frames_dir: Path, times: list[float], fps: float, out_png: Path, cell: int = 360) -> Path` | 每个时间点取 `round(t*fps)` 帧号，戳到最近**存在**的帧（bisect 找最近已存在帧号，距离并列取小号保证确定性）；网格列数 = `ceil(sqrt(n))`，每格等比缩放到 `cell` 宽，下标注 `t=x.xxs` |
| `loudness_report` | `(video_path: str) -> dict` | `ffmpeg -i x -af astats=metadata=1 -f null -` 输出重定向临时文件再读（`errors="replace"`），定位 stderr 末尾 `Overall` 段解析 `RMS level dB` / `Peak level dB`；duration 复用 Task 1 `probe_duration`；返回 `{"rms", "peak", "duration"}` |
| `completeness_check` | `(frames_dir: Path, expected: int) -> list[str]` | 扫 `frame_%06d.png/jpg`（兼容 Task 1 jpg 与 Task 2 png 产物）：缺号、0 字节、越界多余帧均报错，错误信息带帧号 |
| `review_workspace` | `(ws: dict, timeline: dict, video_path: str) -> dict` | 防御式汇总，返回 `{"contact_sheets": [...], "loudness": {...}, "errors": [...]}`；**绝不抛异常**（内部 try/except 全覆盖 + 外层兜底） |

`review_workspace` 五步汇总：
1. 每 beat 的 `[t_start, t_end]` 抽帧 contact sheet 落 `ws["review"]/beat_<id>_<t_start>.png`（beat id 做文件名安全清洗）
2. 帧完整性 `expected = round(duration*fps)`
3. 响度 `loudness_report`
4. 时长偏差：`probe_duration` vs `timeline.duration`，`> 1/fps + 0.05` 报错
5. **静音轨回归钉**（Task 2 审查补欠账）：`_audio_track_info` 用 PyAV 判有无 audio stream 及其 `stream.duration × stream.time_base`（异常时兜底容器 duration），全败走 `ffmpeg -i` stderr 的 `Audio:` 行兜底；无声轨 / 音轨时长 < 0.05s（≈0）/ 探测链全失败均报错入 errors

### 2. `backend/tests/test_studio_review.py`（新建，86 行，5 个测试）

- `test_review_on_synthetic_frames`：任务书 Step 1 测试**逐字采用**
- `test_review_workspace_happy_path`：testsrc+静音 aac 音轨 + 帧齐全 → `errors == []`、sheet 落 `review/beat_b1_0.00.png`、loudness 键齐
- `test_review_workspace_missing_audio_pin`：**无音轨视频**（testsrc 纯视频）→ 断言 errors 含「音轨」（回归钉要求）
- `test_review_workspace_duration_deviation`：2s 实拍 vs timeline 1.0s → 断言报「时长偏差」
- `test_review_workspace_defensive_garbage`：`review_workspace({}, {}, "nonexistent.mp4")` 不抛异常，errors 非空

## 二、测试输出关键行原文（失败 → 通过）

RED（实现前，任务书 Step 2 预期 ModuleNotFoundError）：

```
backend\tests\test_studio_review.py FFFFF                                [100%]
E   ModuleNotFoundError: No module named 'capabilities.video.services.studio_review'
...
============================== 5 failed in 0.94s ==============================
```

GREEN（实现后，任务书 Step 4）：

```
backend\tests\test_studio_review.py .....                                [100%]
============================== 5 passed in 1.07s ==============================
```

回归（Task 1–3 studio 套件，确认无破坏）：

```
backend\tests\test_studio_assets.py .                                    [  8%]
backend\tests\test_studio_workspace.py ..........                        [ 91%]
backend\tests\test_studio_render.py .                                    [100%]
============================== 12 passed in 10.44s ==============================
```

## 三、自审发现

1. **astats 格式实证**：实现前先用探针确认了本机 ffmpeg（PATH → WindowsApps，Lavf61）的 astats stderr 格式——mono 也会输出 `Overall` 段；键为 `RMS level dB:` / `Peak level dB:`。注意同段还有 `RMS peak dB`，正则用了完整键名 `RMS level dB:\s*` 避免误匹配（代码内有注释）。解析取 `rfind("Overall")` 之后的首个匹配；无 `Overall`（理论上多声道无汇总的旧版）时回退全文首个匹配。
2. **JSON 安全**：数字静音（anullsrc）的 astats 输出是 `-inf`，直接进 dict 会让 json.dumps 产出非法 JSON（`Infinity`），统一钉到 `-120.0`（`_DB_FLOOR`），docstring 已注明。happy path 测试的 `loudness.peak == -120.0` 即验证此路径。
3. **t_end 边界**：beat 的 `t_end=1.0, fps=8` → 帧号 8 越界（存在帧 0..7），nearest 逻辑戳到帧 7，不报错——happy path 覆盖了该边界。
4. **AAC 容器时长**：aac priming 使 mp4 容器时长略超 1s（≈1.05s），仍在 `1/fps+0.05=0.175s` 容差内，happy path 实测 `errors == []` 通过。
5. **commit 时 git 索引既存损坏条目**：提交输出出现 `error: invalid object ... for '_tmp_shot/inspect_shell.py'`（仓库既存问题，与本次变更无关），按约束用具体 pathspec + `--only` 提交成功，`git log -1 --stat` 确认只含本次两个文件。

## 四、偏离任务书的点

1. **测试数量超出 brief**：brief 只给 1 个测试；硬约束要求静音轨回归钉必须在测试中覆盖（造无音轨视频断言报错），且接口含 `review_workspace`，故补了 4 个测试（happy path / 无音轨钉 / 时长偏差 / 垃圾输入防御）。
2. **`-inf` 钉到 `-120.0`**：brief 未规定静音值的表示，为保证 JSON 可序列化自定 dB 下限 -120。
3. **contact sheet 标签/底色**：`t=x.xxs` 标签与深灰底色是质检产物自身版式（工具 UI），非视频风格常量，未触碰 overlay/themes 层。
4. **`loudness_report`/`contact_sheet` 在无帧/无音轨/非法输入时抛异常**（`RuntimeError`/`ValueError`），由 `review_workspace` 统一捕获入 errors——防御式边界按任务书落在 review_workspace 层，叶子函数保持快速失败。
5. 实现了 PyAV + ffmpeg stderr 双链音轨探测（约束说「可用 PyAV 或 ffmpeg stderr 判断」，两条都做以对齐 studio_assets 的探测链风格）。

## 五、环境事实

- Python 3.12.4（backend/.venv）、Pillow 12.3.0、PyAV 17.0.0、pytest 9.1.1
- ffmpeg 经 `resolve_ffmpeg()` 解析至 `C:\Users\...\WindowsApps\ffmpeg.EXE`，无硬编码；subprocess 全部 str 参数列表 + `CREATE_NO_WINDOW`，无 `shell=True`
