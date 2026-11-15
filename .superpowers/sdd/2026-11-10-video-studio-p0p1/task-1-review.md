# Task 1 审查：原片抽帧 adapter（studio_assets）

- 审查对象：commit `d298d9b`（2 个新文件：`backend/capabilities/video/adapters/studio_assets.py` 160 行、`backend/tests/test_studio_assets.py` 14 行）
- 审查日期：2026-11-10
- 审查方式：三份材料比对（brief / report / review-package）+ 源码核实 + 语言语义独立实证；未重跑测试（测试证据采信实现者报告）

---

## 一、规格符合性核对

### 1.1 接口签名（与 brief 逐字比对）

| brief 要求 | 实现 | 结论 |
| --- | --- | --- |
| `@dataclass FrameSeqInfo { frame_dir: Path, count: int, fps: float, width: int, height: int }` | studio_assets.py L30-38，字段名、顺序、类型逐字一致 | ✅ |
| `def extract_frames(video_path: str, out_dir: Path, fps: float = 24.0) -> FrameSeqInfo` | L41，逐字一致（含默认值） | ✅ |
| `def probe_duration(media_path: str) -> float` | L76，逐字一致 | ✅ |
| 产出 `frame_%06d.jpg` | `FRAME_PATTERN = "frame_%06d.jpg"`（L27）+ `-start_number 0` | ✅ |

`resolve_ffmpeg` 契约已核实：`html_to_video.py` L56 `def resolve_ffmpeg() -> str | None`，实现的 `if not ffmpeg: raise RuntimeError(...)`（L50）/ `if not ffmpeg: return ""`（L136）判空逻辑与契约匹配。

### 1.2 测试断言（与 brief 逐字比对）

测试 14 行中 13 行与 brief 逐字一致（含注释、import 顺序、`testsrc=duration=2:size=320x568:rate=30`、`-pix_fmt yuv420p`、断言值 `48 / 24.0 / 0.01 / 320 / 568 / 2.0 / 0.1 / frame_%06d.jpg`）。唯一差异为第 13 行括号修复，见 1.3。

### 1.3 第 13 行括号修复（偏离点独立验证）

**brief 原文**：`assert (info.frame_dir / "frame_%06d.jpg" % 0).is_file()`

**独立实证**（venv Python 单行命令，非重跑测试）：

- 原表达式：`p / 'frame_%06d.jpg' % 0` → `TypeError: unsupported operand type(s) for %: 'WindowsPath' and 'int'`。`/` 与 `%` 同属乘法级优先级、左结合，先求值 `Path / str` 为 WindowsPath 再 `% 0`。**该断言无论实现如何永远 TypeError，确属 brief 笔误**，与实现者报告的实证一致。
- 修复表达式：`p / ('frame_%06d.jpg' % 0)` → `d\frame_000000.jpg`，`.is_file()` 即**验证第 0 帧文件存在**。`%06d` 格式逐字保留，断言意图（第 0 帧存在）与任务书一致。

**裁决：修复合法且语义与任务书意图一致** ✅（属已裁决偏离范畴，TDD "Test errors? Fix error" 原则的最小修复）。

### 1.4 全局约束逐条核对

| 约束 | 核对结果 |
| --- | --- |
| ffmpeg 一律经 `resolve_ffmpeg` 获取，禁止硬编码 | 两处调用点（L49、L135）全走 `resolve_ffmpeg()`，测试亦然；全文件零硬编码路径 ✅ |
| 本模块不得写死任何颜色/字体/动效参数 | 纯工具模块，无任何风格常量。`-qscale:v 2` 是 brief Step 3 明确要求的 JPEG 质量参数，非风格参数 ✅ |
| probe_duration/宽高用 PyAV 优先、ffmpeg stderr 兜底 | L82-94 / L97-111 完全按裁决实现；brief Step 3 的 ffprobe json 路径属已裁决覆盖，不算违规 ✅ |
| brief Step 3：中文路径 subprocess 传 str、输出落文件再读 | L61/L142 str 参数列表；L120-126/L138-145 stdout/stderr 落临时文件后 `encoding="utf-8", errors="replace"` 读回 ✅ |

