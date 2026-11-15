# SDD ledger — plan: docs/superpowers/plans/2026-11-10-video-studio-p0p1.md

BASE: f759e8e8fb7bf38756571b33027dbb0287911d05
Branch: codex/design-workflow-orchestration-cards（非 main，用户已批准在此实施）
Workspace: .superpowers/sdd/2026-11-10-video-studio-p0p1/（脚本路径翻译失败，手动创建）

## Preflight

- 计划全部为新增文件（adapters/studio_assets、studio_render*、services/studio_workspace、studio_review、studio_preview、tests、spike 场景），与现有代码零接口冲突，无并发修改风险
- git 对象库 reflog 有损坏告警（invalid reflog entry f651178c/f998c961），diff/add/commit 验证可用。Ruling: 忽略 reflog 报错、禁走 reflog 操作、不跑修复 — 若错：仅影响历史回溯，不影响本计划产出
- WSL bash 无法执行技能脚本（路径翻译）— Ruling: 工作区/brief/审查包均手动生成 — 若错：仅文件组织差异

- imageio_ffmpeg 不含 ffprobe 且本机 ffprobe 不在 PATH（talking_head_overlay.py:2637 已实证）。Ruling: studio_assets 的 probe_duration/分辨率/音轨探测用 PyAV 优先（av 包已装，对齐 talking_head_overlay._probe_video_resolution 模式），ffmpeg -i stderr 兜底 — 若错：探测失败降级需排查 av 包
- 后续审查包（review-package 脚本跑不了）：用 `git log --oneline BASE..HEAD` + `git diff -U10 BASE..HEAD` 重定向到审查包文件手动生成

## Tasks

- Task 1 studio_assets: complete (commits f759e8e..d298d9b, review clean, 4 Minor 不阻塞)
  - Minor 留档：_SIZE_RE 未限定视频流行；(0,0) 分辨率降级语义需下游知晓（Task 2/4 消费时注意）；extract_frames 先清帧后执行；ruff 未跑
  - 环境备忘：git 索引 84 个缺失 blob（前会话 WIP 暂存），git status/--cached 中止；提交须用 pathspec 限定文件，禁用 git add -A/--cached 操作；HEAD 历史完好
- Task 2 studio_render: complete (commits d298d9b..8350754, review clean, 2 Minor 不阻塞)
  - 偏离裁决留档：brief 帧 4→末帧 3（off-by-one 笔误）；anullsrc 无限源加 -t 定长（-shortest 过冲实测 0.5s→0.998s，已修+补验）；提交用 git commit -F 消息文件 --only pathspec 绕过损坏索引
  - Minor 留档：taskkill 注释语义；静音轨路径缺回归钉（Task 4 质检可补时长断言）
- Task 3 studio_workspace: complete (commits 8350754..70b6ee1 = 实现 562d9f + 修复R1 70b6ee1, review 1 Important 已修复、定点复审 0 发现, 4 Minor 不阻塞)
  - 偏离裁决留档：speech/beats 缺失即报错（与 spec「v1 仅口播原片」裁决一致）；json NaN/Inf 漏洞已修（math.isfinite）
  - Minor 留档：见 task-3-review.md
- Task 4 studio_review: complete (commits 70b6ee1..d1d7b24, review clean, 4 Minor 不阻塞)
  - 自定裁决留档：静音轨 rms/peak 钉 -120.0（JSON 安全，语义「无效」依赖 docstring 可辨）；静音轨回归钉已补（Task 2 欠账销账）
  - Minor 留档：ws 缺键早退跳过视频侧检查；ffmpeg 兜底误报「无音轨」；缺号路径无直接测试
- Task 5 studio_preview: complete (commits d1d7b24..efce25c, review clean, 4 Minor 不阻塞)
  - Minor 留档：url_for 缺前置守卫；幂等/校验路径无测试；重复 start 泄漏；stderr 访问日志噪音
- Task 6 spike: complete (commits efce25c..dd4cc37, review clean, 4 Minor 记录性质不阻塞)
  - 偏离裁决留档：人物层与底帧同瞬取帧（round(bi*30/24)，防重影）；seekTo 返回 decode Promise 供 evaluate await；assets_manifest.js script src 绕 file:// CORS；speech 用 matting_live2/transcript.json 既有转录（SEG_START 平移）
  - 成片 backend/outputs/studio/spike1/out/final.mp4（15s，450 帧，review errors==[]，RMS -13.56dB）
- 全部任务完成 → 最终整体审查（BASE f759e8e..HEAD dd4cc37）：总裁决通过、可进 P0 人工验收；2 Important 条件性发现
  - Important#1 帧数公式 seam：已修复（23c9610，ceil 统一，19 passed，定点复审 ✅）；Minor 遗留：render 侧 int(x+0.999) 极窄窗口偏差（沿袭 html_to_video 同式，记 P2 销账）
  - Important#2（见 final-review.md）：按审查者条件记入 P2 计划销账项
- 状态：P0+P1 代码全部完成待用户人工验收（看 final.mp4 / 开预览）；台账保留至验收通过后再清理工作区
