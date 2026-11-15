"""叠加层编排服务 — 多叠加层时序编排。

设计依据：
  - ossclip 的 Zod-typed scene library：LLM 从预定义模板选参数，不自由生成 HTML
  - HyperFrames 的 source-time → cut-time 映射：叠加锚点随剪辑重算
  - edit-greek-reel 的 3 阶段流水线：规划 → 渲染 → 合成

核心功能：
  - 叠加模板库管理
  - 多叠加层时序编排（重叠检测、优先级排序）
  - source-time → cut-time 锚点重映射
"""

from __future__ import annotations

import json
import logging
import re
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class OverlayTemplate(str, Enum):
    """叠加模板类型 — 对齐 ossclip Zod-typed scene library。"""
    CAPTION_POP = "caption_pop"
    CAPTION_KARAOKE = "caption_karaoke"
    CAPTION_TYPEWRITER = "caption_typewriter"
    KEYWORD_ICON = "keyword_icon"
    KEYWORD_HIGHLIGHT = "keyword_highlight"
    STAT_CARD = "stat_card"
    NUMBER_ROLL = "number_roll"
    BAR_CHART_GROW = "bar_chart_grow"
    TITLE_CARD = "title_card"
    LOWER_THIRD = "lower_third"
    SECTION_CARD = "section_card"
    EMOJI_STICKER = "emoji_sticker"
    # v4：画面互动 + 整屏冲击
    ARROW_POINT = "arrow_point"
    CIRCLE_MARK = "circle_mark"
    FULL_CARD = "full_card"
    SPLIT_COMPARE = "split_compare"


TEMPLATE_PRIORITY: dict[OverlayTemplate, int] = {
    OverlayTemplate.FULL_CARD: 11,
    OverlayTemplate.SPLIT_COMPARE: 10,
    OverlayTemplate.TITLE_CARD: 10,
    OverlayTemplate.LOWER_THIRD: 9,
    OverlayTemplate.SECTION_CARD: 8,
    OverlayTemplate.STAT_CARD: 8,
    OverlayTemplate.NUMBER_ROLL: 7,
    OverlayTemplate.BAR_CHART_GROW: 7,
    OverlayTemplate.EMOJI_STICKER: 6,
    OverlayTemplate.ARROW_POINT: 6,
    OverlayTemplate.CIRCLE_MARK: 6,
    OverlayTemplate.KEYWORD_HIGHLIGHT: 5,
    OverlayTemplate.KEYWORD_ICON: 5,
    OverlayTemplate.CAPTION_KARAOKE: 3,
    OverlayTemplate.CAPTION_POP: 2,
    OverlayTemplate.CAPTION_TYPEWRITER: 2,
}

# 分层标签 = 模板 CSS 布局事实（2026-10-27 逐模板核实 overlay_templates.py 的 body 定位）。
# 事故教训：旧标签（keyword_highlight="inline"）与真实渲染（屏幕正中）不符，
# 同层冲突检测对它失效 → section_card 与 keyword_highlight 同屏叠字（o6 事故）。
#   bottom：flex-end 字幕带（caption 三件套 padding-bottom 260px / lower_third 180px）
#   center：垂直居中带（full 居中 / 靠左 / 靠右变体 — 同带同刻必撞）
#   free：绝对定位 x/y（arrow_point / circle_mark，小标注不强制互斥）
#   full：fixed inset 0 全屏覆盖（同时刻覆盖一切，与任何带冲突）
TEMPLATE_LAYER: dict[OverlayTemplate, str] = {
    OverlayTemplate.CAPTION_POP: "bottom",
    OverlayTemplate.CAPTION_KARAOKE: "bottom",
    OverlayTemplate.CAPTION_TYPEWRITER: "bottom",
    OverlayTemplate.LOWER_THIRD: "bottom",
    OverlayTemplate.KEYWORD_ICON: "center",
    OverlayTemplate.KEYWORD_HIGHLIGHT: "center",
    OverlayTemplate.STAT_CARD: "center",
    OverlayTemplate.NUMBER_ROLL: "center",
    OverlayTemplate.BAR_CHART_GROW: "center",
    OverlayTemplate.TITLE_CARD: "center",
    OverlayTemplate.SECTION_CARD: "center",
    OverlayTemplate.EMOJI_STICKER: "center",
    OverlayTemplate.ARROW_POINT: "free",
    OverlayTemplate.CIRCLE_MARK: "free",
    OverlayTemplate.FULL_CARD: "full",
    OverlayTemplate.SPLIT_COMPARE: "full",
}


class OverlaySegment(BaseModel):
    """单个叠加片段。

    template: 内置 16 模板存 OverlayTemplate（str Enum）；P1 运行时生成
    模板（gen_*）存纯 str — template_name() 统一取名。
    """
    segment_id: str
    time_start: float = Field(ge=0)
    time_end: float = Field(gt=0)
    template: str
    params: dict[str, Any] = Field(default_factory=dict)
    skipped: bool = False


def template_name(t: Any) -> str:
    """模板名统一取值 — OverlayTemplate Enum 或纯 str（gen_* 生成模板）。"""
    return t.value if hasattr(t, "value") else str(t)


class OverlayPlan(BaseModel):
    """叠加规划 — 闸门 1 的产物。"""
    video_id: str
    segments: list[OverlaySegment] = Field(default_factory=list)
    palette: list[str] = Field(default_factory=list)  # v4：全片 3 色撞色盘
    needs_confirmation: bool = True
    # P2 两步链：叙事结构摘要（theme/sections[]: role+visual_intent）—
    # 可解释中间层，随 model_dump 落 report；单步链/降级时为 None
    narrative: dict[str, Any] | None = None


