# Task 2 审查报告：确定性场景渲染 adapter（studio_render）

审查对象：commit `8350754cb5cd1ddd774c7389886d063460e6e1e3`（父提交 d298d9b，3 文件 +286 行）
审查方式：task-2-review-package.md 全量 diff 与磁盘源码逐行核对 + `_render_worker.py` / `html_to_video.py` / `studio_assets.py` 交叉核实 + `git show --stat` 验证提交产物。未重跑测试（采信报告 RED→GREEN→回归证据链）。

---

## 一、三个偏离点独立核实

### 偏离 1：测试探测帧 4 → 3 —— ✅ 合理，断言意图完整保留

- 矛盾属实：`n_frames = max(1, int(0.5*8 + 0.999)) = 4`，帧号 0 起，末帧是 3；`frame_000004.png` 必不存在。报告 GREEN 首轮 `FileNotFoundError` 实证（该轮已先通过 `count == 4` 与帧 0 字节级确定性断言）。
- 断言意图核实：原意图「0.5s 时方块已位移且资产非空白」。改探帧 3（t=0.375s）：场景 `seekTo` 置 `left = 37.5px`，40×40 红方块覆盖 x∈[37.5, 77.5)，探测点 (50,10) 距边缘 12.5px、完全在方块内部（无亚像素抗锯齿干扰），R>200 断言稳健；帧 0 时该像素为白底 —— 「方块位移 + 资产非空白」双重意图均保留。测试内已注释裁决理由。
- 该偏离合乎 brief 注明的笔误裁决精神（括号笔误同类 off-by-one），且实现者如实上报为 self 裁决。

### 偏离 2：anullsrc 加 `-t` 定长 —— ✅ 修复正确，接口未变

- 修复核对：`-f lavfi -t {video_duration:.3f} -i anullsrc=...` —— `-t` 作为输入 option 限制静音轨生成时长为 `n_frames/fps = 0.5s`，`-shortest` 保留。用法正确（input option 位置正确），与本机 ffmpeg「无限源 + shortest 过冲」的既有教训同族（`html_to_video._encode_preview` 的 color 源 `d=` 参数同法，注释明载）。
- 接口核对：`render_scene` 签名、`RenderResult` 字段、成败语义逐字未动；仅编码命令内部实现变化，属 brief「无 audio → 静音轨合成」的必要实现修正。
- 覆盖缺口说明：该路径不在 brief 测试内（r1/r2 未请求 out_video），修复证据为一次性自验（脚本已删），见 Minor-2。

### 偏离 3：`git commit --only` 绕过损坏索引 —— ✅ 环境问题，非代码缺陷

- `git show --stat` 独立验证：哈希 8350754、父提交 d298d9b（Task 1）、中文消息无乱码、恰 3 文件 +286 行（180+74+32），与报告逐项吻合。diff 与磁盘文件逐行一致。产物完整，无索引污染带入。

---

## 二、审查重点逐项核实

### 1. 确定性保障 —— 强

- worker 循环严格逐帧 `page.evaluate("(t) => window.seekTo(t)", t)` → `screenshot(...)`，循环内无 `wait_for_timeout`、无真实时间读取；唯一时间余量是 `load` 后一次 300ms 资产等待，非画面驱动。时间轴唯一来源是 `i / fps`，纯函数式。
- `omit_background=False`、`--force-device-scale-factor=1`、`device_scale_factor=1` 均为确定性保障参数，非风格。
- 同帧两次渲染字节一致断言真实生效：r1/r2 是两次独立 `render_scene`（两次独立 Chromium 启动），`frame_000000.png` `read_bytes()` 相等；报告证据链显示该断言在 GREEN 首轮（帧 4 越界前）已实际通过。
- `page.goto(file://...as_uri())` 打开磁盘场景文件（相对资产可用），无 `set_content`，符合场景时钟契约。worker 的 seekTo 无 `&&` 防御（_render_worker.py 有）：brief 逐字要求，且场景缺 seekTo 时会走 status error 报错——契约强化，合理差异。
- 音画时长钉子：`abs(probe_duration - 0.5) < 1/8 + 0.05`，全局约束逐字钉在混音路径上。

### 2. 子进程协议 —— 强

- `_worker_status.json` 语义清晰：成功 `{ok:true, count, elapsed}`，异常 `{ok:false, count:0, error:"TypeName: msg"}`；父进程先校验 rc（兜住 worker 无法写 status 的场景，如 playwright 缺失——其 import 在 try 外，traceback 走 stderr 尾部 600 字符），再读 status 校验 ok/count==n_frames，双通道无死角。
- 超时路径能杀树：except 分支先 `taskkill /PID <pid> /T /F`（此时 worker 活着，/T 可枚举整树杀 chromium 孙进程）→ `communicate(15)` 收尸 → 仍超时 `proc.kill()` 兜底 → raise。比 html_to_video 的超时路径（直接 return None）多了收尸与二次兜底，更健壮。
- 超时值合理：`60s 启动 + 0.6s/帧`，与 html_to_video 常量逐字同源；0.5s×8fps 场景预算 62.4s，Chromium 实启 3-6s、每帧约 0.1-0.3s，余量充足。
- Popen 全 str 参数、无 shell=True、CREATE_NO_WINDOW（win32）三处 subprocess（worker、ffmpeg、taskkill）全部带齐。

