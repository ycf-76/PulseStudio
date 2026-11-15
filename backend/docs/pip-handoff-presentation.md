# 口播讲解「全屏开场 → 让位演示」方案（v1.0）

> 状态：待评审 —— 用户批准前不动任何代码
> 日期：2026-10-31
> 需求原话：摄像头（只露头）先全屏，开始讲解内容时缩小放右上角，
> 页面留给素材演示（如 PPT），且 PPT 与口播同步。PPT 来源要求
> **双支持：用户自带 pptx + AI 自动生成**。

---

## 1. 现状盘点（2026-10-31，效果层 M1-M3 已收口）

### 已有、直接复用的零件

| 零件 | 位置 | 说明 |
|------|------|------|
| 人物抠像 | `adapters/person_matting.py`（RVM） | 输出 rgba 帧序列 `matte.frame_dir`，pip 链已消费 |
| PIP 小窗 | `talking_head_overlay._execute_pip`（~1677-1760） | 四角位置枚举 + pip_scale(0.1-0.5) + margin，matte 帧序列 overlay 到主画面 |
| 幻灯片主画面 | `_slides_to_video` | PNG 序列 → deck 视频；auto_deck 走句边界定时（`_slide_durations_from_transcript`），手动 deck_images 均分 |
| AI 生成 PPT | `auto_deck` 链（deck_orchestrator） | 转录 → LLM 要点 → Playwright 截图 PNG |
| 词级时钟 / 触发机制 | M2 `_find_trigger_start` / `_relaxed_span` | trigger 词 → 时间戳，卡触发式弹入已验收（live14） |
| 转场 | `_XFADE_STYLES` xfade 0.5s | smart_cut 段间转场现成 |

### 缺口（本方案要补的）

1. **开场全屏段**：现 pip 全程小窗，无「[0,t0] 全屏 → t0 缩小」时间轴
2. **触发翻页**：现 deck 页按句边界均分，未接 M2 触发机制
3. **只露头**：matte 保留半身/全身，无头部裁切
4. **pptx 入口**：deck_images 只收 PNG，无 pptx→PNG 转换器（仓库无
   python-pptx/soffice 依赖）

---

## 2. 设计

### N1 开场全屏 → 缩小让位（P0）

- **t0 判定**（开始讲解 = 第一页 PPT 上屏时刻）：
  - auto_deck：首页 trigger 命中词 start（M2 契约复用，`_find_trigger_start`）
  - 手动 deck（pptx/PNG）：可传 `handoff_t0` 显式指定；缺省取首个
    非静音句边界；找不到 → 3.0s 缺省
  - 优先级：`handoff_t0` > 首页触发词 > 句边界 > 3.0s（全降级不阻断）
- **分段合成**：
  - 段A = 原片 [0, t0+0.4]（人物全屏，不抠像 —— 最自然）
  - 段B = [t0, end] 现有 PIP 全合成（deck 主画面 + matte 角落小窗）
  - t0 处 xfade 0.4s 溶解（段A 人画面淡出 → 段B 内容画面淡入，
    「人让位给内容」）；音频 acrossfade 0.4s
  - 纯函数：`_handoff_plan(t0, duration) -> 段表` +
    `_handoff_cmd(...) -> ffmpeg argv`（红测试先行）
- **v2 备选（本方案不承诺）**：人像连续缩小「飞」到角落（overlay
  x/y/w/h 逐帧插值，渲染成本 ×N），若溶解版用户觉得生硬再开

### N2 PPT 与口播同步翻页（P0）

- **auto_deck 页**：`_slide_durations_from_transcript` 升级 —— 每页
  优先用 trigger 命中词 start 作翻页点（说到才翻，M2 同源）；
  找不到触发词的页回退句边界序列（绝不阻断）
- **手动 deck**：新增可选参数 `deck_triggers: list[str]`（与页序对齐），
  命中同上；不给则维持均分（现行为不变）
- 纯函数：`_trigger_slide_starts(rows, slides, duration) -> 页起点序列`

### N3 只露头（P1）

- matte 帧序列 overlay 前加 `crop`：v1 固定头肩框 —— 顶部
  `head_ratio`（默认 0.55 高）× 全宽，参数
  `pip_head_crop: float | None`（None=不裁，保持现行为）
- v2（不承诺）：人脸检测自适应框；先看 v1 效果

### N4 pptx → PNG 导入（P1）

- 新 adapter `adapters/pptx_to_images.py`，转换链：
  1. LibreOffice `soffice --headless --convert-to pdf`（探测 PATH +
     常见安装路径）
  2. `pdftoppm -png -r 150` 逐页出图
  3. Windows 备选：PowerPoint COM（win32com，本机装 Office 时）
- 顺序探测，全缺 → 明确报错事件（提示装 LibreOffice 或导出 PNG），
  不阻断其他链
- 参数 `deck_pptx: str`（与 deck_images/auto_deck 优先级：
  显式 deck_images > deck_pptx > auto_deck）
- 用户 pptx 页与口播的同步：v1 均分或手动 `deck_triggers`；
  「LLM 读 pptx 页文本自动配触发词」为 v2（不承诺）

---

## 3. 里程碑与验收

| 期 | 内容 | 验收物 |
|----|------|--------|
| N1 | N1 转场 + N2 同步翻页 | live16 连播：开场人全屏 → 说到第一页内容瞬间缩到右上角；此后每页「说到才翻」；用户实看裁决 |
| N2 | N3 只露头 + N4 pptx 入口 | live17 连播：小窗只露头；用户提供一个真实 pptx 走完双入口；用户实看裁决 |

纪律沿用效果层三期：方案批准 → 红测试 → 绿 → 真机连播 → 用户裁决 →
文档收口；每期通过才开下一期。

## 4. 风险与降级链

- t0 找不到 → 句边界/3.0s 缺省，绝不阻断出片
- 抠像失败 → 现有矩形小窗回退链不变；开场全屏段不受影响
- soffice/pdftoppm 缺失 → pptx 入口报错引导，auto_deck 路径不受影响
- xfade 分辨率不一致（原片 vs deck 画布）→ 段A 统一 scale 到主画布
- 翻页触发词全 miss → 回退句边界均分（现行为）

## 5. 明确不做

- 人像连续飞位动画（v2 备选，先验证溶解版）
- PPT 页内动画还原（pptx 动画导出即静态图，行业同款做法）
- 人脸检测自适应裁切（v1 固定框先验证）
- 转场模板扩容