class TimeMapper:
    """source-time → cut-time 映射 — 对齐 HyperFrames。

    剪辑后，叠加锚点需要从源时间轴映射到剪辑后时间轴。
    """

    def __init__(self, kept_segments: list[tuple[float, float]]):
        self._kept = sorted(kept_segments)
        self._offset = 0.0
        self._table: list[tuple[float, float, float]] = []
        cut_time = 0.0
        for src_start, src_end in self._kept:
            self._table.append((src_start, src_end, cut_time))
            cut_time += src_end - src_start

    def map(self, source_time: float) -> float | None:
        """源时间 → 剪辑后时间。如果源时刻被删了，返回 None。"""
        for src_start, src_end, cut_start in self._table:
            if src_start <= source_time <= src_end:
                return cut_start + (source_time - src_start)
        return None

    def total_cut_duration(self) -> float:
        if not self._table:
            return 0.0
        last_src_start, last_src_end, last_cut_start = self._table[-1]
        return last_cut_start + (last_src_end - last_src_start)


def remap_overlay_anchors(
    plan: OverlayPlan,
    kept_segments: list[tuple[float, float]],
) -> OverlayPlan:
    """把 overlay_plan 中的时间锚点从源时间轴映射到剪辑后时间轴。

    对齐 HyperFrames: "Recompute the mapping and re-resolve every overlay anchor before rendering."
    如果某个 overlay 的源时刻被删了，标记为 skipped。
    """
    mapper = TimeMapper(kept_segments)
    new_segments = []

    for seg in plan.segments:
        new_start = mapper.map(seg.time_start)
        new_end = mapper.map(seg.time_end)

        if new_start is None or new_end is None:
            new_segments.append(seg.model_copy(update={"skipped": True}))
            logger.info(f"Overlay {seg.segment_id} skipped (source time cut)")
            continue

        new_segments.append(seg.model_copy(update={
            "time_start": new_start,
            "time_end": new_end,
        }))

    return OverlayPlan(
        video_id=plan.video_id,
        segments=new_segments,
        palette=plan.palette,
        needs_confirmation=plan.needs_confirmation,
    )


def sort_overlays_by_priority(
    segments: list[OverlaySegment],
) -> list[OverlaySegment]:
    """按模板优先级排序叠加层。

    高优先级（如 title_card）先渲染，低优先级后渲染。
    同层叠加按时间排序。
    """
    return sorted(
        segments,
        key=lambda s: (
            -TEMPLATE_PRIORITY.get(s.template, 0),
            s.time_start,
        ),
    )


def _spatial_conflict(a: OverlaySegment, b: OverlaySegment) -> bool:
    """两个活跃片段是否空间冲突（时间重叠 + 布局带相交）。

    规则（与 TEMPLATE_LAYER 布局事实一致）：
    - 时间不重叠（首尾相接不算）→ 不冲突
    - 任一为 full（全屏覆盖）→ 冲突
    - 同带（bottom/center）→ 冲突
    - free 仅与 free 冲突（绝对定位小标注，与其他带不强制互斥）
    """
    if a.time_start >= b.time_end or b.time_start >= a.time_end:
        return False
    # gen_* 生成模板布局未知 → 默认 center（参与同带互斥，宁少勿叠）
    la = TEMPLATE_LAYER.get(template_name(a.template), "center")
    lb = TEMPLATE_LAYER.get(template_name(b.template), "center")
    if la == "full" or lb == "full":
        return True
    if la == "free" or lb == "free":
        return la == lb
    return la == lb


def detect_overlay_conflicts(
    segments: list[OverlaySegment],
) -> list[tuple[OverlaySegment, OverlaySegment]]:
    """检测空间冲突的叠加片段对（时间重叠 + 布局带相交）。

    返回冲突对列表（供 LLM 或用户决策）。
    """
    conflicts = []
    active = [s for s in segments if not s.skipped]

    for i in range(len(active)):
        for j in range(i + 1, len(active)):
            if _spatial_conflict(active[i], active[j]):
                conflicts.append((active[i], active[j]))

    return conflicts


def resolve_overlay_conflicts(plan: OverlayPlan) -> OverlayPlan:
    """规划后处理：贪心消解空间冲突 — 检测到不裁决等于没检测。

    2026-10-27 o6 事故：LLM 重规划屡次违反"同带不重叠"软规则，
    section_card 与 keyword_highlight 同屏叠字，VLM 两轮审查都抓到
    但重规划又再犯 → 强制裁决：低优先级标 skipped（不删除，可追溯）。

    贪心顺序：优先级降序 → time_start 升序 → 时长降序
    （同优先级先开始者胜出）。
    """
    active_idx = [i for i, s in enumerate(plan.segments) if not s.skipped]
    ranked = sorted(active_idx, key=lambda i: (
        -TEMPLATE_PRIORITY.get(plan.segments[i].template, 0),
        plan.segments[i].time_start,
        -(plan.segments[i].time_end - plan.segments[i].time_start),
    ))

    kept: list[int] = []
    for i in ranked:
        seg = plan.segments[i]
        if all(not _spatial_conflict(seg, plan.segments[j]) for j in kept):
            kept.append(i)

    skipped = set(active_idx) - set(kept)
    if not skipped:
        return plan

    for i in sorted(skipped):
        seg = plan.segments[i]
        logger.info(
            f"Overlay {seg.segment_id} skipped (conflict resolution: "
            f"{template_name(seg.template)} loses spatial conflict)"
        )
    segments = [
        s.model_copy(update={"skipped": True}) if i in skipped else s
        for i, s in enumerate(plan.segments)
    ]
    return plan.model_copy(update={"segments": segments})


