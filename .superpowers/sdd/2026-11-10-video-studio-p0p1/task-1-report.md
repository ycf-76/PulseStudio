# Task 1 报告：原片抽帧 adapter（studio_assets）

日期：2026-11-10 · 分支：codex/design-workflow-orchestration-cards · 提交：`d298d9b`

## 做了什么

### 1. 新建 `backend/capabilities/video/adapters/studio_assets.py`

纯工具 adapter，不含任何风格常量。签名与任务书逐字一致：

```python
@dataclass
class FrameSeqInfo:
    frame_dir: Path
    count: int
    fps: float
    width: int
    height: int


def extract_frames(video_path: str, out_dir: Path, fps: float = 24.0) -> FrameSeqInfo
def probe_duration(media_path: str) -> float
```

实现要点：

- **抽帧**：`ffmpeg -y -loglevel error -i <src> -vf fps=<N> -qscale:v 2 -start_number 0 <out_dir>/frame_%06d.jpg`。
  `-start_number 0` 是测试通过的关键（image2 muxer 默认从 1 起编号，而测试断言 `frame_000000.jpg` 存在）。
  抽帧前清除输出目录旧 `frame_*.jpg` 保证 count 精确；抽帧后 0 帧产出则 raise。
- **元信息探测链（本机无 ffprobe，按裁决执行）**：
  1. PyAV（`av.open`）读容器 `duration`（µs→秒）与 video stream `codec_context.width/height`；
  2. 失败兜底：`ffmpeg -i <path>`（rc=1 属预期）stderr 解析 `Duration: HH:MM:SS.ms` 行与 `WxH` 流字段。
- **Windows 安全**：subprocess 一律 str 参数列表、禁止 shell=True；ffmpeg stdout/stderr 重定向到临时文件再以 `encoding="utf-8", errors="replace"` 读回（中文路径 GBK 兼容）。
- **ffmpeg 解析**：全部经 `capabilities.video.adapters.html_to_video.resolve_ffmpeg()`，零硬编码路径。

内部辅助：`_probe_size` / `_run_ffmpeg` / `_ffmpeg_info_stderr` / `_duration_from_ffmpeg_stderr` / `_size_from_ffmpeg_stderr`，正则 `_DURATION_RE`、`_SIZE_RE`。

### 2. 测试 `backend/tests/test_studio_assets.py`

任务书原文预先存在；仅修复第 13 行表达式优先级 bug（见「偏离」）。TDD 全程：RED（ModuleNotFoundError）→ 实现 → GREEN。

## 测试输出关键行原文

**失败（RED，实现前）**：

```
backend\tests\test_studio_assets.py:3: in test_extract_frames_counts_and_meta
    from capabilities.video.adapters.studio_assets import extract_frames, probe_duration
E   ModuleNotFoundError: No module named 'capabilities.video.adapters.studio_assets'
============================== 1 failed in 0.71s ==============================
```

**修复测试表达式 bug 后重新确认 RED 失败原因仍为模块缺失**（同一 ModuleNotFoundError，1 failed in 0.26s）。

**通过（GREEN，实现后）**：

```
backend\tests\test_studio_assets.py .                                    [100%]

============================== 1 passed in 0.59s ==============================
```

提交后又做一次最终回归：`1 passed in 0.59s`。

## 自审发现

1. **`-start_number 0` 必需**：不加则首个输出文件是 `frame_000001.jpg`，测试的 `frame_000000.jpg` 断言必挂。
2. **PyAV duration 单位是微秒**：`container.duration / 1_000_000`；mp4 容器对 lavfi 2s 视频返回精确 2.0s，容差 0.1 内。
3. **probe_duration 总失败路径**：PyAV 与 ffmpeg stderr 双双失败时 raise RuntimeError（签名必须返回 float，不能静默 0）。
4. **宽高兜底失败返回 (0, 0)** 而非 raise：抽帧本体（ffmpeg 成功）不受元信息探测失败影响，调用方可据 0 值降级处理。
5. **ruff 未装**（backend/.venv 无 ruff），静态检查跳过；代码按仓库 100 列宽、isort 风格手写对齐。

## 偏离任务书的点

1. **测试第 13 行表达式修复（必要 bug fix，TDD "Test errors? Fix error" 原则）**：
   原文 `(info.frame_dir / "frame_%06d.jpg" % 0)` 因 `/` 与 `%` 同级左结合，实际求值为
   `(info.frame_dir / "frame_%06d.jpg") % 0`，对 pathlib.Path 取 `%` 抛
   `TypeError: unsupported operand type(s) for %: 'WindowsPath' and 'int'`（已用 venv Python 实证）。
   该断言无论实现如何永远 TypeError。最小修复为加括号：`(info.frame_dir / ("frame_%06d.jpg" % 0))`，
   `%06d` 逐字格式与断言原意（`frame_000000.jpg` 存在）完整保留，其余测试值一字未动。
2. **任务书 Step 3 提到「meta 用 ffprobe json 解析」**：按父任务裁决覆盖——本机无 ffprobe，
   改为 PyAV 优先 + ffmpeg -i stderr Duration 行兜底（与裁决一致）。
3. **`fps={fps:g}`**：任务书命令模板字面为 `fps=24`，缺省 24.0 时 `:g` 输出 `24` 与模板逐字一致；
   任意 float fps（如 23.976）同样合法传给 ffmpeg。

## ⚠️ 仓库级发现（与本任务无关，需人工处置）

提交过程中发现 **git 索引损坏**：索引 1366 个 OID 中有 84 个 blob 在对象库中缺失
（历史遗留的大量未提交 WIP 暂存：165 新增 / 218 删除 / 23 修改，部分为前几个会话的
`_tmp_shot/` 探针、side_session 等特性文件）。后果与处置：

- `git status` / `git diff --cached` 均报 `fatal: unable to read <oid>` 而中止；
- **HEAD 历史完全完好**（根树 1991 个 blob 全部在库，已逐一校验）；
- 84 个缺失 blob 对应的工作区文件全部健在——可用 `git hash-object -w <path>` 逐个无损重写自愈；
- 本次提交采用 **pathspec 限定提交**（`git commit -- <两个文件>`），git 内部基于 HEAD+指定路径
  构建临时索引，提交纯净（`git diff HEAD~1 HEAD --name-status` 确认恰好 2 个文件），
  且**完全未触碰**用户的 WIP 暂存区；
- 顺带自愈了一个同样成因的悬空引用：`.superpowers/sdd/2026-11-03-side-session-mvp.md/progress.md`
  （`git hash-object -w` 重写后已从暂存区摘除，文件仍在磁盘，blob fe14467 已回库）。

建议后续人工决策：这批 WIP 暂存是恢复（自愈 blob 后重新审视）还是放弃（`git read-tree HEAD` 重建索引）。
