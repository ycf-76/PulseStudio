"""叠加动画视觉自审服务 — P0 视觉自审闭环（2026-10-27）。

设计依据（《AI口播剪辑智能度分析报告.md》P0 路线）：
  - 全行业开源项目（ossclip / edit-greek-reel / AutoCut / FunClip）都没有
    自动审片闭环 — 渲染完直接出片，效果烂用户只能重跑。这是差异化点。
  - 本项目渲染是确定性的（seekTo 时间轴替代 CSS keyframes）：同一规划
    必然渲染出同一画面 → VLM 审出的问题可信、带反馈重规划后可验证。
  - 降级契约（最重要）：VLM / ffmpeg / 抽帧任何环节不可用 → 返回 None，
    调用方跳过审查直接出片。**审查是增值步骤，绝不是管线闸门。**

流水线：
  preview_pairs (seg, 黑底预览 mp4)
    → _pick_frame_plan 选帧（中点必选，长段补入/出帧，预算封顶）
    → extract_review_frames ffmpeg 定点抽帧
    → 底片参考帧（判遮挡：字幕挡脸等黑底上不可见的问题）
    → QwenVLAdapter 多图审查（模式仿 vl_understand_images）
    → _parse_review_output 防御式解析（坏 JSON → passed 降级）
    → build_replan_feedback → generate_overlay_plan(feedback=...) 重规划
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any, NamedTuple

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# 总帧预算：叠加帧 ≤6 + 底片参考帧 2 = 8 张图（qwen-vl-max 多图安全上限内）
MAX_OVERLAY_FRAMES = 6
MAX_BASE_FRAMES = 2

# 帧 payload 控制：实测叠加帧 PNG ~560KB，JPEG ~30-60KB（-88% 以上）。
# 叠加预览是大色块平面设计，896px/q75 足够 VLM 判断塌缩/溢出/配色。
MAX_FRAME_SIDE = 896
JPEG_QUALITY = 75

# 请求体预算：实测（2026-10-27）请求体 >~145KB 会被网络中间层掐断
# （SSLEOFError，与代理无关 — 136KB 过、156KB 炸；特征吻合安全软件
# ~128KB TLS 流重组窗口）→ 帧按实测 base64 尺寸分块，每块一次 VLM 调用。
MAX_CALL_PAYLOAD = 110_000
_PER_ITEM_OVERHEAD = 2_048  # 每帧分摊的 prompt/JSON/TLS 开销余量


class _ChunkItem(NamedTuple):
    """一个待审帧：meta=OverlaySegment（叠加帧）或 None（底片参考帧）。"""
    meta: Any | None
    uri: str
    tag: str = "mid"  # mid/settled/in/out/base — 帧时刻性质（VLM 防误报依据）


def _pack_chunks(
    items: list[_ChunkItem],
    budget: int = MAX_CALL_PAYLOAD,
) -> list[list[_ChunkItem]]:
    """按请求体预算装箱（保序）。单项超预算独占一块（不丢帧）。"""
    chunks: list[list[_ChunkItem]] = []
    cur: list[_ChunkItem] = []
    cur_size = 0
    for item in items:
        n = len(item.uri) + _PER_ITEM_OVERHEAD
        if cur and cur_size + n > budget:
            chunks.append(cur)
            cur, cur_size = [], 0
        cur.append(item)
        cur_size += n
    if cur:
        chunks.append(cur)
    return chunks


def _frame_to_data_uri(path: str | Path) -> str | None:
    """帧文件 → JPEG base64 data URI（压缩请求体）。

    PIL 缺失/转换失败 → 退回原始 base64（url_to_base64，PNG 大图能发就发）；
    全部失败返回 None（调用方跳过该帧）。
    """
    try:
        import base64
        import io

        from PIL import Image

        im = Image.open(path).convert("RGB")
        w, h = im.size
        if max(w, h) > MAX_FRAME_SIDE:
            scale = MAX_FRAME_SIDE / max(w, h)
            im = im.resize(
                (round(w * scale), round(h * scale)), Image.LANCZOS,
            )
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=JPEG_QUALITY)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception as e:
        logger.debug(f"[overlay_review] frame JPEG convert failed ({path}): {e}")
    try:
        from app.services.asset_library import url_to_base64
        return url_to_base64(str(path))
    except Exception:
        return None


class ReviewIssue(BaseModel):
    """单条审查问题。"""
    segment_id: str = ""          # 关联叠加段；空串 = 全片性问题
    severity: str = "suggestion"  # problem（必须修） | suggestion（可优化）
    problem: str = ""             # 问题描述
    suggestion: str = ""          # 修改方向


class ReviewResult(BaseModel):
    """一轮视觉审查的结论。"""
    issues: list[ReviewIssue] = Field(default_factory=list)
    passed: bool = True

    @property
    def critical_issues(self) -> list[ReviewIssue]:
        """problem 级问题 — 触发带反馈重规划的判据。"""
        return [i for i in self.issues if i.severity == "problem"]


REVIEW_PROMPT = """你是口播视频叠加动画的视觉审查员。下面按顺序给你 {n} 张帧：
前 {n_base} 张是底片参考画面（原视频抽帧，无叠加），其余是叠加动画的渲染预览帧
（黑底，只含叠加层元素——字幕/色块/卡片/emoji/箭头等）。