OVERLAY_PLAN_PROMPT = """分析以下口播视频的转录文本，规划叠加动画。

视频信息：
- 时长：{duration:.1f}s
- 转录文本（含词级时间戳 words，用于精确对齐）：
{transcript_json}

可用模板（16 种，必须多样化混用）：
字幕类：
1. caption_pop: 逐词弹出大字幕；params.accent_words 传本段最重的 2-4 个词 →
   命中词自动变亮黄/亮红圆角色块弹出（字幕的"亮点"，尽量每次都传）
2. caption_karaoke: 卡拉OK字幕，当前词亮黄色块高亮（CapCut 风）
3. caption_typewriter: 打字机字幕（叙事推进）
强调类：
4. keyword_icon: 关键词白底气泡卡 + emoji 图标弹出（概念引入）
5. keyword_highlight: 关键词亮黄标记笔从左扫出，扫满文字翻黑（金句强调）
6. emoji_sticker: 大 emoji 弹跳微旋转（全片最强戏剧点专用）
数据类：
7. stat_card: 亮色渐变数据卡（数字论据）
8. number_roll: 数字滚动计数（增长/成果数字，戏剧点专用）
9. bar_chart_grow: 多彩柱状图生长（对比数据）
结构类：
10. title_card: 标题卡 + 亮色下划线扫出（开头/结尾/段落分隔）
11. section_card: 段落编号卡 — 转录出现"第一/第二/第三/首先/其次/最后"等列举时，
    段首必用；params.number 传 "01"/"02"，params.title 传该段主题
12. lower_third: 亮色渐变字幕条（身份/来源介绍）
画面互动类（v4 — 让叠加与画面空间对话）：
13. arrow_point: 箭头指向 + 标签色块（口播指向画面某处/某物时"戳"向它）；
    params: x/y (0-1 相对位置), direction (up/down/left/right), label, color
14. circle_mark: 手绘感圆圈圈注 — 圆圈"画"出来圈住重点位置；
    params: x/y, color, size (0-1 直径比例), label（可选）
整屏冲击类（v4 — 开场轰炸/结尾收束专用，单次驻留 1.2-2.0s，全片最多 3 次）：
15. full_card: 整屏色块闪卡，大字砸入 + 快速淡出；params: text, sub, color
16. split_compare: 左右分屏 ❌/✅ 对比卡；params: bad_text, good_text, bad_color, good_color

自定义模板（gen_* — 内容定制视觉，按需大胆提名）：
内置 16 个模板优先考虑；但内容真正需要独特视觉时（进度条、对话气泡、
地图标注、倒计时、时间轴、行业专属图示……任何贴合内容的形态），直接用
"gen_<功能名>"（snake_case 英文）提名，数量不设限。
params.requirement 必须一句话说清视觉规格（布局/元素/动画/用色）——
它是生成模板的工程需求书，写得越具体生成质量越高。
原则：能用内置模板表达的不必硬造；但绝不要为了省事拿不贴合的内置模板凑合。

视觉冲击规则（最重要 — 白字黑边不叫效果，每个叠加都要有亮色记忆点）：
1. 全片至少使用 4 种不同模板；caption 类（caption_pop/caption_karaoke/caption_typewriter）
   合计时长不超过全片的 50%，把画面时间让给色块/气泡/卡片/emoji 类强视觉模板
2. caption_pop 尽量都传 accent_words（本段最重的 2-4 个词）→ 亮色色块弹出
3. 每个叠加都要有亮色记忆点：色块（section_card/stat_card/full_card）、白底气泡（keyword_icon）、
   标记笔（keyword_highlight）、大 emoji（emoji_sticker）、箭头/圆圈（arrow_point/circle_mark）
   优先于纯文字；同一个模板不要连续用超过 2 次
4. 图形落在词上：叠加的 time_start 必须对齐到说出该内容的词的时间戳（转录 words 数据），
   不要提前出现，误差控制在 0.1s 内
5. 字面化视觉：文字内容必须直接来自口播原话（提取关键词/数字），不要自行编造文案
6. 一个区域只有一个动的东西：同层（bottom/center/top/inline）不重叠，
   相邻叠加间隔 ≥1.5s
7. 强特效克制：emoji_sticker/number_roll 只用于全片最强的 2-3 个戏剧点

节奏规则（v4 — 规划即节奏，不是均匀铺满）：
1. 开场 3 秒内 ≥2 个叠加（开场轰炸：full_card 或 title_card 打头，随后一个字幕/图标跟进）
2. 密集段 2-3 个连续叠加后允许留白空档（每 10 秒 1 个是下限，不是平均目标）
3. 结尾必须有收束卡：full_card 或 title_card，time_end 贴近视频结尾
4. 整屏冲击卡（full_card/split_compare）只用在开场、最强论点、结尾三个位置

主题色系统（v4 — 全片视觉统一）：
先定全片 3 色撞色盘（亮色撞色为主，示例 ["#FF4D4D", "#FFE066", "#4ECDC4"]，禁止超过 3 色），
之后每个叠加的 params 必须带 color 字段且取值必须来自盘内
（caption_pop 的 accent_words 无需色值，自动黄红交替）。

密度约束（重要）：
- 每 10 秒至少 1 个叠加，全片最少 {min_segments} 个
- 转录 words 的文本有口语/错字时，params 里的文字可轻微润色（去口头语、纠错字），
   但必须保留原意和关键词

规划约束：
1. 同一时刻最多 2 个叠加层（不同 layer）
2. keyword_icon 每个关键词最多出现一次
3. 不要在静音/停顿处叠加字幕
4. 所有文字（标题/字幕/关键词/卡片文案）必须使用简体中文

只输出 JSON（不要 markdown 代码块），格式：
{{"palette": ["#FF4D4D", "#FFE066", "#4ECDC4"], "overlays": [{{"segment_id": "o1", "time_start": 0.0, "time_end": 3.0, "template": "title_card", "params": {{"title": "标题", "color": "#FFE066"}}}}]}}"""