### 1.5 实现细节对 brief 测试意图的支撑（必要，非偏离）

- `-start_number 0`（L63）：image2 muxer 默认从 1 起编号，不加则修复后的断言 `frame_000000.jpg` 必挂——满足 brief 断言意图的必要参数。
- `fps={fps:g}`（L62）：缺省 24.0 时输出 `fps=24`，与 brief 命令模板逐字一致；任意 float fps 合法传递。
- `-loglevel error`（L61）：降低 stderr 噪音，失败时错误信息仍完整落入临时日志。

### 1.6 提交范围

`d298d9b` 恰好包含 brief Step 5 指定的 2 个文件，无夹带 ✅（报告披露的 git 索引损坏问题属仓库级事项，本次提交用 pathspec 限定完成，与本任务 diff 无关，已单独提请人工处置）。

---

## 二、代码质量核对

- **subprocess 安全**：全部 list 参数、无 `shell=True`（L123/L141）✅
- **资源清理**：`tempfile.TemporaryDirectory()` 上下文自动清理（L120/L138）；`av.open` 用 with 释放句柄（L85/L102）；旧帧 `glob + unlink` 保证 count 精确（L55-56）；log 文件 with-open（L122/L140）✅
- **异常路径**：`probe_duration` 双链失败 raise `RuntimeError`（签名必须返回 float，不静默 0，L92-93）；`_run_ffmpeg` 非零退出附 stderr 尾部 600 字符（L125-126）；`extract_frames` 0 帧产出 raise（L68-69）；PyAV 宽泛 `except Exception` 带 `logger.debug` 记录，探测链设计意图明确（L88-89/L109-110）✅
- **YAGNI**：无多余公开 API；4 个私有辅助均有调用点（`_ffmpeg_info_stderr` 被 duration/size 两条兜底链复用）；2 个正则各自单用途 ✅
- **源码与 diff 一致性**：两份源文件与 review-package diff 逐行一致，无 diff 外改动 ✅

---

## 三、发现清单

### Critical（0 条）

无。

### Important（0 条）

无。

### Minor（4 条）

1. **`_SIZE_RE` 兜底解析未限定视频流行**（studio_assets.py L130/L159）：`re.search` 取全 stderr 首个 `,\s*\d{2,5}x\d{2,5}[,\s]` 匹配，若流信息前出现其他 `N×N` 片段或多视频流场景可能误匹配。仅影响 stderr 兜底路径（PyAV 优先，测试环境未触发），失败模式为宽高元信息错误而非崩溃。建议后续限定在含 `Video:` 的行内解析。
2. **`_probe_size` 双链失败返回 `(0, 0)` 而非 raise**（L98/L111）：与 `probe_duration` 的 raise 策略不对称。文档字符串已声明、报告自审已披露设计理由（抽帧本体不受元信息探测失败影响），属合理取舍；但 `FrameSeqInfo.width/height` 的 0 值语义需下游调用方知晓，Task 2+ 消费该结构时留意降级处理。
3. **`extract_frames` 先清空旧帧再执行 ffmpeg**（L55-56 → L65）：`_run_ffmpeg` 失败时输出目录处于"旧帧已删、新帧未生"状态。失败即 raise、无数据损坏风险，仅记录该副作用，无需改动。
4. **静态检查未执行**：venv 无 ruff，报告已如实披露。代码人工核对 100 列内、与仓库风格一致，未见违规；属流程性缺口，建议后续统一补跑。

---

## 四、裁决

1. **规格符合性：✅ 通过** —— 接口签名与测试断言值逐字一致；第 13 行括号修复经独立实证确属 brief 笔误、语义与任务书意图一致；三项全局约束（resolve_ffmpeg / 无风格常量 / PyAV 优先兜底）全部满足；已裁决偏离如实披露。
2. **代码质量：通过** —— subprocess 安全、编码安全、资源清理、异常路径均达标，无 YAGNI 违规；4 条 Minor 均不阻塞合入，可作为后续任务的小改进输入。