帧清单：
{frame_list}

全片主题色盘：{palette}

逐帧检查叠加预览帧是否存在以下真实可见的问题：
1. 文字：为空或缺失、塌缩挤成一团、溢出边界被截断、乱码
2. 元素：图形/色块/emoji 缩成几乎看不见的小点、多个元素堆叠错位
3. 布局：卡片/分屏明显失衡（一侧完全空白）、内容整体只占画面极小一角
4. 配色：文字与所在色块对比度过低看不清、颜色杂乱刺眼、与主题色盘严重冲突
5. 遮挡：对照底片参考帧，叠加元素落在人物面部/主体上且明显遮挡
   （底部字幕区压到下巴以下属正常，只报真的盖住脸的情况）
6. 指向：箭头/圆圈完全孤立（没有任何标签或配套图形）

注意：
- 黑底预览里没有人物/视频内容是正常的，不要报告"画面没有人"
- 帧是动画的瞬时快照：帧清单标注"入场早期态"的帧中，部分元素尚未出现
  属正常动画过程，不要报告为"缺失/未显示/未高亮"；判断某段元素是否
  缺失或高亮不全，以稳定态帧（标注"动画完成后"）为准
- emoji 图标（🧢☀️🥶 等）的颜色由系统字体决定，不受 params.color
  控制：不要报告"emoji 颜色与主题色/params.color 不符"类问题
- 标题/文字的换行：若换行点是词组边界且无孤字，属正常排版；
  只有"单字孤行"或"词被拦腰截断"才值得报告
- 只报告真实可见的问题，宁缺勿滥，不要为凑数编造
- 问题要具体：哪一帧、什么元素、什么现象
- severity: problem = 明显缺陷必须修；suggestion = 可优化不阻断