# P2 两步链第一步：叙事结构分析（参考 NarratoAI 的两步 LLM 链）
NARRATIVE_PROMPT = """分析以下口播视频的转录文本，先做叙事结构分析（这是叠加动画规划的第一步）。

视频信息：
- 时长：{duration:.1f}s
- 转录文本（含词级时间戳 words）：
{transcript_json}

输出叙事结构摘要：
1. theme：一句话点明全片主题
2. palette：全片 3 色撞色盘（亮色撞色，色相从内容情绪推导 — 警示红黄 / 清新青绿 / 活力橙粉等）
3. sections：叙事段划分，时间连续覆盖全片，段边界落在词间隙处（用 words 时间戳）。
   每段含：
   - role 叙事角色：opening 开场 / argument 论点 / transition 转折 / climax 高潮 / closing 收束
   - summary 一句话段落摘要
   - visual_intent 视觉意图：这段想要什么感觉的视觉强化（如"冲击开场"/"数据佐证"/"轻快推进"/"收束定格"）
   - key_points 本段最重的 2-4 个关键词（后续叠加文案从这些词里出）

注意：只做结构与意图分析，不选模板不排时间轴（那是下一步的事）。
只输出 JSON（不要 markdown 代码块），格式：
{{"theme": "…", "palette": ["#…", "#…", "#…"], "sections": [{{"section_id": "s1", "role": "opening", "time_start": 0.0, "time_end": 4.1, "summary": "…", "visual_intent": "…", "key_points": ["…"]}}]}}"""


# 第二步头部块：叙事 JSON 注入规划 prompt（模板清单/规则仍以
# OVERLAY_PLAN_PROMPT 为唯一真相 — v1.8 双源漂移教训）
_NARRATIVE_TO_PLAN_HEADER = """叙事结构分析（第一步产出 — 你的规划必须在此结构内落实）：
{narrative_json}

落实要求：
- palette 已在上面定好，直接采用，不要另定
- 每个叠加的时间轴落在所属叙事段范围内，模板气质对应该段 visual_intent
- 叙事段的 key_points 是叠加文案（accent_words/keyword/卡片标题）的首选来源
- 段边界与 role 决定节奏：opening/closing 段用整屏冲击卡，argument 段用强视觉模板推进"""


def _parse_narrative_output(text: str, duration: float) -> dict[str, Any] | None:
    """解析叙事结构输出 — 防御式（仿 _parse_plan_output）。

    坏 JSON / 非 dict / sections 缺失或空 → None（调用方降级单步链）；
    段缺失时间戳 → 丢弃该段保留其余；时间越界 clamp 到时长内
    （duration≤0 = 探测失败，不 clamp）。
    """
    start = text.find("{")
    if start < 0:
        return None
    end = text.rfind("}") + 1
    if end <= start:
        return None

    try:
        payload = json.loads(text[start:end])
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None

    raw_sections = payload.get("sections")
    if not isinstance(raw_sections, list) or not raw_sections:
        return None

    sections: list[dict[str, Any]] = []
    for i, s in enumerate(raw_sections):
        if not isinstance(s, dict):
            continue
        try:
            ts = max(0.0, float(s["time_start"]))
            te = float(s["time_end"])
        except (KeyError, TypeError, ValueError):
            continue  # 无时间戳的段对规划不可用
        if duration > 0:
            ts = min(ts, duration)
            te = min(te, duration)
        if te <= ts:
            continue
        sections.append({
            "section_id": str(s.get("section_id") or f"s{i + 1}"),
            "role": str(s.get("role") or "argument"),
            "time_start": ts,
            "time_end": te,
            "summary": str(s.get("summary") or ""),
            "visual_intent": str(s.get("visual_intent") or ""),
            "key_points": [str(k) for k in (s.get("key_points") or []) if k],
        })
    if not sections:
        return None
    sections.sort(key=lambda x: x["time_start"])

    palette = payload.get("palette")
    if not isinstance(palette, list):
        palette = []
    # 繁→简归一化（v1.4 模式套新层）：转录词流偶发繁体会被叙事步
    # 原样回显进 key_points — 归一化保证中间层与下游引导文案统一简体
    return _normalize_simplified({
        "theme": str(payload.get("theme") or ""),
        "palette": [str(c) for c in palette],
        "sections": sections,
    })


async def generate_narrative(
    llm: Any,
    transcript_segments: list[dict[str, Any]],
    duration: float,
    feedback: str | None = None,
) -> dict[str, Any] | None:
    """P2 两步链第一步：叙事结构分析（开场/论点/转折/收束 + 视觉意图）。

    产出是"可解释中间层"：第二步规划在此结构内落实时间轴/模板/参数；
    P0 审查反馈拼入此处做结构层反思。

    降级契约：LLM 不可用/输出不可解析 → None（调用方无声走单步链）。
    """
    if not llm or not transcript_segments:
        return None

    from app.adapters.llm_base import llm_prompt_to_text

    prompt = NARRATIVE_PROMPT.format(
        duration=duration,
        transcript_json=json.dumps(transcript_segments, ensure_ascii=False),
    )
    if feedback:
        prompt = (
            "上一版规划的视觉审查发现问题（请在结构分析时反思"
            "是否存在结构层面的原因）：\n"
            f"{feedback}\n\n{prompt}"
        )
    try:
        text = await llm_prompt_to_text(llm, prompt)
    except Exception as e:
        logger.warning(f"narrative generation failed: {e}")
        return None

    narrative = _parse_narrative_output(text, duration)
    if narrative is None:
        logger.warning("narrative output unparsable, fallback to single-step plan")
    return narrative


def overlay_coverage(plan: OverlayPlan, duration: float) -> float:
    """叠加时间覆盖率（并集/总时长）— 密度自检指标（ADR-004）。"""
    if duration <= 0 or not plan.segments:
        return 0.0
    intervals = sorted(
        (max(0.0, s.time_start), min(duration, s.time_end))
        for s in plan.segments
        if not s.skipped and s.time_end > s.time_start
    )
    if not intervals:
        return 0.0
    total = 0.0
    cur_start, cur_end = intervals[0]
    for start, end in intervals[1:]:
        if start > cur_end:
            total += cur_end - cur_start
            cur_start, cur_end = start, end
        else:
            cur_end = max(cur_end, end)
    total += cur_end - cur_start
    return min(total / duration, 1.0)