### 3. 资源泄漏 —— 有兜底

- 临时帧目录由调用方传入，本模块不产生临时帧；ffmpeg log 用 `TemporaryDirectory` with 块自动清理。
- worker 崩溃：rc≠0 → RuntimeError 含 stderr 尾部；status 不可读 → RuntimeError；ok=false → RuntimeError 含 error 字段。父进程各分支均不挂起（communicate 有超时）。
- 启动前清 `frame_*.png` 与旧 status：复用目录重渲时防残留高号帧被 image2 顺序误编（同 studio_assets.extract_frames 理由），正确。
- 正常退出时 worker `os._exit(0)` 不做清理，finally taskkill 兜底语义见 Minor-1（实践中防泄漏由 playwright driver stdin EOF 生效，html_to_video 已生产验证）。

### 4. YAGNI —— 清白

- 对外仅 `RenderResult` + `render_scene`，签名与 brief 逐字一致；`_encode_video`/`_kill_process_tree`/超时常量均为 Step 3 明示实现件；无重试、无回调、无进度上报等未要求功能。无风格常量（模块 docstring 明示归属层）。ffmpeg 唯一来源 `resolve_ffmpeg()`，无硬编码。

### 5. 全局约束逐条

| 约束 | 结果 |
| --- | --- |
| ffmpeg 一律 `resolve_ffmpeg()`，禁硬编码 | ✅（studio_render.py 唯一 ffmpeg 来源 L135） |
| Playwright 独立子进程 + 帧落盘后 `os._exit(0)` 硬退出 | ✅（worker L64/L70；成功 0、异常 1） |
| Windows `CREATE_NO_WINDOW` | ✅（三处 subprocess 全带，win32 条件守卫） |
| `window.seekTo(t)` 契约、禁随机/真实时间驱动 | ✅（时间轴仅 `i/fps`；场景代码归上游） |
| `page.goto(file://)`，禁 set_content | ✅（worker L45） |
| 平台层禁风格常量 | ✅（无颜色/字体/动效常量） |
| 音画时长偏差 ≤ 1帧+0.05s 测试钉住 | ✅（test L32） |

### 6. 测试与 brief 逐字核对

三处括号笔误修正（上级已裁决）、帧 4→3（含注释）之外全部逐字；`# 混音：1s 静音音轨` 注释与 `-t 0.5` 的矛盾按「值逐字」原则保留原样，处理符合裁决规则（brief 原文即自相矛盾，非实现引入）。

---

## 三、发现清单

### Critical（0 条）

无。

### Important（0 条)

无。

### Minor（2 条）

1. **taskkill 清扫链的语义瑕疵（studio_render.py L88-100, L173-179）**：① finally 分支在超时路径会二次调用 taskkill（except 内已调，幂等无害）；② 非 Windows 平台 `_kill_process_tree` 直接 return，超时时仅 `proc.kill()` 杀 worker、chromium 孙进程可能残留（沿用 html_to_video 同构 no-op，本仓库 Windows 目标，影响低）；③ worker 正常 `os._exit(0)` 后进程已死，finally 的 taskkill 对该 PID 实际是 no-op，注释「正常退出也确保 chromium 孙进程被带走」的生效机制实为 playwright driver 的 stdin EOF 收尾（沿袭 html_to_video 原注释）。三点均不影响本机正确性，属注释准确性/跨平台健壮性观察。

2. **静音轨编码路径无自动化回归钉住（test_studio_render.py）**：无 `audio_path` + `out_video` 的分支（anullsrc 定长修复所在路径）不在 brief 测试覆盖内，修复仅靠一次性自验（脚本已删，未留仓）。brief 测试逐字约束所致，非实现者过错；且无音轨时长过冲正是偏离 2 暴露过的真实风险。建议后续任务补一条无音轨 `out_video` 的时长断言（`render_scene(..., out_video=...)` 后 `probe_duration` 校验）。

---

## 四、裁决

1. **规格符合性：✅** —— 接口（`RenderResult`、`render_scene` 签名）、worker 协议（argv/file://+load+300ms/逐帧 seekTo+截图/status+os._exit）、编码命令（`-r fps -i frame_%06d.png` h264 yuv420p + aac + `-shortest`）、全局约束七条全部达标；三处偏离均核实为必要且合理，测试为 brief 逐字 + 已裁决笔误修正。
2. **代码质量：通过** —— 确定性保障、子进程协议、失败语义、资源兜底均为本仓库既有模式的忠实且略有加强的实现；0 Critical / 0 Important / 2 Minor，两条均不阻塞合入。