只输出 JSON（不要 markdown 代码块）：
{{"issues": [{{"segment_id": "o3", "severity": "problem", "problem": "描述具体现象", "suggestion": "具体修改方向"}}], "pass": false}}
没有 problem 级问题时 pass 必须为 true。"""


async def extract_review_frames(
    video_path: str | Path,
    timestamps: list[float],
    out_dir: str | Path,
    prefix: str = "frame",
) -> list[Path]:
    """从视频抽取指定时刻的 PNG 帧 — 每个时间戳一帧。

    防御式（审查绝不阻断管线）：负时间戳 clamp 到 0；抽帧失败/空文件跳过；
    ffmpeg 不可用返回空。绝不抛异常。

    已验证可用的抽帧命令（-ss 在 -i 前 = 快速 seek）：
      [FF, "-y", "-ss", t, "-i", VIDEO, "-frames:v", "1", dst]
    """
    from capabilities.video.adapters.html_to_video import resolve_ffmpeg

    ffmpeg = resolve_ffmpeg()
    if ffmpeg is None:
        return []

    src = Path(video_path)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    frames: list[Path] = []
    for i, t in enumerate(timestamps):
        t = max(0.0, float(t))
        dst = out / f"{prefix}_{i:02d}.png"
        try:
            proc = await asyncio.create_subprocess_exec(
                ffmpeg, "-y", "-loglevel", "error",
                "-ss", f"{t:.3f}", "-i", str(src),
                "-frames:v", "1", str(dst),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.communicate()
        except Exception as e:
            logger.debug(f"[overlay_review] extract frame {prefix}@{t:.2f}s failed: {e}")
            continue
        if dst.is_file() and dst.stat().st_size > 0:
            frames.append(dst)
        else:
            dst.unlink(missing_ok=True)
    return frames


# caption 三件套是逐词弹出动画 — 抽帧必须避开入场中间态
# （2026-10-27 demo round2 误报实证：段中点后半句词 opacity=0，
# VLM 把"还没弹出"判成"词缺失/未高亮/params 不一致"）
_CAPTION_TEMPLATES = {"caption_pop", "caption_karaoke", "caption_typewriter"}


def _pick_frame_plan(
    pairs: list[tuple[Any, Path]],
    max_frames: int = MAX_OVERLAY_FRAMES,
) -> list[tuple[Any, Path, float, str]]:
    """选帧计划：[(seg, 预览视频, 抽帧时刻, tag)]。

    策略（帧预算内最大化信息量）：
      - 段数 > 预算 → 按时间轴均匀抽样段（开场/中段/结尾都有代表帧）
      - 每段中点必选（动画完全展开态，最能暴露塌缩/溢出/配色问题）
      - caption 三件套例外：逐词弹出动画的中点=半入场态（后半句
        opacity=0），VLM 必然误报 → 抽稳定态 settled（出场前 0.25s，
        不早于中点），且不补 in/out 帧（入场早期帧是噪声，out 与
        settled 同时刻重复）
      - 剩余预算给最长的段补 in（入场后 0.35s）/ out（出场前 0.25s）帧
      - 短段（≤2s）中点一帧足够，不浪费预算
    """
    active = [(seg, vid) for seg, vid in pairs if not getattr(seg, "skipped", False)]
    if not active:
        return []

    if len(active) > max_frames:
        # 均匀抽样 max_frames 段（按 preview_pairs 原顺序 = 渲染顺序）
        n = len(active)
        idxs = sorted({round(i * (n - 1) / (max_frames - 1)) for i in range(max_frames)})
        active = [active[i] for i in idxs]

    def _is_caption(seg: Any) -> bool:
        from capabilities.video.services.overlay_orchestrator import template_name
        return template_name(seg.template) in _CAPTION_TEMPLATES

    plan: list[tuple[Any, Path, float, str]] = []
    for seg, vid in active:
        dur = seg.time_end - seg.time_start
        if _is_caption(seg):
            plan.append((seg, vid, max(dur / 2.0, dur - 0.25), "settled"))
        else:
            plan.append((seg, vid, dur / 2.0, "mid"))

    budget = max_frames - len(plan)
    # 长段补 in/out 帧（按时长降序分配剩余预算）
    for seg, vid in sorted(
        active, key=lambda p: p[0].time_end - p[0].time_start, reverse=True,
    ):
        if budget <= 0:
            break
        if _is_caption(seg):
            continue  # settled 已是稳定态；in 是半入场噪声，out 与 settled 重复
        dur = seg.time_end - seg.time_start
        if dur <= 2.0:
            continue
        plan.append((seg, vid, min(0.35, dur / 2.0), "in"))
        budget -= 1
        if budget > 0:
            plan.append((seg, vid, max(dur - 0.25, dur / 2.0 + 0.01), "out"))
            budget -= 1
    return plan


def _parse_review_output(text: str) -> ReviewResult:
    """解析 VLM 审查输出 — 防御式（仿 _parse_plan_output）。

    坏 JSON / 无 JSON / 非 dict issue / 非法 severity / 空内容：
    全部安全跳过；解析整体失败 → passed=True 降级（审查失败绝不阻断出片）。
    pass 字段缺失时由"是否存在 problem 级 issue"推断。
    """
    start = text.find("{")
    if start < 0:
        return ReviewResult(passed=True)
    end = text.rfind("}") + 1
    if end <= start:
        return ReviewResult(passed=True)

    try:
        payload = json.loads(text[start:end])
    except (json.JSONDecodeError, ValueError):
        logger.warning("[overlay_review] VLM 输出非合法 JSON，降级为通过")
        return ReviewResult(passed=True)
    if not isinstance(payload, dict):
        return ReviewResult(passed=True)

    issues: list[ReviewIssue] = []
    raw_issues = payload.get("issues")
    if isinstance(raw_issues, list):
        for item in raw_issues:
            if not isinstance(item, dict):
                continue
            severity = str(item.get("severity") or "suggestion")
            if severity not in {"problem", "suggestion"}:
                severity = "suggestion"
            problem = str(item.get("problem") or "").strip()
            suggestion = str(item.get("suggestion") or "").strip()
            if not problem and not suggestion:
                continue
            issues.append(ReviewIssue(
                segment_id=str(item.get("segment_id") or ""),
                severity=severity,
                problem=problem,
                suggestion=suggestion,
            ))

    raw_pass = payload.get("pass")
    if isinstance(raw_pass, bool):
        passed = raw_pass
    else:
        passed = not any(i.severity == "problem" for i in issues)

    return ReviewResult(issues=issues, passed=passed)


def build_replan_feedback(result: ReviewResult) -> str:
    """审查结论 → 重规划反馈文本（拼进 generate_overlay_plan 的 prompt）。

    复用 generate_overlay_plan 内部重试的反馈模式：问题定位 + 修改方向，
    让 LLM 在重新规划时针对性修复（同位置同模板的问题不能再犯）。
    """
    if not result.issues:
        return ""
    lines = [
        "上一版叠加动画渲染后，视觉审查发现以下问题。"
        "重新规划时必须针对性修复（出现问题的位置/模板不要再犯同样错误）：",
    ]
    for i, issue in enumerate(result.issues, 1):
        loc = f"segment {issue.segment_id}" if issue.segment_id else "整体"
        body = issue.problem or issue.suggestion
        tag = "必须修复" if issue.severity == "problem" else "建议"
        lines.append(f"{i}. [{tag}] {loc}: {body}")
        if issue.problem and issue.suggestion:
            lines.append(f"   修改方向：{issue.suggestion}")
    return "\n".join(lines)


async def _review_chunk(
    adapter: Any,
    chunk: list[_ChunkItem],
    palette: list[str],
) -> ReviewResult | None:
    """单块帧 → 一次 VLM 审查调用。调用失败返回 None（跳过该块）。"""
    lines: list[str] = []
    _TAG_DESC = {
        "settled": "动画完成后的稳定态（此刻该段全部元素都应可见）",
        "in": "入场早期态（部分元素可能尚未出现，属正常动画过程）",
        "out": "出场前状态",
        "base": "底片参考画面（无叠加）",
    }
    n_base = 0
    for i, item in enumerate(chunk, 1):
        tag_desc = _TAG_DESC.get(item.tag, "动画展开中段")
        if item.meta is None:
            n_base += 1
            lines.append(f"- 第{i}帧: 底片参考画面（无叠加）")
        else:
            seg = item.meta
            from capabilities.video.services.overlay_orchestrator import template_name
            params_brief = json.dumps(seg.params, ensure_ascii=False)[:120]
            lines.append(
                f"- 第{i}帧: segment_id={seg.segment_id}, template={template_name(seg.template)}, "
                f"时间窗 [{seg.time_start:.1f}s, {seg.time_end:.1f}s], "
                f"帧时刻性质: {tag_desc}, params: {params_brief}"
            )
    prompt = REVIEW_PROMPT.format(
        n=len(chunk),
        n_base=n_base,
        frame_list="\n".join(lines),
        palette=", ".join(palette) or "未指定",
    )

    content: list[dict[str, Any]] = [{"text": prompt}]
    content.extend({"image": item.uri} for item in chunk)
    messages = [{"role": "user", "content": content}]

    try:
        result = await adapter.chat(messages)
    except Exception as e:
        logger.warning(f"[overlay_review] 本块 VLM 调用失败，跳过该块：{e}")
        return None

    # DashScope 多图输入 content 可能是 list[{"text": ...}] — 拉平
    # （2026-10-13 vl_understand_images 踩过的坑，text.find 直接 AttributeError）
    raw = result.get("content", "")
    if isinstance(raw, list):
        text = "".join(
            item.get("text", "") if isinstance(item, dict) else str(item)
            for item in raw
        )
    else:
        text = str(raw or "")
    return _parse_review_output(text)


def _validate_issue_refs(result: ReviewResult, plan: Any) -> ReviewResult:
    """程序化防幻觉校验 — 渲染确定性优势的延伸（params 是文字真值来源）。

    VLM 报问题时的引号引用文字必须能在该段 params 里找到（子串匹配），
    否则视为段号错位/幻觉，整条丢弃（2026-10-27 第六次 demo round1
    三条 problem 全属此类：把 o13/o17 的内容标到 o14/o18 头上）。
    - 无引号引用的描述性 issue（"元素溢出边界"）不受影响
    - segment_id 为空（全片问题）或无法关联具体文字的不校验
    - 校验全灭后 critical 归零 → passed 翻 true，避免无效重规划
    误杀权衡：宁可放过（保留存疑 issue）也不误杀 —— 引用命中任意
    一个即保留；只拦"引用了段里根本不存在的文字"这一种硬幻觉。
    """
    if not result.issues:
        return result
    seg_map: dict[str, Any] = {}
    for seg in getattr(plan, "segments", None) or []:
        seg_map[seg.segment_id] = seg

    kept: list[ReviewIssue] = []
    dropped = 0
    for issue in result.issues:
        seg = seg_map.get(issue.segment_id)
        if seg is None:
            # 段号不在 plan 里 → 无法取证。只有带引号引用的才丢弃
            # （编造段号+编造文字双重幻觉）；无引用的保留原样
            if _quote_refs(f"{issue.problem} {issue.suggestion}"):
                dropped += 1
                logger.warning(
                    "[overlay_review] issue 引用了不存在段落 %s 的文字，丢弃：%s",
                    issue.segment_id, (issue.problem or "")[:60])
                continue
            kept.append(issue)
            continue
        params_text = " ".join(
            str(v) for v in (seg.params or {}).values()
            if isinstance(v, str)
        )
        refs = _quote_refs(f"{issue.problem} {issue.suggestion}")
        if not refs or any(r in params_text for r in refs):
            kept.append(issue)
        else:
            dropped += 1
            logger.warning(
                "[overlay_review] issue 引用文字不在 segment %s params 中，"
                "判定段号错位/幻觉丢弃：%s",
                issue.segment_id, refs)

    if dropped:
        passed = all(i.severity != "problem" for i in kept)
        return ReviewResult(issues=kept, passed=passed)
    return result


# 引号字符类：直引号 ' " + 弯引号 ' ' " " + CJK 引号「」『』【】
# （2026-10-27 run9 实证：VLM 输出用弯引号 ' '，漏配 → 引用提取为空
# → 幻觉 issue 被当"无引用描述"放行）
_QUOTE_PAIRS = "'\"\u2018\u2019\u201c\u201d「」『』【】"
_QUOTE_RE = re.compile(rf"[{_QUOTE_PAIRS}]([^{_QUOTE_PAIRS}]{{2,20}})[{_QUOTE_PAIRS}]")


def _quote_refs(text: str) -> list[str]:
    """提取文本中的引号引用（≥2 字）— VLM 习惯用引号引述帧里的文字。"""
    return [q.strip() for q in _QUOTE_RE.findall(text or "") if len(q.strip()) >= 2]


async def review_overlays(
    plan: Any,
    preview_pairs: list[tuple[Any, Path]],
    work_dir: str | Path,
    base_video: str | None = None,
    max_frames: int = MAX_OVERLAY_FRAMES,
    max_call_payload: int = MAX_CALL_PAYLOAD,
) -> ReviewResult | None:
    """VLM 看图审查叠加渲染效果 — P0 闭环的审查步。

    Args:
        plan: OverlayPlan（取 palette 给 VLM 做配色依据）
        preview_pairs: [(seg, 黑底预览 mp4 路径)]（_render_previews 的产物）
        work_dir: 帧文件输出目录（tmp_dir，跑完自动清）
        base_video: 底片路径 — 抽 2 张参考帧供 VLM 判断遮挡（字幕挡脸）
        max_frames: 叠加帧预算
        max_call_payload: 单次 VLM 调用的请求体字节预算（超了分块多调）

    Returns:
        ReviewResult — 审查结论；None = VLM 不可用/前置失败，调用方降级跳过。
    """
    work_dir = Path(work_dir)
    try:
        from capabilities.video.adapters.html_to_video import resolve_ffmpeg
        if resolve_ffmpeg() is None:
            logger.info("[overlay_review] ffmpeg 不可用，跳过视觉审查")
            return None

        from app.config import get_settings
        api_key = (get_settings().dashscope_api_key or "").strip()
        if not api_key:
            logger.info("[overlay_review] 未配置 DASHSCOPE_API_KEY，跳过视觉审查")
            return None

        frame_plan = _pick_frame_plan(preview_pairs, max_frames)
        if not frame_plan:
            return None

        # ── 抽帧：叠加预览帧（精准定位每段） + 底片参考帧（判遮挡）──
        frames: list[tuple[Any | None, Path, str]] = []  # (seg, png, tag)；seg=None → 底片参考帧
        base_frame_paths: list[Path] = []
        if base_video:
            # 参考帧取全片 1/3、2/3 处（挡脸判断只需看到人物常态位置）
            base_duration = 0.0
            try:
                from capabilities.video.adapters.silence_detect import probe_duration
                base_duration = await probe_duration(base_video)
            except Exception:
                base_duration = 0.0
            if base_duration > 0:
                base_frame_paths = await extract_review_frames(
                    base_video,
                    [base_duration / 3.0, base_duration * 2.0 / 3.0],
                    work_dir, prefix="review_base",
                )
        for seg, vid, t, tag in frame_plan:
            got = await extract_review_frames(
                vid, [t], work_dir, prefix=f"review_{seg.segment_id}_{tag}",
            )
            if got:
                frames.append((seg, got[0], tag))
        if not frames:
            logger.warning("[overlay_review] 叠加帧抽取全部失败，跳过视觉审查")
            return None

        # ── 帧编码 + 按请求体预算分块 ──
        from capabilities.image_gen.adapters.qwen_vl import QwenVLAdapter
        from app.services.model_tier import PipelineStage, get_tier_router

        vl_model = get_tier_router().resolve(PipelineStage.VL_UNDERSTAND, "vl")
        adapter = QwenVLAdapter(api_key=api_key, model=vl_model)

        palette = list(getattr(plan, "palette", None) or [])
        # 底片参考帧在前（块内"前 n_base 张"措辞依赖此序）
        frame_items = (
            [(None, p, "base") for p in base_frame_paths]
            + [(seg, p, tag) for seg, p, tag in frames]
        )
        items: list[_ChunkItem] = []
        for meta, path, tag in frame_items:
            uri = _frame_to_data_uri(path)
            if uri:
                items.append(_ChunkItem(meta=meta, uri=uri, tag=tag))
        if not items:
            logger.warning("[overlay_review] 帧无法转 base64，跳过视觉审查")
            return None

        results: list[ReviewResult] = []
        for chunk in _pack_chunks(items, max_call_payload):
            parsed = await _review_chunk(adapter, chunk, palette)
            if parsed is not None:
                results.append(parsed)
        if not results:
            logger.warning("[overlay_review] 所有分块审查失败，降级跳过")
            return None

        merged = ReviewResult(
            issues=[i for r in results for i in r.issues],
            passed=all(r.passed for r in results),
        )
        merged = _validate_issue_refs(merged, plan)
        logger.info(
            "[overlay_review] 审查完成：%d 块 / %d 帧，%d 个问题（problem 级 %d），passed=%s",
            len(results), len(items),
            len(merged.issues), len(merged.critical_issues), merged.passed,
        )
        return merged

    except Exception as e:
        logger.warning(f"[overlay_review] 视觉审查失败，降级跳过：{e}")
        return None