def largest_overlay_gap(
    plan: OverlayPlan, duration: float,
) -> tuple[float, float, float]:
    """时间轴上最长的连续无叠加区间：(空白秒数, 起点, 终点)。

    密度自检的第二指标（2026-10-28 样本多样性审计）：全局覆盖率只看
    总量，一百个叠加全挤在前十秒也照样达标 —— 但后 2/3 的片子等于
    没做增强。片头/片尾空白一并计入（开场 1-2s 无叠加属正常，
    由调用方阈值放行）。
    """
    if duration <= 0:
        return 0.0, 0.0, 0.0
    intervals = sorted(
        (max(0.0, s.time_start), min(duration, s.time_end))
        for s in plan.segments
        if not s.skipped and s.time_end > s.time_start
    )
    if not intervals:
        return duration, 0.0, duration

    best = (intervals[0][0], 0.0, intervals[0][0])  # 片头空白
    cur_end = intervals[0][1]
    for start, end in intervals[1:]:
        if start > cur_end:
            gap = start - cur_end
            if gap > best[0]:
                best = (gap, cur_end, start)
        cur_end = max(cur_end, end)

    tail = duration - cur_end
    if tail > best[0]:
        best = (tail, cur_end, duration)
    return best


def _normalize_simplified(obj: Any) -> Any:
    """递归繁体→简体归一化（zh-cn 地区变体：對著→对着，显著 不受影响）。

    LLM 偶发输出繁体：观众观感差，且与 whisper 简体词流字符失配
    会把词级对齐降级成均分（破坏 OVE 规则 1）。缺 zhconv 时原样返回（零件可缺）。
    """
    try:
        from zhconv import convert
    except ImportError:
        return obj
    if isinstance(obj, str):
        return convert(obj, "zh-cn")
    if isinstance(obj, dict):
        return {k: _normalize_simplified(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_normalize_simplified(v) for v in obj]
    return obj


# v4 撞色盘（LLM 未给/给不足 3 色时的保底）
_FALLBACK_PALETTES: list[list[str]] = [
    ["#FF4D4D", "#FFE066", "#4ECDC4"],
    ["#FF9F43", "#A78BFA", "#22C55E"],
    ["#FF6B6B", "#4ECDC4", "#FFD93D"],
]

# 收束卡模板（结尾必须有其一）
_CLOSER_TEMPLATES = {OverlayTemplate.FULL_CARD, OverlayTemplate.TITLE_CARD}


def _normalize_hex(c: Any) -> str | None:
    """hex 颜色归一化为 #RRGGBB 大写；非法返回 None。"""
    if not isinstance(c, str):
        return None
    h = c.strip()
    if not h:
        return None
    if not h.startswith("#"):
        h = "#" + h
    body = h[1:].upper()
    if len(body) == 3 and all(ch in "0123456789ABCDEF" for ch in body):
        body = "".join(ch * 2 for ch in body)
    if len(body) == 6 and all(ch in "0123456789ABCDEF" for ch in body):
        return "#" + body
    return None


def _parse_plan_output(
    result_text: str,
    duration: float,
) -> tuple[list[OverlaySegment], list[str], list[dict[str, Any]]]:
    """解析 LLM 规划输出 — v4 双格式兼容：
      新格式 {{"palette": [...], "overlays": [...]}}；旧格式 [...] 纯数组。

    palette 逐项归一化（非法剔除；可能不足 3 色，由 _ensure_palette 补齐）。
    LLM 输出加固（anti-hallucination：不信任外部输出）—
    时间越界 clamp、坏段跳过，单个错误不否决整个规划。

    Returns:
        (segments, palette(归一化后), raw_results)
    """
    brace = result_text.find("{")
    bracket = result_text.find("[")
    starts = [i for i in (brace, bracket) if i >= 0]
    if not starts:
        raise ValueError("LLM output contains no JSON")
    json_start = min(starts)
    if json_start == brace:
        payload = json.loads(result_text[json_start:result_text.rfind("}") + 1])
        raw_results = payload.get("overlays") or []
        raw_palette = payload.get("palette") or []
    else:
        raw_results = json.loads(result_text[json_start:result_text.rfind("]") + 1])
        raw_palette = []

    palette: list[str] = []
    for c in raw_palette:
        h = _normalize_hex(c)
        if h and h not in palette:
            palette.append(h)

    segments = []
    for i, item in enumerate(raw_results):
        raw_tpl = str(item.get("template", "caption_pop") or "caption_pop")
        try:
            template: str = OverlayTemplate(raw_tpl)  # type: ignore[assignment]
        except ValueError:
            # P1 模板生成器：未知模板名（gen_*）不丢弃 — 渲染前由
            # generate_template 现场生成，失败才降级 skip
            template = raw_tpl

        start = max(0.0, float(item.get("time_start", 0) or 0))
        end = max(0.0, float(item.get("time_end", 0) or 0))
        if duration > 0:
            start = min(start, duration)
            end = min(end, duration)
        if end - start < 0.5:
            logger.warning(
                "skip invalid overlay segment %d: [%s, %s]",
                i, item.get("time_start"), item.get("time_end"),
            )
            continue

        segments.append(OverlaySegment(
            segment_id=item.get("segment_id", f"o{len(segments)}"),
            time_start=round(start, 3),
            time_end=round(end, 3),
            template=template,
            params=_normalize_simplified(item.get("params", {})),
        ))

    # 段号唯一化 — 重规划 LLM 偶发输出重复 segment_id（2026-10-27 run9
    # 实证：两个 o20 → 渲染文件 overlay_o20.mp4 互相覆盖 → 成片时间窗
    # 错配/空屏）。重编号保内容：后出现的重复段取现有最大编号 +1 递增。
    seen_ids: set[str] = set()
    max_num = 0
    for s in segments:
        if s.segment_id.startswith("o") and s.segment_id[1:].isdigit():
            max_num = max(max_num, int(s.segment_id[1:]))
    for i, s in enumerate(segments):
        if s.segment_id in seen_ids:
            max_num += 1
            new_id = f"o{max_num}"
            logger.warning(
                "duplicate segment_id %s at index %d → renamed %s",
                s.segment_id, i, new_id,
            )
            segments[i] = s.model_copy(update={"segment_id": new_id})
            seen_ids.add(new_id)
        else:
            seen_ids.add(s.segment_id)

    return segments, palette, raw_results


def _ensure_palette(palette: list[str]) -> list[str]:
    """主题色保底 — LLM 未给/给不足 3 色时从撞色盘补齐（确定性）。"""
    out: list[str] = []
    seen: set[str] = set()
    for c in palette:
        h = _normalize_hex(c)
        if h and h not in seen:
            seen.add(h)
            out.append(h)
    for fallback in _FALLBACK_PALETTES:
        for c in fallback:
            if len(out) >= 3:
                return out
            if c not in seen:
                seen.add(c)
                out.append(c)
    return out


def validate_plan_rhythm(plan: OverlayPlan, duration: float) -> list[str]:
    """v4 节奏/主题自检 — 返回问题描述列表（warning 级，不阻断渲染）。

    规则：palette ≥3 色；开场 3s 内 ≥2 个叠加；结尾有收束卡；显式用色来自盘内。
    """
    issues: list[str] = []
    segs = [s for s in plan.segments if not s.skipped]
    if not segs:
        return issues
    if len(plan.palette) < 3:
        issues.append("palette 少于 3 色，全片主题色不统一")
    if len([s for s in segs if s.time_start < 3.0]) < 2:
        issues.append("开场 3s 内叠加不足 2 个（缺开场轰炸）")
    if duration >= 10 and not any(
        s.time_end >= duration - 3.0 and s.template in _CLOSER_TEMPLATES
        for s in segs
    ):
        issues.append("结尾缺少收束卡（full_card/title_card）")
    if plan.palette:
        allowed = set(plan.palette)
        off = sum(
            1
            for s in segs for v in s.params.values()
            if (h := _normalize_hex(v)) and h not in allowed
        )
        if off:
            issues.append(f"{off} 个叠加的用色不在盘内")
    return issues


async def generate_overlay_plan(
    transcript_segments: list[dict[str, Any]],
    duration: float,
    llm: Any = None,
    feedback: str | None = None,
    use_two_step: bool = False,
) -> tuple[OverlayPlan, list[dict[str, Any]]]:
    """LLM 规划叠加动画 — 对齐 ossclip 的 LLM producer brain。

    LLM 从预定义模板库选参数，不自由生成 HTML。

    Args:
        transcript_segments: 转录片段
        duration: 视频时长
        llm: LLM 客户端
        feedback: P0 视觉自审闭环的外部反馈（overlay_review 审出的问题）—
            拼在 prompt 头部，让 LLM 重新规划时针对性修复。None = 初次规划。
        use_two_step: P2 两步链 — True 时先跑叙事结构分析（开场/论点/
            转折/收束 + 视觉意图），叙事块拼在规划 prompt 头部；叙事
            失败无声降级单步链（feedback 此时也进叙事 prompt 做结构反思）。

    Returns:
        (叠加规划, LLM 原始响应用于调试)
    """
    if not llm or not transcript_segments:
        return OverlayPlan(video_id=""), []

    from app.adapters.llm_base import llm_prompt_to_text

    # P2 两步链第一步：叙事结构分析（失败 None → 无声降级单步）
    narrative = None
    if use_two_step:
        narrative = await generate_narrative(
            llm, transcript_segments, duration, feedback=feedback,
        )

    transcript_json = json.dumps(transcript_segments, ensure_ascii=False)
    min_segments = max(3, int(duration / 15))
    prompt = OVERLAY_PLAN_PROMPT.format(
        duration=duration,
        transcript_json=transcript_json,
        min_segments=min_segments,
    )
    if narrative:
        prompt = _NARRATIVE_TO_PLAN_HEADER.format(
            narrative_json=json.dumps(narrative, ensure_ascii=False),
        ) + "\n\n" + prompt
    if feedback:
        prompt = f"{feedback}\n\n{prompt}"

    for attempt in range(3):
        try:
            result_text = await llm_prompt_to_text(llm, prompt)

            segments, palette, raw_results = _parse_plan_output(result_text, duration)

            plan = OverlayPlan(
                video_id="",
                segments=segments,
                palette=palette,
                narrative=narrative,
            )

            # 密度自检（ADR-004）：覆盖率过低 → 带反馈重试
            # duration 探测失败时退化为最大段尾（避免 0 分母导致覆盖率恒 0 误判）
            coverage_duration = duration if duration > 0 else max(
                (s.time_end for s in segments), default=0.0
            )
            coverage = overlay_coverage(plan, coverage_duration)
            gap, gap_start, gap_end = largest_overlay_gap(plan, coverage_duration)
            # 空白阈值：至少 6s 容差，长片按 8% 放宽 —— 实测合格产物
            # （2026-10-27 run12 全家桶）最大空白 1.9~3.4s，6s 不误报；
            # 180s 片上限 14.4s（伪影变体只覆盖前 26s → 154s 空白被拦）
            gap_limit = max(6.0, coverage_duration * 0.08)
            too_sparse = coverage < 0.30
            too_gappy = gap > gap_limit
            if (too_sparse or too_gappy) and attempt < 2:
                logger.warning(
                    "overlay plan density check failed: coverage %.0f%% "
                    "(min 30%%), max gap %.1fs @%.1f-%.1fs (limit %.1fs), attempt %d",
                    coverage * 100, gap, gap_start, gap_end, gap_limit, attempt + 1,
                )
                problems: list[str] = []
                if too_sparse:
                    problems.append(
                        f"整体密度不足：{len(segments)} 个叠加，覆盖率仅 "
                        f"{coverage * 100:.0f}%（要求 ≥30%，最少 {min_segments} 个）"
                    )
                if too_gappy:
                    problems.append(
                        f"分布不均：{gap_start:.0f}s~{gap_end:.0f}s 连续 {gap:.0f}s "
                        f"没有任何叠加（要求任意空档 ≤{gap_limit:.0f}s）"
                    )
                prompt = (
                    "上次规划未通过密度自检：" + "；".join(problems) + "。\n"
                    "请重新规划：叠加必须铺满全片（开场/中段/收束都有），"
                    "每个关键论点都要有视觉强化，不要集中在前段；"
                    "并提高模板多样性（至少 4 种，多用具色块/气泡/emoji 类强视觉模板），"
                    "caption_pop 记得传 accent_words。\n"
                    f"原始请求：{prompt}"
                )
                continue

            # v4 节奏/主题自检（warning 级，不重试不阻断 — LLM 已尽力，规则兜底在视觉上可容）
            for issue in validate_plan_rhythm(plan, coverage_duration):
                logger.warning("overlay plan rhythm check: %s", issue)

            plan.palette = _ensure_palette(plan.palette)
            logger.info(
                f"LLM overlay plan: {len(segments)} segments from {len(raw_results)} "
                f"results, coverage {coverage * 100:.0f}%, palette {plan.palette}"
            )
            return plan, raw_results

        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"LLM overlay plan format invalid (attempt {attempt + 1}): {e}")
            prompt = (
                f"上次输出格式有误：{e}\n"
                "请严格按 JSON 格式重新输出（palette + overlays）。\n"
                f"原始请求：{prompt}"
            )
        except Exception as e:
            logger.warning(f"LLM overlay plan failed (attempt {attempt + 1}): {e}")
            break

    logger.warning("LLM overlay plan failed after 3 attempts")
    return OverlayPlan(video_id=""), []


