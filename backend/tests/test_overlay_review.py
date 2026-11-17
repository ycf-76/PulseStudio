"""P0 视觉自审闭环测试 — VLM 看图审查 + 带反馈重规划（2026-10-27）。

背景：全行业开源项目（ossclip/edit-greek-reel/AutoCut 等）都没有自动审片闭环，
本项目渲染是确定性的（seekTo 时间轴），审出的问题可信、修了必生效 —— 这是
差异化点。契约见 docs/smart-cut-and-overlay.md 10.10。

核心契约：
  - extract_review_frames：ffmpeg 定点抽帧；坏时间戳 clamp、失败帧跳过，绝不抛
  - _parse_review_output：仿 _parse_plan_output 防御式解析；坏 JSON → passed 降级（不阻断）
  - build_replan_feedback：issues → 反馈文本（problem 标"必须修复"）
  - review_overlays：VLM 不可用（无 key / 无 ffmpeg / 抽帧失败 / API 异常）→ None 降级
  - generate_overlay_plan(feedback=...)：外部反馈拼进 prompt
  - _review_loop（skill）：最多 2 轮（初审 + 1 次带反馈重渲复审）；事件反映真实走向
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from capabilities.video.services.overlay_orchestrator import (
    OverlayPlan,
    OverlaySegment,
    OverlayTemplate,
)

# ── ffmpeg 可用性（与 html_to_video 同一解析链）──────────────

try:
    from capabilities.video.adapters.html_to_video import resolve_ffmpeg

    _HAS_FFMPEG = resolve_ffmpeg() is not None
except Exception:
    _HAS_FFMPEG = False

# patch("capabilities.image_gen.adapters.qwen_vl.QwenVLAdapter") 需要父模块
# 先导入子模块属性（mock 只 import 到 adapters 这一层）。
# 必须先完整加载 app.adapters：qwen_vl → app.adapters.llm_base → app.adapters.__init__
# → 回头 import qwen_vl（部分初始化）→ 循环导入炸（生产代码靠函数内延迟导入规避）
import app.adapters  # noqa: F401,E402
import capabilities.image_gen.adapters.qwen_vl  # noqa: F401,E402


def _seg(
    sid: str,
    start: float,
    end: float,
    template: OverlayTemplate = OverlayTemplate.CAPTION_POP,
    params: dict | None = None,
) -> OverlaySegment:
    return OverlaySegment(
        segment_id=sid,
        time_start=start,
        time_end=end,
        template=template,
        params=params or {},
    )


# ── 1. 抽帧 ────────────────────────────────────────────────


@pytest.mark.skipif(not _HAS_FFMPEG, reason="需要 ffmpeg")
class TestExtractReviewFrames:
    async def _make_video(self, tmp_path: Path) -> Path:
        """lavfi 造一个 2s 红色小视频（真编码，模式仿 test_subtitle_single_pass）"""
        ffmpeg = resolve_ffmpeg()
        src = tmp_path / "src.mp4"
        proc = await asyncio.create_subprocess_exec(
            ffmpeg,
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=360x640:d=2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(src),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        assert src.is_file(), f"ffmpeg 造片失败：{stderr.decode(errors='replace')[:200]}"
        return src

    @pytest.mark.asyncio
    async def test_extracts_mid_frame_as_png(self, tmp_path):
        from capabilities.video.services.overlay_review import extract_review_frames

        src = await self._make_video(tmp_path)
        frames = await extract_review_frames(src, [1.0], tmp_path, prefix="rv")
        assert len(frames) == 1
        assert frames[0].is_file()
        assert frames[0].read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"  # PNG magic

    @pytest.mark.asyncio
    async def test_out_of_range_timestamp_skipped(self, tmp_path):
        """越界时间戳抽不出帧 → 跳过，不抛异常"""
        from capabilities.video.services.overlay_review import extract_review_frames

        src = await self._make_video(tmp_path)
        frames = await extract_review_frames(src, [99.0], tmp_path, prefix="rv")
        assert frames == []

    @pytest.mark.asyncio
    async def test_negative_timestamp_clamped_to_zero(self, tmp_path):
        from capabilities.video.services.overlay_review import extract_review_frames

        src = await self._make_video(tmp_path)
        frames = await extract_review_frames(src, [-1.0], tmp_path, prefix="rv")
        assert len(frames) == 1

    @pytest.mark.asyncio
    async def test_missing_video_returns_empty(self, tmp_path):
        from capabilities.video.services.overlay_review import extract_review_frames

        frames = await extract_review_frames(
            tmp_path / "nope.mp4",
            [0.0],
            tmp_path,
            prefix="rv",
        )
        assert frames == []

    @pytest.mark.asyncio
    async def test_partial_failure_keeps_good_frames(self, tmp_path):
        """好时间戳 + 坏时间戳混批：好的保留，坏的跳过"""
        from capabilities.video.services.overlay_review import extract_review_frames

        src = await self._make_video(tmp_path)
        frames = await extract_review_frames(src, [0.5, 99.0, 1.5], tmp_path, prefix="rv")
        assert len(frames) == 2


# ── 2. 选帧计划（纯函数，帧预算控制）────────────────────────


class TestPickFramePlan:
    def test_empty_pairs(self):
        from capabilities.video.services.overlay_review import _pick_frame_plan

        assert _pick_frame_plan([], max_frames=6) == []

    @staticmethod
    def _static_seg(sid: str, start: float, end: float) -> OverlaySegment:
        """非 caption 模板（通用选帧策略测试用 — caption 有专属稳定态策略）"""
        return _seg(sid, start, end, template=OverlayTemplate.TITLE_CARD)

    def test_many_segments_uniformly_sampled(self):
        """段数 > 预算 → 均匀抽样（全片时间轴覆盖），每帧都是中点"""
        from capabilities.video.services.overlay_review import _pick_frame_plan

        pairs = [
            (self._static_seg(f"o{i}", i * 2.0, i * 2.0 + 1.5), Path(f"v{i}.mp4"))
            for i in range(20)
        ]
        plan = _pick_frame_plan(pairs, max_frames=6)
        assert len(plan) <= 6
        assert all(tag == "mid" for _, _, _, tag in plan)

    def test_few_segments_get_in_out_frames(self):
        """段少且长 → 每段中点 + 入/出帧，总量不超预算"""
        from capabilities.video.services.overlay_review import _pick_frame_plan

        pairs = [
            (self._static_seg("o1", 0.0, 3.0), Path("v1.mp4")),
            (self._static_seg("o2", 4.0, 9.0), Path("v2.mp4")),
        ]
        plan = _pick_frame_plan(pairs, max_frames=6)
        assert len(plan) <= 6
        seg_ids = {seg.segment_id for seg, _, _, _ in plan}
        assert seg_ids == {"o1", "o2"}
        tags = [tag for _, _, _, tag in plan]
        assert "mid" in tags and ("in" in tags or "out" in tags)

    def test_short_segments_only_mid(self):
        """短段（≤2s）中点一帧足够，不浪费预算"""
        from capabilities.video.services.overlay_review import _pick_frame_plan

        pairs = [(self._static_seg("o1", 0.0, 1.5), Path("v1.mp4"))]
        plan = _pick_frame_plan(pairs, max_frames=6)
        assert len(plan) == 1
        assert plan[0][3] == "mid"

    def test_every_segment_present_when_within_budget(self):
        from capabilities.video.services.overlay_review import _pick_frame_plan

        pairs = [
            (self._static_seg(f"o{i}", i * 3.0, i * 3.0 + 1.0), Path(f"v{i}.mp4")) for i in range(4)
        ]
        plan = _pick_frame_plan(pairs, max_frames=6)
        assert {seg.segment_id for seg, _, _, _ in plan} == {"o0", "o1", "o2", "o3"}


class TestCaptionSettledFrames:
    """caption 三件套抽稳定态帧（2026-10-27 demo round2 o13/o23 误报实证）。

    caption_pop/karaoke/typewriter 是逐词弹出动画：段中点时刻后半句
    词尚未弹出（opacity=0），VLM 会把"还没弹出"误判为"词缺失/未高亮
    /params 与视觉不一致"。修法：mid 帧抽动画完成态（dur-0.25），
    且不补 in/out 帧（入场早期帧是噪声、out 与 settled 重复）。
    """

    def test_caption_mid帧是稳定态(self):
        from capabilities.video.services.overlay_review import _pick_frame_plan

        # o13 复现：dur=2.2s，中点 1.1s 时"拉肚子"还没弹出
        pairs = [(_seg("o13", 28.82, 31.02), Path("v.mp4"))]
        plan = _pick_frame_plan(pairs, max_frames=6)
        assert len(plan) == 1
        seg, _, t, tag = plan[0]
        assert tag == "settled"
        assert t == pytest.approx(2.2 - 0.25)  # 出场前稳定态

    def test_caption长段不补in_out帧(self):
        from capabilities.video.services.overlay_review import _pick_frame_plan

        pairs = [(_seg("o1", 0.0, 5.0), Path("v.mp4"))]
        plan = _pick_frame_plan(pairs, max_frames=6)
        assert len(plan) == 1  # 只有 settled 一帧
        assert plan[0][3] == "settled"

    def test_极短caption段稳定态不越界(self):
        from capabilities.video.services.overlay_review import _pick_frame_plan

        pairs = [(_seg("o1", 0.0, 0.4), Path("v.mp4"))]
        plan = _pick_frame_plan(pairs, max_frames=6)
        t, tag = plan[0][2], plan[0][3]
        assert tag == "settled"
        assert 0.0 < t <= 0.4

    def test_karaoke_typewriter同策略(self):
        from capabilities.video.services.overlay_review import _pick_frame_plan

        pairs = [
            (_seg("o1", 0.0, 3.0, template=OverlayTemplate.CAPTION_KARAOKE), Path("v1.mp4")),
            (_seg("o2", 4.0, 7.0, template=OverlayTemplate.CAPTION_TYPEWRITER), Path("v2.mp4")),
        ]
        plan = _pick_frame_plan(pairs, max_frames=6)
        assert [tag for _, _, _, tag in plan] == ["settled", "settled"]

    def test_非caption模板不受影响(self):
        from capabilities.video.services.overlay_review import _pick_frame_plan

        pairs = [(_seg("o1", 0.0, 3.0, template=OverlayTemplate.SECTION_CARD), Path("v.mp4"))]
        plan = _pick_frame_plan(pairs, max_frames=6)
        mid = [p for p in plan if p[3] == "mid"]
        assert mid and mid[0][2] == pytest.approx(1.5)


# ── 3. 审查输出解析加固（仿 _parse_plan_output）───────────────


class TestParseReviewOutput:
    def test_valid_json(self):
        from capabilities.video.services.overlay_review import _parse_review_output

        text = (
            '{"issues": [{"segment_id": "o3", "severity": "problem", '
            '"problem": "文字溢出右边界", "suggestion": "缩短文案"}], "pass": false}'
        )
        r = _parse_review_output(text)
        assert r.passed is False
        assert len(r.issues) == 1
        assert r.issues[0].segment_id == "o3"
        assert r.issues[0].severity == "problem"
        assert r.critical_issues == r.issues

    def test_pass_true_no_issues(self):
        from capabilities.video.services.overlay_review import _parse_review_output

        r = _parse_review_output('{"issues": [], "pass": true}')
        assert r.passed is True
        assert r.critical_issues == []

    def test_json_with_surrounding_text(self):
        """VLM 前后废话包裹 JSON → 仍能解析"""
        from capabilities.video.services.overlay_review import _parse_review_output

        text = '好的，审查结果如下：{"issues": [{"segment_id": "o1", "severity": "problem", "problem": "塌缩"}], "pass": false} 以上。'
        r = _parse_review_output(text)
        assert r.passed is False
        assert len(r.issues) == 1

    def test_bad_json_degrades_to_pass(self):
        """坏 JSON → passed=True 降级（审查失败绝不阻断出片）"""
        from capabilities.video.services.overlay_review import _parse_review_output

        r = _parse_review_output('{"issues": [{"segment_id": broken')
        assert r.passed is True
        assert r.issues == []

    def test_no_json_degrades_to_pass(self):
        from capabilities.video.services.overlay_review import _parse_review_output

        assert _parse_review_output("这里没有任何 JSON").passed is True

    def test_issue_field_hardening(self):
        """非 dict 项跳过 / 非法 severity 归一为 suggestion / 空内容 issue 跳过"""
        from capabilities.video.services.overlay_review import _parse_review_output

        text = (
            '{"issues": ["不是dict", '
            '{"segment_id": "o1", "severity": "致命", "problem": "x"}, '
            '{"segment_id": "o2", "severity": "problem", "problem": "", "suggestion": ""}, '
            '{"segment_id": "o3", "severity": "problem", "problem": "真问题"}], "pass": false}'
        )
        r = _parse_review_output(text)
        assert len(r.issues) == 2
        assert r.issues[0].segment_id == "o1"
        assert r.issues[0].severity == "suggestion"  # 非法值归一
        assert r.issues[1].problem == "真问题"

    def test_pass_inferred_when_missing(self):
        """pass 字段缺失 → 由是否存在 problem 级 issue 推断"""
        from capabilities.video.services.overlay_review import _parse_review_output

        r = _parse_review_output(
            '{"issues": [{"segment_id": "o1", "severity": "problem", "problem": "x"}]}'
        )
        assert r.passed is False
        r2 = _parse_review_output(
            '{"issues": [{"segment_id": "o1", "severity": "suggestion", "suggestion": "y"}]}'
        )
        assert r2.passed is True


# ── 3.2 issue 引用校验（程序化防幻觉 — 渲染确定性优势的延伸）──


class TestValidateIssueRefs:
    """VLM 报问题引用的文字必须真实存在于该段 params。

    2026-10-27 第六次 demo round1 三条 problem 全是段号错位/幻觉实证：
    - 报 o14"出门做好防晒断行"，o14 params 是"不只是怕晒黑"（那是 o13 的）
    - 报 o18"帽子防晒衣防晒霜色块混色"，o18 是 title_card"夏天舒服过"
    - 报 o22"对比度3.2:1"，实算 9.76:1 达标
    三条误导了重规划（白改 o10-o17）。硬防线：issue 文本中引号引用的
    文字若在该段 params 里找不到 → 幻觉/错位，整条丢弃；引用全灭后
    critical 归零 → passed 翻 true，避免无效重规划。
    无引用的 issue（描述性问题如"元素溢出边界"）不受影响。
    """

    def _plan_with(self, **segs_params) -> OverlayPlan:
        from capabilities.video.services.overlay_review import ReviewIssue, ReviewResult

        segments = [
            OverlaySegment(
                segment_id=sid,
                time_start=float(i),
                time_end=float(i) + 2.0,
                template=OverlayTemplate.CAPTION_POP,
                params=p,
            )
            for i, (sid, p) in enumerate(segs_params.items())
        ]
        return OverlayPlan(video_id="v", segments=segments)

    def test_第六次demo_o18实录复现_引用文字不在段内_丢弃(self):
        from capabilities.video.services.overlay_review import (
            ReviewIssue,
            ReviewResult,
            _validate_issue_refs,
        )

        plan = self._plan_with(
            **{
                "o17": {"keyword": "帽子 防晒衣 防晒霜"},
                "o18": {"title": "夏天舒服过 健康第一位", "color": "#FFE066"},
            }
        )
        result = ReviewResult(
            passed=False,
            issues=[
                ReviewIssue(
                    segment_id="o18",
                    severity="problem",
                    problem="第4帧中'帽子''防晒衣''防晒霜'三个高亮词色块颜色混用",
                    suggestion="统一按params.color渲染",
                ),
            ],
        )
        kept = _validate_issue_refs(result, plan)
        assert kept.issues == []
        assert kept.passed is True  # critical 全灭 → 不再触发重规划

    def test_引用命中段params_保留(self):
        from capabilities.video.services.overlay_review import (
            ReviewIssue,
            ReviewResult,
            _validate_issue_refs,
        )

        plan = self._plan_with(
            **{
                "o22": {"text": "夏天别大意", "sub": "健康第一位", "color": "#4ECDC4"},
            }
        )
        result = ReviewResult(
            passed=False,
            issues=[
                ReviewIssue(
                    segment_id="o22",
                    severity="suggestion",
                    problem="主标题与副标'健康第一位'对比度低",
                    suggestion="改白色文字",
                ),
            ],
        )
        kept = _validate_issue_refs(result, plan)
        assert len(kept.issues) == 1

    def test_无引用的描述性issue_保留(self):
        from capabilities.video.services.overlay_review import (
            ReviewIssue,
            ReviewResult,
            _validate_issue_refs,
        )

        plan = self._plan_with(**{"o3": {"keyword": "正式入夏"}})
        result = ReviewResult(
            passed=False,
            issues=[
                ReviewIssue(
                    segment_id="o3",
                    severity="problem",
                    problem="元素溢出画面右边界",
                    suggestion="缩小字号",
                ),
            ],
        )
        kept = _validate_issue_refs(result, plan)
        assert len(kept.issues) == 1
        assert kept.passed is False

    def test_混合引用_任一命中即保留(self):
        from capabilities.video.services.overlay_review import (
            ReviewIssue,
            ReviewResult,
            _validate_issue_refs,
        )

        plan = self._plan_with(**{"o21": {"text": "效果空间特别多"}})
        result = ReviewResult(
            passed=False,
            issues=[
                ReviewIssue(
                    segment_id="o21",
                    severity="problem",
                    problem="'效果空间特别多'末字被截断",
                    suggestion="加宽卡片",
                ),
            ],
        )
        kept = _validate_issue_refs(result, plan)
        assert len(kept.issues) == 1

    def test_全片issue无段号_保留(self):
        from capabilities.video.services.overlay_review import (
            ReviewIssue,
            ReviewResult,
            _validate_issue_refs,
        )

        plan = self._plan_with(**{"o1": {"text": "x"}})
        result = ReviewResult(
            passed=False,
            issues=[
                ReviewIssue(
                    segment_id="",
                    severity="suggestion",
                    problem="全片配色偏暗",
                    suggestion="提高明度",
                ),
            ],
        )
        kept = _validate_issue_refs(result, plan)
        assert len(kept.issues) == 1

    def test_引用的段不存在_丢弃(self):
        """VLM 编造段号（o99 不在 plan 里）→ 无法取证 → 丢弃"""
        from capabilities.video.services.overlay_review import (
            ReviewIssue,
            ReviewResult,
            _validate_issue_refs,
        )

        plan = self._plan_with(**{"o1": {"text": "x"}})
        result = ReviewResult(
            passed=False,
            issues=[
                ReviewIssue(
                    segment_id="o99",
                    severity="problem",
                    problem="'某些文字'塌缩",
                    suggestion="修布局",
                ),
            ],
        )
        kept = _validate_issue_refs(result, plan)
        assert kept.issues == []
        assert kept.passed is True

    def test_弯引号引用也能提取_2026run9实录(self):
        """2026-10-27 run9 实证：VLM 用弯引号 ' '（U+2018/2019）引用，
        旧正则漏配 → 引用提取为空 → o18 幻觉（内容实为 o19 的）被当
        '无引用描述'放行。修后弯引号必须同样触发段号校验。"""
        from capabilities.video.services.overlay_review import _quote_refs

        refs = _quote_refs("第4帧中\u2018防晒衣\u2019与\u2018防晒霜\u2019之间缺少空格")
        assert "防晒衣" in refs and "防晒霜" in refs

    def test_弯引号幻觉段号_丢弃_2026run9实录(self):
        from capabilities.video.services.overlay_review import (
            ReviewIssue,
            ReviewResult,
            _validate_issue_refs,
        )

        plan = self._plan_with(
            **{
                "o19": {"text": "帽子、防晒衣、防晒霜，都安排上"},
            }
        )
        result = ReviewResult(
            passed=False,
            issues=[
                ReviewIssue(
                    segment_id="o18",
                    severity="problem",
                    problem="第4帧中\u2018防晒衣\u2019与\u2018防晒霜\u2019之间缺少"
                    "空格或分隔符，导致\u2018防晒衣防晒霜\u2019视觉上连读",
                    suggestion="加空格分隔",
                ),
            ],
        )
        kept = _validate_issue_refs(result, plan)
        assert kept.issues == []
        assert kept.passed is True


# ── 3.5 帧压缩（大请求体会被网络中间层掐断，必须 JPEG 化）─────


class TestFrameToDataUri:
    def _png(self, tmp_path, size=(684, 1174)):
        from PIL import Image

        p = tmp_path / "f.png"
        Image.new("RGB", size, (200, 60, 60)).save(p, "PNG")
        return p

    def test_png_becomes_jpeg_data_uri(self, tmp_path):
        from capabilities.video.services.overlay_review import _frame_to_data_uri

        uri = _frame_to_data_uri(self._png(tmp_path))
        assert uri is not None
        assert uri.startswith("data:image/jpeg;base64,")

    def test_downscales_to_max_side(self, tmp_path):
        from PIL import Image

        from capabilities.video.services.overlay_review import MAX_FRAME_SIDE, _frame_to_data_uri

        big = self._png(tmp_path, size=(2160, 3840))
        uri = _frame_to_data_uri(big)
        assert uri is not None
        import base64
        import io

        im = Image.open(io.BytesIO(base64.b64decode(uri.split(",", 1)[1])))
        assert max(im.size) == MAX_FRAME_SIDE

    def test_corrupt_file_falls_back_to_raw_base64(self, tmp_path):
        """非图片文件 → PIL 转换失败 → 退回 url_to_base64 原始读取"""
        from capabilities.video.services import overlay_review

        p = tmp_path / "f.png"
        p.write_bytes(b"not an image")
        with patch(
            "app.services.asset_library.url_to_base64", return_value="data:image/png;base64,aGk="
        ):
            uri = overlay_review._frame_to_data_uri(p)
        assert uri == "data:image/png;base64,aGk="

    def test_missing_file_returns_none(self, tmp_path):
        from capabilities.video.services.overlay_review import _frame_to_data_uri

        assert _frame_to_data_uri(tmp_path / "nope.png") is None


# ── 3.6 请求体分块（网络中间层 ~145KB 掐大 POST，实测 2026-10-27）──


class TestPackChunks:
    def _item(self, i, uri_kb):
        from capabilities.video.services.overlay_review import _ChunkItem

        return _ChunkItem(meta=f"m{i}", uri="u" * (uri_kb * 1024))

    def test_empty(self):
        from capabilities.video.services.overlay_review import _pack_chunks

        assert _pack_chunks([], budget=100_000) == []

    def test_single_fits_one_chunk(self):
        from capabilities.video.services.overlay_review import _pack_chunks

        items = [self._item(0, 40), self._item(1, 40)]
        chunks = _pack_chunks(items, budget=100_000)
        assert len(chunks) == 1  # 2×(40KB+2KB 开销) = 84KB < 100KB
        assert len(chunks[0]) == 2

    def test_exceeding_budget_splits(self):
        from capabilities.video.services.overlay_review import _pack_chunks

        items = [self._item(0, 40), self._item(1, 40)]
        chunks = _pack_chunks(items, budget=80_000)
        assert len(chunks) == 2

    def test_small_items_packed(self):
        from capabilities.video.services.overlay_review import _pack_chunks

        items = [self._item(i, 5) for i in range(8)]
        chunks = _pack_chunks(items, budget=100_000)
        assert len(chunks) == 1
        assert len(chunks[0]) == 8

    def test_order_preserved_across_chunks(self):
        from capabilities.video.services.overlay_review import _pack_chunks

        items = [self._item(i, 40) for i in range(4)]
        chunks = _pack_chunks(items, budget=100_000)
        metas = [item.meta for c in chunks for item in c]
        assert metas == ["m0", "m1", "m2", "m3"]

    def test_oversized_single_gets_own_chunk(self):
        """单帧超预算 → 独占一块（不丢帧，由调用方自行发大请求或失败跳过）"""
        from capabilities.video.services.overlay_review import _pack_chunks

        items = [self._item(0, 200), self._item(1, 5)]
        chunks = _pack_chunks(items, budget=100_000)
        assert len(chunks) == 2
        assert len(chunks[0]) == 1


# ── 4. 反馈文本构建 ─────────────────────────────────────────


class TestBuildReplanFeedback:
    def test_problems_marked_and_located(self):
        from capabilities.video.services.overlay_review import (
            ReviewIssue,
            ReviewResult,
            build_replan_feedback,
        )

        result = ReviewResult(
            passed=False,
            issues=[
                ReviewIssue(
                    segment_id="o3",
                    severity="problem",
                    problem="文字溢出右边界",
                    suggestion="缩短文案或减小字号",
                ),
                ReviewIssue(
                    segment_id="", severity="suggestion", problem="", suggestion="配色可以更统一"
                ),
            ],
        )
        feedback = build_replan_feedback(result)
        assert "o3" in feedback
        assert "文字溢出右边界" in feedback
        assert "缩短文案或减小字号" in feedback
        assert "必须修复" in feedback
        assert "整体" in feedback  # 无 segment_id 的 issue 标整体

    def test_empty_issues_returns_empty_string(self):
        from capabilities.video.services.overlay_review import ReviewResult, build_replan_feedback

        assert build_replan_feedback(ReviewResult(passed=True)) == ""


# ── 5. review_overlays 降级路径（VLM 不可用绝不阻断）─────────


class TestReviewOverlaysDegradation:
    def _pairs(self):
        return [
            (
                _seg("o1", 0.0, 2.0, OverlayTemplate.TITLE_CARD, {"title": "标题"}),
                Path("fake1.mp4"),
            ),
            (
                _seg("o2", 3.0, 5.0, OverlayTemplate.CAPTION_POP, {"text": "字幕"}),
                Path("fake2.mp4"),
            ),
        ]

    def _plan(self):
        return OverlayPlan(video_id="", palette=["#FF4D4D", "#FFE066", "#4ECDC4"])

    @pytest.mark.asyncio
    async def test_no_api_key_returns_none(self, tmp_path, monkeypatch):
        from app.config import get_settings
        from capabilities.video.services.overlay_review import review_overlays

        monkeypatch.setattr(get_settings(), "dashscope_api_key", "")
        assert await review_overlays(self._plan(), self._pairs(), tmp_path) is None

    @pytest.mark.asyncio
    async def test_no_ffmpeg_returns_none(self, tmp_path, monkeypatch):
        from app.config import get_settings
        from capabilities.video.services.overlay_review import review_overlays

        monkeypatch.setattr(get_settings(), "dashscope_api_key", "sk-test")
        with patch("capabilities.video.adapters.html_to_video.resolve_ffmpeg", return_value=None):
            assert await review_overlays(self._plan(), self._pairs(), tmp_path) is None

    @pytest.mark.asyncio
    async def test_frame_extraction_failure_returns_none(self, tmp_path, monkeypatch):
        """抽帧全失败（视频路径是假的）→ None，不碰 VLM"""
        from app.config import get_settings
        from capabilities.video.services.overlay_review import review_overlays

        monkeypatch.setattr(get_settings(), "dashscope_api_key", "sk-test")
        with patch("capabilities.image_gen.adapters.qwen_vl.QwenVLAdapter") as mock_cls:
            result = await review_overlays(self._plan(), self._pairs(), tmp_path)
        assert result is None
        mock_cls.assert_not_called()  # 没抽到帧就不该烧 VLM 调用

    @pytest.mark.asyncio
    async def test_vlm_adapter_crash_returns_none(self, tmp_path, monkeypatch):
        from app.config import get_settings
        from capabilities.video.services import overlay_review

        monkeypatch.setattr(get_settings(), "dashscope_api_key", "sk-test")
        fake_frames = [tmp_path / "f1.png", tmp_path / "f2.png"]
        for f in fake_frames:
            f.write_bytes(b"\x89PNG\r\n\x1a\nfake")
        mock_adapter = MagicMock()
        mock_adapter.chat = AsyncMock(side_effect=RuntimeError("VLM down"))
        with (
            patch.object(
                overlay_review, "extract_review_frames", AsyncMock(return_value=fake_frames)
            ),
            patch(
                "capabilities.image_gen.adapters.qwen_vl.QwenVLAdapter", return_value=mock_adapter
            ),
            patch(
                "app.services.asset_library.url_to_base64",
                return_value="data:image/png;base64,aGk=",
            ),
        ):
            result = await overlay_review.review_overlays(
                self._plan(),
                self._pairs(),
                tmp_path,
            )
        assert result is None

    @pytest.mark.asyncio
    async def test_happy_path_returns_review_result(self, tmp_path, monkeypatch):
        """全链路 mock：抽帧 ok → VLM 返回问题 JSON → 解析出 ReviewResult"""
        from app.config import get_settings
        from capabilities.video.services import overlay_review

        monkeypatch.setattr(get_settings(), "dashscope_api_key", "sk-test")
        fake_frames = [tmp_path / "f1.png"]
        fake_frames[0].write_bytes(b"\x89PNG\r\n\x1a\nfake")
        vlm_json = (
            '{"issues": [{"segment_id": "o1", "severity": "problem", '
            '"problem": "标题塌缩", "suggestion": "减小字号"}], "pass": false}'
        )
        mock_adapter = MagicMock()
        mock_adapter.chat = AsyncMock(return_value={"content": vlm_json})
        captured_prompt = {}

        async def fake_chat(messages, **kwargs):
            captured_prompt["text"] = messages[0]["content"][0]["text"]
            captured_prompt["images"] = sum(1 for c in messages[0]["content"] if "image" in c)
            return {"content": vlm_json}

        mock_adapter.chat = fake_chat
        with (
            patch.object(
                overlay_review, "extract_review_frames", AsyncMock(return_value=fake_frames)
            ),
            patch(
                "capabilities.image_gen.adapters.qwen_vl.QwenVLAdapter", return_value=mock_adapter
            ),
            patch(
                "app.services.asset_library.url_to_base64",
                return_value="data:image/png;base64,aGk=",
            ),
        ):
            result = await overlay_review.review_overlays(
                self._plan(),
                self._pairs(),
                tmp_path,
            )
        assert result is not None
        assert result.passed is False
        assert result.issues[0].problem == "标题塌缩"
        # prompt 必须含帧清单与色盘（VLM 有依据地审，不瞎猜）
        assert "o1" in captured_prompt["text"]
        assert "title_card" in captured_prompt["text"]
        assert "#FF4D4D" in captured_prompt["text"]

    @pytest.mark.asyncio
    async def test_multi_chunk_merges_issues(self, tmp_path, monkeypatch):
        """预算极小 → 帧分块多次调用；issues 合并，passed 取合取"""
        from app.config import get_settings
        from capabilities.video.services import overlay_review

        monkeypatch.setattr(get_settings(), "dashscope_api_key", "sk-test")
        fake_frames = [tmp_path / f"f{i}.png" for i in range(3)]
        for f in fake_frames:
            f.write_bytes(b"\x89PNG\r\n\x1a\nfake")
        pairs = [(_seg(f"o{i}", i * 2.0, i * 2.0 + 1.5), Path(f"v{i}.mp4")) for i in range(3)]
        calls = []

        async def fake_chat(messages, **kwargs):
            calls.append(messages)
            idx = len(calls) - 1
            if idx == 0:
                return {
                    "content": '{"issues": [{"segment_id": "oA", "severity": "problem", "problem": "塌缩"}], "pass": false}'
                }
            return {"content": '{"issues": [], "pass": true}'}

        mock_adapter = MagicMock()
        mock_adapter.chat = fake_chat
        with (
            patch.object(
                overlay_review, "extract_review_frames", AsyncMock(return_value=fake_frames)
            ),
            patch(
                "capabilities.image_gen.adapters.qwen_vl.QwenVLAdapter", return_value=mock_adapter
            ),
            patch(
                "app.services.asset_library.url_to_base64",
                return_value="data:image/png;base64,aGk=",
            ),
        ):
            result = await overlay_review.review_overlays(
                self._plan(),
                pairs,
                tmp_path,
                max_call_payload=100,  # 强制每帧一块
            )
        assert result is not None
        assert len(calls) == 3
        assert result.passed is False  # 任一未过即整体未过
        assert [i.segment_id for i in result.issues] == ["oA"]

    @pytest.mark.asyncio
    async def test_chunk_failure_skips_chunk(self, tmp_path, monkeypatch):
        """某块网络失败 → 跳过该块，其余块结论有效（不整体降级）"""
        from app.config import get_settings
        from capabilities.video.services import overlay_review

        monkeypatch.setattr(get_settings(), "dashscope_api_key", "sk-test")
        fake_frames = [tmp_path / f"f{i}.png" for i in range(2)]
        for f in fake_frames:
            f.write_bytes(b"\x89PNG\r\n\x1a\nfake")
        calls = []

        async def fake_chat(messages, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("SSL EOF")
            return {"content": '{"issues": [], "pass": true}'}

        mock_adapter = MagicMock()
        mock_adapter.chat = fake_chat
        with (
            patch.object(
                overlay_review, "extract_review_frames", AsyncMock(return_value=fake_frames)
            ),
            patch(
                "capabilities.image_gen.adapters.qwen_vl.QwenVLAdapter", return_value=mock_adapter
            ),
            patch(
                "app.services.asset_library.url_to_base64",
                return_value="data:image/png;base64,aGk=",
            ),
        ):
            result = await overlay_review.review_overlays(
                self._plan(),
                self._pairs(),
                tmp_path,
                max_call_payload=100,
            )
        assert result is not None
        assert len(calls) == 2
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_all_chunks_fail_returns_none(self, tmp_path, monkeypatch):
        from app.config import get_settings
        from capabilities.video.services import overlay_review

        monkeypatch.setattr(get_settings(), "dashscope_api_key", "sk-test")
        fake_frames = [tmp_path / "f1.png"]
        fake_frames[0].write_bytes(b"\x89PNG\r\n\x1a\nfake")
        mock_adapter = MagicMock()
        mock_adapter.chat = AsyncMock(side_effect=RuntimeError("SSL EOF"))
        with (
            patch.object(
                overlay_review, "extract_review_frames", AsyncMock(return_value=fake_frames)
            ),
            patch(
                "capabilities.image_gen.adapters.qwen_vl.QwenVLAdapter", return_value=mock_adapter
            ),
            patch(
                "app.services.asset_library.url_to_base64",
                return_value="data:image/png;base64,aGk=",
            ),
        ):
            result = await overlay_review.review_overlays(
                self._plan(),
                self._pairs(),
                tmp_path,
                max_call_payload=100,
            )
        assert result is None

    @pytest.mark.asyncio
    async def test_content_list_flattened(self, tmp_path, monkeypatch):
        """DashScope 多图输入返回 content 为 list[{"text":...}] → 拉平解析（踩过的坑）"""
        from app.config import get_settings
        from capabilities.video.services import overlay_review

        monkeypatch.setattr(get_settings(), "dashscope_api_key", "sk-test")
        fake_frames = [tmp_path / "f1.png"]
        fake_frames[0].write_bytes(b"\x89PNG\r\n\x1a\nfake")
        mock_adapter = MagicMock()
        mock_adapter.chat = AsyncMock(
            return_value={
                "content": [{"text": '{"issues": [], "pass": true}'}],
            }
        )
        with (
            patch.object(
                overlay_review, "extract_review_frames", AsyncMock(return_value=fake_frames)
            ),
            patch(
                "capabilities.image_gen.adapters.qwen_vl.QwenVLAdapter", return_value=mock_adapter
            ),
            patch(
                "app.services.asset_library.url_to_base64",
                return_value="data:image/png;base64,aGk=",
            ),
        ):
            result = await overlay_review.review_overlays(
                self._plan(),
                self._pairs(),
                tmp_path,
            )
        assert result is not None
        assert result.passed is True


# ── 6. generate_overlay_plan 外部反馈参数 ───────────────────


class TestGenerateOverlayPlanFeedback:
    @pytest.mark.asyncio
    async def test_feedback_prepended_into_prompt(self):
        from capabilities.video.services.overlay_orchestrator import generate_overlay_plan

        segments = [{"index": 0, "start": 0.0, "end": 3.0, "text": "增长秘诀"}]
        captured = {}

        async def fake_llm_prompt_to_text(llm, prompt, max_tokens=None):
            captured["prompt"] = prompt
            return (
                '{"palette": ["#FF4D4D", "#FFE066", "#4ECDC4"], "overlays": ['
                '{"segment_id": "o1", "time_start": 0.0, "time_end": 10.0, '
                '"template": "title_card", "params": {"title": "增长秘诀", "color": "#FFE066"}}]}'
            )

        with patch("app.adapters.llm_base.llm_prompt_to_text", new=fake_llm_prompt_to_text):
            plan, _ = await generate_overlay_plan(
                segments,
                duration=10.0,
                llm=MagicMock(),
                feedback="视觉审查发现 o3 箭头指向空白处，请修复",
            )
        assert "o3 箭头指向空白处" in captured["prompt"]
        assert "增长秘诀" in captured["prompt"]  # 原始 prompt 仍在
        assert len(plan.segments) == 1

    @pytest.mark.asyncio
    async def test_no_feedback_unchanged(self):
        from capabilities.video.services.overlay_orchestrator import generate_overlay_plan

        segments = [{"index": 0, "start": 0.0, "end": 3.0, "text": "增长秘诀"}]
        captured = {}

        async def fake_llm_prompt_to_text(llm, prompt, max_tokens=None):
            captured["prompt"] = prompt
            return (
                '[{"segment_id": "o1", "time_start": 0.0, "time_end": 10.0, '
                '"template": "title_card", "params": {"title": "x"}}]'
            )

        with patch("app.adapters.llm_base.llm_prompt_to_text", new=fake_llm_prompt_to_text):
            await generate_overlay_plan(segments, duration=10.0, llm=MagicMock())
        assert "上次" not in captured["prompt"]


# ── 7. skill 闭环轮次控制（_review_loop）────────────────────


class TestSkillReviewLoop:
    def _make_skill(self):
        from capabilities.video.skills.talking_head_overlay import TalkingHeadOverlaySkill

        return TalkingHeadOverlaySkill()

    def _make_parsed(self):
        from capabilities.video.skills.talking_head_overlay import OverlayInput

        return OverlayInput(video_path="/tmp/test.mp4")

    def _make_state(self, plan=None, pairs=None):
        return {
            "plan": plan or OverlayPlan(video_id="", segments=[_seg("o1", 0.0, 2.0)]),
            "preview_pairs": pairs or [(_seg("o1", 0.0, 2.0), Path("v1.mp4"))],
            "review": None,
        }

    def _result(self, passed: bool, n_critical: int = 0):
        from capabilities.video.services.overlay_review import ReviewIssue, ReviewResult

        issues = [
            ReviewIssue(segment_id=f"o{i}", severity="problem", problem=f"问题{i}", suggestion="修")
            for i in range(n_critical)
        ]
        return ReviewResult(passed=passed, issues=issues)

    @pytest.mark.asyncio
    async def test_review_none_degrades_silently(self):
        """VLM 不可用（None）→ 0 事件、状态不动，直接进合成"""
        skill = self._make_skill()
        state = self._make_state()
        with patch(
            "capabilities.video.services.overlay_review.review_overlays",
            new=AsyncMock(return_value=None),
        ):
            events = [
                e
                async for e in skill._review_loop(
                    state,
                    [],
                    [],
                    self._make_parsed(),
                    10.0,
                    Path("tmp"),
                    1080,
                    1920,
                )
            ]
        assert events == []
        assert state["review"] is None

    @pytest.mark.asyncio
    async def test_pass_on_first_round_single_event(self):
        skill = self._make_skill()
        state = self._make_state()
        with patch(
            "capabilities.video.services.overlay_review.review_overlays",
            new=AsyncMock(return_value=self._result(passed=True)),
        ):
            events = [
                e
                async for e in skill._review_loop(
                    state,
                    [],
                    [],
                    self._make_parsed(),
                    10.0,
                    Path("tmp"),
                    1080,
                    1920,
                )
            ]
        assert len(events) == 1
        assert events[0]["event"] == "overlay_review_done"
        assert events[0]["data"]["passed"] is True
        assert events[0]["data"]["will_retry"] is False
        assert events[0]["data"]["round"] == 1

    @pytest.mark.asyncio
    async def test_critical_retries_once_then_passes(self):
        """round1 发现 problem → 带反馈重规划重渲 → round2 通过 → 共 2 事件"""
        skill = self._make_skill()
        state = self._make_state()
        new_plan = OverlayPlan(video_id="", segments=[_seg("o9", 0.0, 1.5)])
        new_pairs = [(_seg("o9", 0.0, 1.5), Path("v9.mp4"))]
        review_results = [self._result(passed=False, n_critical=1), self._result(passed=True)]
        replan = AsyncMock(return_value=new_plan)
        rerender = AsyncMock(return_value=new_pairs)
        with (
            patch(
                "capabilities.video.services.overlay_review.review_overlays",
                new=AsyncMock(side_effect=review_results),
            ),
            patch.object(skill, "_replan_with_feedback", replan),
            patch.object(skill, "_render_previews", rerender),
        ):
            events = [
                e
                async for e in skill._review_loop(
                    state,
                    [{"index": 0}],
                    [],
                    self._make_parsed(),
                    10.0,
                    Path("tmp"),
                    1080,
                    1920,
                )
            ]
        assert len(events) == 2
        assert events[0]["data"]["will_retry"] is True
        assert events[1]["data"]["passed"] is True
        assert events[1]["data"]["round"] == 2
        # 反馈文本来自 review 结果
        feedback_arg = (
            replan.call_args.args[-1]
            if replan.call_args.args
            else replan.call_args.kwargs.get("feedback", "")
        )
        assert "问题0" in str(feedback_arg)
        # 状态已切换到新规划
        assert state["plan"] is new_plan
        assert state["preview_pairs"] == new_pairs

    @pytest.mark.asyncio
    async def test_max_two_rounds_no_infinite_loop(self):
        """两轮都有 problem → 到上限出片，绝不无限循环"""
        skill = self._make_skill()
        state = self._make_state()
        with (
            patch(
                "capabilities.video.services.overlay_review.review_overlays",
                new=AsyncMock(return_value=self._result(passed=False, n_critical=2)),
            ),
            patch.object(
                skill,
                "_replan_with_feedback",
                AsyncMock(return_value=OverlayPlan(video_id="", segments=[_seg("o9", 0.0, 1.5)])),
            ),
            patch.object(
                skill,
                "_render_previews",
                AsyncMock(return_value=[(_seg("o9", 0.0, 1.5), Path("v9.mp4"))]),
            ),
        ):
            events = [
                e
                async for e in skill._review_loop(
                    state,
                    [{"index": 0}],
                    [],
                    self._make_parsed(),
                    10.0,
                    Path("tmp"),
                    1080,
                    1920,
                )
            ]
        assert len(events) == 2
        assert all(e["data"]["critical_count"] == 2 for e in events)
        assert events[-1]["data"]["will_retry"] is False

    @pytest.mark.asyncio
    async def test_replan_failure_keeps_current_render(self):
        """重规划失败 → 按当前渲染出片，事件如实反映 will_retry=False"""
        skill = self._make_skill()
        state = self._make_state()
        with (
            patch(
                "capabilities.video.services.overlay_review.review_overlays",
                new=AsyncMock(return_value=self._result(passed=False, n_critical=1)),
            ),
            patch.object(skill, "_replan_with_feedback", AsyncMock(return_value=None)),
        ):
            events = [
                e
                async for e in skill._review_loop(
                    state,
                    [],
                    [],
                    self._make_parsed(),
                    10.0,
                    Path("tmp"),
                    1080,
                    1920,
                )
            ]
        assert len(events) == 1
        assert events[0]["data"]["will_retry"] is False

    @pytest.mark.asyncio
    async def test_empty_pairs_no_review(self):
        """没有可审的预览 → 不进循环（不烧 VLM）"""
        skill = self._make_skill()
        state = {"plan": OverlayPlan(video_id=""), "preview_pairs": [], "review": None}
        with patch(
            "capabilities.video.services.overlay_review.review_overlays", new=AsyncMock()
        ) as mock_review:
            events = [
                e
                async for e in skill._review_loop(
                    state,
                    [],
                    [],
                    self._make_parsed(),
                    10.0,
                    Path("tmp"),
                    1080,
                    1920,
                )
            ]
        assert events == []
        mock_review.assert_not_called()

    # ── P0 收尾：审查明细落盘（issue 文本进事件 → report.reviews）──

    @staticmethod
    def _detailed_result():
        from capabilities.video.services.overlay_review import ReviewIssue, ReviewResult

        return ReviewResult(
            passed=False,
            issues=[
                ReviewIssue(
                    segment_id="o6",
                    severity="problem",
                    problem="段落卡与标记笔同屏叠字",
                    suggestion="跳过重叠段",
                ),
                ReviewIssue(
                    segment_id="",
                    severity="suggestion",
                    problem="全片配色偏暗",
                    suggestion="提高明度",
                ),
            ],
        )

    @pytest.mark.asyncio
    async def test_重试路径事件带明细(self):
        skill = self._make_skill()
        state = self._make_state()
        with (
            patch(
                "capabilities.video.services.overlay_review.review_overlays",
                new=AsyncMock(return_value=self._detailed_result()),
            ),
            patch.object(
                skill,
                "_replan_with_feedback",
                AsyncMock(return_value=OverlayPlan(video_id="", segments=[_seg("o9", 0.0, 1.5)])),
            ),
            patch.object(
                skill,
                "_render_previews",
                AsyncMock(return_value=[(_seg("o9", 0.0, 1.5), Path("v9.mp4"))]),
            ),
        ):
            events = [
                e
                async for e in skill._review_loop(
                    state,
                    [{"index": 0}],
                    [],
                    self._make_parsed(),
                    10.0,
                    Path("tmp"),
                    1080,
                    1920,
                )
            ]
        issues = events[0]["data"]["issues"]
        assert len(issues) == 2
        assert issues[0]["segment_id"] == "o6"
        assert issues[0]["severity"] == "problem"
        assert issues[0]["problem"] == "段落卡与标记笔同屏叠字"
        assert issues[0]["suggestion"] == "跳过重叠段"
        assert issues[1]["segment_id"] == ""  # 全片性问题允许空段
        assert issues[1]["severity"] == "suggestion"

    @pytest.mark.asyncio
    async def test_达上限路径事件带明细(self):
        skill = self._make_skill()
        state = self._make_state()
        with (
            patch(
                "capabilities.video.services.overlay_review.review_overlays",
                new=AsyncMock(return_value=self._detailed_result()),
            ),
            patch.object(
                skill,
                "_replan_with_feedback",
                AsyncMock(return_value=OverlayPlan(video_id="", segments=[_seg("o9", 0.0, 1.5)])),
            ),
            patch.object(
                skill,
                "_render_previews",
                AsyncMock(return_value=[(_seg("o9", 0.0, 1.5), Path("v9.mp4"))]),
            ),
        ):
            events = [
                e
                async for e in skill._review_loop(
                    state,
                    [{"index": 0}],
                    [],
                    self._make_parsed(),
                    10.0,
                    Path("tmp"),
                    1080,
                    1920,
                )
            ]
        # 两轮事件都必须带明细（对比两轮 issue 才有审计价值）
        for e in events:
            assert len(e["data"]["issues"]) == 2
            assert {"segment_id", "severity", "problem", "suggestion"} == set(
                e["data"]["issues"][0].keys()
            )

    @pytest.mark.asyncio
    async def test_重规划失败路径事件带明细(self):
        skill = self._make_skill()
        state = self._make_state()
        with (
            patch(
                "capabilities.video.services.overlay_review.review_overlays",
                new=AsyncMock(return_value=self._detailed_result()),
            ),
            patch.object(skill, "_replan_with_feedback", AsyncMock(return_value=None)),
        ):
            events = [
                e
                async for e in skill._review_loop(
                    state,
                    [],
                    [],
                    self._make_parsed(),
                    10.0,
                    Path("tmp"),
                    1080,
                    1920,
                )
            ]
        assert len(events) == 1
        assert events[0]["data"]["issues"][0]["problem"] == "段落卡与标记笔同屏叠字"

    @pytest.mark.asyncio
    async def test_通过轮带建议明细(self):
        """passed=True 时建议级问题也要落盘（suggestion 有价值）"""
        from capabilities.video.services.overlay_review import ReviewIssue, ReviewResult

        skill = self._make_skill()
        state = self._make_state()
        result = ReviewResult(
            passed=True,
            issues=[
                ReviewIssue(
                    segment_id="o3",
                    severity="suggestion",
                    problem="图标偏小",
                    suggestion="放大 10%",
                ),
            ],
        )
        with patch(
            "capabilities.video.services.overlay_review.review_overlays",
            new=AsyncMock(return_value=result),
        ):
            events = [
                e
                async for e in skill._review_loop(
                    state,
                    [],
                    [],
                    self._make_parsed(),
                    10.0,
                    Path("tmp"),
                    1080,
                    1920,
                )
            ]
        assert len(events) == 1
        assert events[0]["data"]["issues"][0]["problem"] == "图标偏小"