# ── 10.19 对话式修改层：意图解析 + patch 执行器（三重锁） ────────

class RevisionAction(str, Enum):
    """修改动作白名单 — ADD 参数空间大，第一期不上（走 global 重规划）。"""
    UPDATE = "update"
    DELETE = "delete"
    MOVE = "move"


class RevisionInstruction(BaseModel):
    """一条修改指令 — LLM 解析产物，纯数据。"""
    segment_ids: list[str] = Field(default_factory=list)
    action: RevisionAction
    params: dict[str, Any] = Field(default_factory=dict)
    locate_hint: str = ""   # LLM 回显的定位依据（定位失败时随问题返给用户）
    raw_text: str = ""      # 用户原话（审计）


class RevisionResult(BaseModel):
    """意图解析结果 — local=代码级 patch，global=feedback 重规划。"""
    route: str  # "local" | "global"
    instructions: list[RevisionInstruction] = Field(default_factory=list)
    reason: str = ""


# UPDATE 禁改键：时间走 MOVE，模板/定位/裁决标记不许 LLM 碰
_UPDATE_FORBIDDEN_KEYS = {"time_start", "time_end", "segment_id", "template", "skipped"}
_HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _revision_plan_digest(plan: OverlayPlan) -> str:
    """当前规划的摘要（喂给意图解析 prompt — LLM 需要知道有哪些段可改）。"""
    lines = []
    for s in plan.segments:
        if s.skipped:
            continue
        params = {
            k: v for k, v in list(s.params.items())[:6]
            if isinstance(v, (str, int, float))
        }
        lines.append(json.dumps({
            "segment_id": s.segment_id,
            "template": template_name(s.template),
            "time": f"{s.time_start:.1f}-{s.time_end:.1f}",
            "params": params,
        }, ensure_ascii=False))
    return "\n".join(lines)


def _strip_json_fences(raw: str) -> str:
    """剥掉 LLM 爱加的 ```json 围栏。"""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


REVISION_PARSE_PROMPT = """你是口播视频叠加规划的修改意图解析器。用户看着当前叠加规划说了修改意见，
你的任务是把它解析成结构化修改指令。

当前叠加规划（segment_id | 模板 | 时间 | 参数）：
{plan_digest}

用户原话：「{raw_text}」

判断路由：
- scope=global：用户要的是整体风格/节奏/密度级别的调整（如"整体再活泼点""太单调了"
  "重新规划"），或意见无法落到具体段上 — 交回上层走全量重规划。
- scope=local：用户明确指向一个或几个具体段做具体修改。此时输出 instructions。

local 硬性约束：
- segment_ids 必须从上面当前规划里选，逐字一致；定位拿不准就归 global，不要猜
- action 只能是 update / delete / move（add 不支持 — 加新叠加请归 global）
- update 的 params 只放要改的参数键值；不许改 time_start/time_end（挪时间用 move）、
  不许改 template（换模板请归 global）
- move 的 params 必须同时给 time_start 和 time_end（绝对秒数）
- locate_hint 写清你的定位依据（如"第二个 caption_pop"），便于定位失败时向用户确认

输出 JSON（不要解释文字、不要代码围栏）：
{{"scope": "local", "instructions": [{{"segment_ids": ["seg1"], "action": "update", "params": {{"text": "新文案"}}, "locate_hint": "开场字幕"}}]}}
或
{{"scope": "global", "reason": "整体风格调整"}}"""


async def parse_revision_intent(
    llm: Any,
    raw_text: str,
    current_plan: OverlayPlan,
    duration: float,
) -> tuple[RevisionResult | None, list[str]]:
    """用户反馈 → 修改意图（三重锁第 1 层：LLM 只产纯数据）。

    Returns:
        (result, problems) — result=None 表示彻底解析失败（坏 JSON/LLM 异常），
        上层反问用户；route=local 且 instructions=[] 表示有指令但全部被拒
        （越权/定位失败），problems 带定位依据供上层反问。
    """
    if not llm:
        return None, ["LLM 不可用，无法解析修改意图"]
    if not raw_text.strip():
        return None, ["修改意见为空"]

    from app.adapters.llm_base import llm_prompt_to_text

    prompt = REVISION_PARSE_PROMPT.format(
        plan_digest=_revision_plan_digest(current_plan) or "（当前规划为空）",
        raw_text=raw_text.strip(),
    )
    try:
        raw = await llm_prompt_to_text(llm, prompt)
    except Exception as e:
        logger.warning(f"[revision] LLM 意图解析失败：{e}")
        return None, [f"意图解析调用失败：{e}"]

    try:
        data = json.loads(_strip_json_fences(raw))
    except (json.JSONDecodeError, TypeError):
        return None, [f"意图输出不是合法 JSON：{str(raw)[:120]!r}"]
    if not isinstance(data, dict) or data.get("scope") not in ("local", "global"):
        return None, ["意图输出缺少 scope（local/global）"]
    if data["scope"] == "global":
        return RevisionResult(route="global", reason=str(data.get("reason") or "")), []

    problems: list[str] = []
    valid_ids = {s.segment_id for s in current_plan.segments}
    instructions: list[RevisionInstruction] = []
    for item in data.get("instructions") or []:
        if not isinstance(item, dict):
            continue
        hint = str(item.get("locate_hint") or "")
        try:
            action = RevisionAction(str(item.get("action") or ""))
        except ValueError:
            problems.append(
                f"不支持的动作 {item.get('action')!r}（只支持 update/delete/move，"
                f"定位依据：{hint or '未提供'}）"
            )
            continue
        ids = [str(i) for i in (item.get("segment_ids") or [])]
        missing = [i for i in ids if i not in valid_ids]
        if not ids or missing:
            problems.append(
                f"定位失败：segment_ids {missing or ids or '为空'} 不在当前规划中"
                f"（定位依据：{hint or '未提供'}）"
            )
            continue
        instructions.append(RevisionInstruction(
            segment_ids=ids,
            action=action,
            params=dict(item.get("params") or {}),
            locate_hint=hint,
            raw_text=raw_text.strip(),
        ))
    return RevisionResult(route="local", instructions=instructions), problems


def apply_revision(
    plan: OverlayPlan,
    instructions: list[RevisionInstruction],
    duration: float,
) -> tuple[OverlayPlan, list[str]]:
    """代码级 patch 当前规划（三重锁第 2 层：白名单执行器，纯函数）。

    只动指令指到的段 — 其余段逐字节不动（局部修改不波及无辜）。
    patch 后强制过 resolve_overlay_conflicts（同带重叠裁决）+
    validate_plan_rhythm（warning 级问题并入返回，如实上报不静默修）。
    """
    problems: list[str] = []
    patched = plan.model_copy(deep=True)
    by_id = {s.segment_id: s for s in patched.segments}

    for ins in instructions:
        targets = [by_id[i] for i in ins.segment_ids if i in by_id]
        if len(targets) != len(ins.segment_ids):
            missing = [i for i in ins.segment_ids if i not in by_id]
            problems.append(
                f"定位失败：{missing} 不在当前规划中（依据：{ins.locate_hint or ins.raw_text}）"
            )
            continue

        if ins.action == RevisionAction.DELETE:
            for seg in targets:
                seg.skipped = True  # 保留可追溯（对齐冲突消解裁决先例）
            continue

        if ins.action == RevisionAction.MOVE:
            ts, te = ins.params.get("time_start"), ins.params.get("time_end")
            if not isinstance(ts, (int, float)) or not isinstance(te, (int, float)):
                problems.append(
                    f"move 必须同时给 time_start/time_end 数值（依据：{ins.locate_hint}）"
                )
                continue
            for seg in targets:
                orig = (seg.time_start, seg.time_end)
                seg.time_start = max(0.0, min(float(ts), duration))
                seg.time_end = max(0.0, min(float(te), duration))
                if seg.time_end <= seg.time_start:
                    problems.append(
                        f"move 后 end<=start（{seg.segment_id}: "
                        f"{seg.time_start}-{seg.time_end}），已回退原时间"
                    )
                    seg.time_start, seg.time_end = orig
            continue

        # UPDATE：禁键校验 + 颜色格式校验 + 数值型字段校验，其余键 merge
        forbidden = [k for k in ins.params if k in _UPDATE_FORBIDDEN_KEYS]
        bad_color = [
            k for k, v in ins.params.items()
            if ("color" in k) and not (isinstance(v, str) and _HEX_COLOR_RE.match(v))
        ]
        # 10.23-B：数值型字段（像素类）必须数值且在合理域 — LLM 曾给
        # font_size='larger'（无效 CSS，模板参数是 int px）
        _NUMERIC_KEYS = {"font_size"}
        bad_numeric = [
            k for k, v in ins.params.items()
            if k in _NUMERIC_KEYS and not (
                isinstance(v, (int, float)) and not isinstance(v, bool)
                and 8 <= v <= 300
            )
        ]
        if forbidden or bad_color or bad_numeric:
            for k in forbidden:
                problems.append(
                    f"update 不许改 {k}（挪时间用 move / 换模板请走全局重规划，"
                    f"依据：{ins.locate_hint}）"
                )
            for k in bad_color:
                problems.append(
                    f"update 的 {k} 必须是 #rrggbb 格式（依据：{ins.locate_hint}）"
                )
            for k in bad_numeric:
                problems.append(
                    f"update 的 {k} 必须是数值（像素，8-300）（依据：{ins.locate_hint}）"
                )
            continue
        for seg in targets:
            seg.params.update(ins.params)

    # patch 后强制自检：同带重叠裁决 + 节奏自检（warning 如实上报）
    patched = resolve_overlay_conflicts(patched)
    coverage_duration = duration if duration > 0 else max(
        (s.time_end for s in patched.segments), default=0.0
    )
    problems.extend(validate_plan_rhythm(patched, coverage_duration))
    return patched, problems