"""G2 布局编排层测试 — layout_orchestrator 服务 + dynamic_layout skill。

设计依据 docs/smart-cut-and-overlay.md 10.18（已批准方案）：
  三重锁 = LLM 只产 LayoutTimeline JSON（纯数据）
         → 白名单/时间轴完整性/引用存在性校验（不过带错误清单回喂重试）
         → 确定性编译（分段渲染 + concat demuxer，零执行自由度）
  降级链 = 布局规划失败 → fallback 单段静态 pip → 再失败原片
"""
import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

import capabilities.video.services.layout_orchestrator as lo
from capabilities.video.services.layout_orchestrator import (
    DEFAULT_BG_COLOR,
    LayoutPrimitive,
    LayoutSegment,
    LayoutTimeline,
    PipCorner,
    build_render_plan,
    fallback_timeline,
    generate_layout_plan,
    parse_layout_output,
    review_layout,
    validate_layout_timeline,
)


def _seg(start, end, primitive, **kw):
    return LayoutSegment(start=start, end=end, primitive=primitive, **kw)


def _timeline(*segs, bg=DEFAULT_BG_COLOR):
    return LayoutTimeline(segments=list(segs), bg_color=bg)


def _valid_timeline(duration=22.0, broll=True):
    """合法三段式：FULL 开场 → PIP+配图讲核心 → FULL 收束。"""
    return _timeline(
        _seg(0, 4, "full", reason="开场"),
        _seg(4, 16, "pip", pip_position=PipCorner.BOTTOM_RIGHT,
             pip_scale=0.3, main_source="shot:0" if broll else "color",
             reason="核心内容配图"),
        _seg(16, duration, "full", reason="收束"),
    )


# ── 1. validate_layout_timeline — 白名单与时间轴完整性 ──────────

class TestValidateTimeline:
    def test_合法三段式_零问题(self):
        problems = validate_layout_timeline(_valid_timeline(), 22.0, broll_count=1)
        assert problems == []

    def test_空布局被拦(self):
        assert validate_layout_timeline(_timeline(), 22.0, 1)

    def test_未从零开始被拦(self):
        t = _timeline(_seg(0.5, 22, "full"))
        assert any("从 0" in p or "0" in p for p in validate_layout_timeline(t, 22.0, 1))

    def test_未铺满结尾被拦(self):
        t = _timeline(_seg(0, 20, "full"))
        assert any("铺满" in p or "结尾" in p for p in validate_layout_timeline(t, 22.0, 1))

    def test_段间留缝被拦(self):
        t = _timeline(_seg(0, 5, "full"), _seg(6, 22, "full"))
        assert any("缝" in p or "gap" in p.lower() or "相接" in p
                   for p in validate_layout_timeline(t, 22.0, 1))

    def test_段间重叠被拦(self):
        t = _timeline(_seg(0, 8, "full"), _seg(6, 22, "full"))
        assert any("重叠" in p for p in validate_layout_timeline(t, 22.0, 1))

    def test_单段过短被拦(self):
        t = _timeline(
            _seg(0, 4, "full"),
            _seg(4, 5.2, "pip", pip_position=PipCorner.BOTTOM_LEFT,
                 pip_scale=0.3, main_source="color"),
            _seg(5.2, 22, "full"),
        )
        assert any("1.5" in p or "过短" in p for p in validate_layout_timeline(t, 22.0, 1))

    def test_段数超上限被拦(self):
        segs = []
        edges = [i * 2.0 for i in range(12)] + [24.0]
        for s, e in zip(edges, edges[1:]):
            segs.append(_seg(s, e, "full"))
        t = _timeline(*segs)
        assert any("段数" in p or "碎片" in p for p in validate_layout_timeline(t, 24.0, 1))

    def test_pip缺main_source被拦(self):
        t = _timeline(_seg(0, 22, "pip", pip_position=PipCorner.BOTTOM_RIGHT, pip_scale=0.3))
        assert any("main_source" in p for p in validate_layout_timeline(t, 22.0, 1))

    def test_pip_scale越界被拦(self):
        t = _timeline(_seg(0, 22, "pip", pip_position=PipCorner.BOTTOM_RIGHT,
                           pip_scale=0.9, main_source="color"))
        assert any("pip_scale" in p for p in validate_layout_timeline(t, 22.0, 1))

    def test_引用不存在的配图被拦(self):
        t = _timeline(_seg(0, 22, "pip", pip_position=PipCorner.BOTTOM_RIGHT,
                           pip_scale=0.3, main_source="shot:5"))
        assert any("shot:5" in p for p in validate_layout_timeline(t, 22.0, 1))

    def test_无配图时shot引用被拦(self):
        t = _timeline(_seg(0, 22, "pip", pip_position=PipCorner.BOTTOM_RIGHT,
                           pip_scale=0.3, main_source="shot:0"))
        assert validate_layout_timeline(t, 22.0, broll_count=0)

    def test_broll用color被拦(self):
        t = _timeline(_seg(0, 22, "broll", main_source="color"))
        assert any("broll" in p for p in validate_layout_timeline(t, 22.0, 1))

    def test_broll无配图被拦(self):
        t = _timeline(_seg(0, 22, "broll", main_source="shot:0"))
        assert validate_layout_timeline(t, 22.0, broll_count=0)

    def test_非法bg_color被拦(self):
        t = _timeline(_seg(0, 22, "full"), bg="red")
        assert any("bg_color" in p for p in validate_layout_timeline(t, 22.0, 1))

    def test_full段main_source被忽略不报错(self):
        t = _timeline(_seg(0, 22, "full", main_source="shot:9"))
        assert validate_layout_timeline(t, 22.0, 1) == []


# ── 2. parse_layout_output — 防御式解析（仿 _parse_plan_output） ──

class TestParseLayoutOutput:
    def test_合法JSON解析(self):
        raw = json.dumps({
            "segments": [
                {"start": 0, "end": 4, "primitive": "full", "reason": "开场"},
                {"start": 4, "end": 16, "primitive": "pip",
                 "pip_position": "bottom_right", "pip_scale": 0.3,
                 "main_source": "color", "reason": "核心"},
                {"start": 16, "end": 22, "primitive": "full", "reason": "收束"},
            ],
            "bg_color": "#101020",
        })
        timeline, problems = parse_layout_output(raw, 22.0, 0)
        assert problems == []
        assert timeline.bg_color == "#101020"
        assert len(timeline.segments) == 3

    def test_裸数组兼容(self):
        raw = json.dumps([{"start": 0, "end": 22, "primitive": "full"}])
        timeline, problems = parse_layout_output(raw, 22.0, 0)
        assert problems == []
        assert len(timeline.segments) == 1

    def test_pip缺position默认补齐(self):
        raw = json.dumps([{
            "start": 0, "end": 22, "primitive": "pip",
            "pip_scale": 0.3, "main_source": "color",
        }])
        timeline, problems = parse_layout_output(raw, 22.0, 0)
        assert problems == []
        assert timeline.segments[0].pip_position == PipCorner.BOTTOM_RIGHT

    def test_坏段跳过并产生时间轴问题(self):
        raw = json.dumps([
            {"start": 0, "end": 8, "primitive": "full"},
            {"start": 8, "end": 16, "primitive": "不存在的原语"},
            {"start": 16, "end": 22, "primitive": "full"},
        ])
        timeline, problems = parse_layout_output(raw, 22.0, 0)
        assert len(timeline.segments) == 2
        assert problems  # 0-8 与 16-22 之间留缝

    def test_时间越界clamp(self):
        raw = json.dumps([{"start": -3, "end": 99, "primitive": "full"}])
        timeline, _ = parse_layout_output(raw, 22.0, 0)
        assert timeline.segments[0].start == 0
        assert timeline.segments[0].end == 22.0

    def test_乱序自动排序(self):
        raw = json.dumps([
            {"start": 16, "end": 22, "primitive": "full"},
            {"start": 0, "end": 16, "primitive": "full"},
        ])
        timeline, problems = parse_layout_output(raw, 22.0, 0)
        assert problems == []
        assert timeline.segments[0].start == 0

    def test_非JSON输出报解析问题(self):
        timeline, problems = parse_layout_output("我觉得应该人像全屏", 22.0, 0)
        assert timeline.segments == []
        assert problems


# ── 3. fallback_timeline — 降级：单段静态 pip ──────────────────

class TestFallbackTimeline:
    def test_有配图用shot0(self):
        t = fallback_timeline(22.0, broll_count=1)
        assert len(t.segments) == 1
        seg = t.segments[0]
        assert seg.primitive == LayoutPrimitive.PIP
        assert seg.main_source == "shot:0"
        assert (seg.start, seg.end) == (0.0, 22.0)
        assert validate_layout_timeline(t, 22.0, 1) == []

    def test_无配图用color(self):
        t = fallback_timeline(22.0, broll_count=0)
        assert t.segments[0].main_source == "color"
        assert validate_layout_timeline(t, 22.0, 0) == []


# ── 4. build_render_plan — 确定性编译（纯函数，不执行） ─────────

class TestBuildRenderPlan:
    W, H = 1920, 1080

    def test_段数与命令一一对应(self):
        plan = build_render_plan(
            "v.mp4", _valid_timeline(), ["img0.png"], (self.W, self.H),
            Path("tmp"),
        )
        assert len(plan) == 3
        assert all(step.cmd and step.cmd[0] == "ffmpeg" for step in plan)

    def test_full段_剪切重编码_无fade(self):
        plan = build_render_plan(
            "v.mp4", _timeline(_seg(0, 4, "full")), [], (self.W, self.H), Path("tmp"),
        )
        cmd = plan[0].cmd
        joined = " ".join(cmd)
        assert "-ss" in cmd and "0.000" in joined
        assert "-t" in cmd and "4.000" in joined
        assert "libx264" in cmd and "aac" in cmd and "yuv420p" in cmd
        # 布局切点落在语流中 — 不做 fade（区别于 smart_cut 的静音切口）
        assert "fade" not in joined

    def test_pip_shot段_letterbox加overlay(self):
        plan = build_render_plan(
            "v.mp4",
            _timeline(_seg(0, 10, "pip", pip_position=PipCorner.BOTTOM_RIGHT,
                           pip_scale=0.3, main_source="shot:0")),
            ["broll.png"], (self.W, self.H), Path("tmp"),
        )
        joined = " ".join(plan[0].cmd)
        assert "-loop" in plan[0].cmd
        assert "broll.png" in joined
        # 配图 letterbox：等比缩小 + pad 居中
        assert "force_original_aspect_ratio=decrease" in joined
        assert f"pad={self.W}:{self.H}" in joined
        # pip 窗口 30%：576 宽；右下角 + 20px 边距
        assert "scale=576:324" in joined
        assert "overlay=1324:736" in joined
        assert "-map" in plan[0].cmd and "1:a?" in joined

    def test_pip_color段_lavfi纯色背景(self):
        plan = build_render_plan(
            "v.mp4",
            _timeline(_seg(0, 10, "pip", pip_position=PipCorner.TOP_LEFT,
                           pip_scale=0.3, main_source="color"),
                      bg="#1a1a2e"),
            [], (self.W, self.H), Path("tmp"),
        )
        joined = " ".join(plan[0].cmd)
        assert "color=c=0x1a1a2e" in joined
        assert f"s={self.W}x{self.H}" in joined
        # 左上角：20:20
        assert "overlay=20:20" in joined

    def test_broll段_配图全屏无人像窗口(self):
        plan = build_render_plan(
            "v.mp4",
            _timeline(_seg(0, 10, "broll", main_source="shot:1")),
            ["a.png", "b.png"], (self.W, self.H), Path("tmp"),
        )
        joined = " ".join(plan[0].cmd)
        assert "b.png" in joined
        # BROLL = 配图全屏，无 pip 窗口 → 无 overlay
        assert "overlay=" not in joined

    def test_broll段命令引用正确配图(self):
        plan = build_render_plan(
            "v.mp4",
            _timeline(_seg(0, 10, "broll", main_source="shot:1")),
            ["a.png", "b.png"], (self.W, self.H), Path("tmp"),
        )
        joined = " ".join(plan[0].cmd)
        assert "b.png" in joined and "a.png" not in joined

    def test_四角位置几何(self):
        cases = {
            PipCorner.BOTTOM_RIGHT: (1324, 736),
            PipCorner.BOTTOM_LEFT: (20, 736),
            PipCorner.TOP_RIGHT: (1324, 20),
            PipCorner.TOP_LEFT: (20, 20),
        }
        for corner, (x, y) in cases.items():
            plan = build_render_plan(
                "v.mp4",
                _timeline(_seg(0, 5, "pip", pip_position=corner,
                               pip_scale=0.3, main_source="color")),
                [], (self.W, self.H), Path("tmp"),
            )
            assert f"overlay={x}:{y}" in " ".join(plan[0].cmd), corner

    def test_scale各档窗口几何(self):
        for scale, pw in ((0.1, 192), (0.3, 576), (0.5, 960)):
            plan = build_render_plan(
                "v.mp4",
                _timeline(_seg(0, 5, "pip", pip_position=PipCorner.BOTTOM_LEFT,
                               pip_scale=scale, main_source="color")),
                [], (self.W, self.H), Path("tmp"),
            )
            assert f"scale={pw}:" in " ".join(plan[0].cmd), scale


# ── 5. generate_layout_plan — LLM 规划与反馈重试 ────────────────

class _FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts = []

    async def chat(self, messages):
        self.prompts.append(messages)
        return {"content": self.responses.pop(0)}

    def prompt_text(self, i=0):
        """提取第 i 次 prompt 的纯文本（llm_prompt_to_text 包了 messages 层）。"""
        msg = self.prompts[i]
        if isinstance(msg, str):
            return msg
        if isinstance(msg, list) and msg and isinstance(msg[0], dict):
            return str(msg[0].get("content", ""))
        return json.dumps(msg, ensure_ascii=False)


_GOOD_PLAN = json.dumps({
    "segments": [
        {"start": 0, "end": 4, "primitive": "full"},
        {"start": 4, "end": 16, "primitive": "pip", "pip_position": "bottom_right",
         "pip_scale": 0.3, "main_source": "color"},
        {"start": 16, "end": 22, "primitive": "full"},
    ],
})


class TestGenerateLayoutPlan:
    def _run(self, llm):
        return asyncio.run(generate_layout_plan(
            llm, duration=22.0, brolls=[],
        ))

    def test_合法输出一次通过(self):
        llm = _FakeLLM([_GOOD_PLAN])
        timeline, raw = self._run(llm)
        assert timeline is not None
        assert len(timeline.segments) == 3
        assert len(llm.prompts) == 1

    def test_校验失败带反馈重试(self):
        bad = json.dumps([{"start": 0, "end": 10, "primitive": "full"}])  # 未铺满
        llm = _FakeLLM([bad, _GOOD_PLAN])
        timeline, _ = self._run(llm)
        assert timeline is not None
        assert len(llm.prompts) == 2
        # 第二次 prompt 头部带错误清单
        second = llm.prompt_text(1)
        assert "未通过校验" in second or "铺满" in second

    def test_重试耗尽返回None(self):
        bad = json.dumps([{"start": 0, "end": 10, "primitive": "full"}])
        llm = _FakeLLM([bad, bad, bad])
        timeline, _ = self._run(llm)
        assert timeline is None
        assert len(llm.prompts) == 3

    def test_llm缺失返回None(self):
        timeline, _ = self._run(None)
        assert timeline is None

    def test_无配图时prompt声明broll不可用(self):
        llm = _FakeLLM([_GOOD_PLAN])
        self._run(llm)
        assert "无可用配图" in llm.prompt_text(0)

    def test_有配图时prompt列出shot引用(self):
        llm = _FakeLLM([_GOOD_PLAN])
        asyncio.run(generate_layout_plan(
            llm, duration=22.0, brolls=["storyboard/shot_0.png"],
        ))
        assert "shot:0" in llm.prompt_text(0)


# ── 6. review_layout — P0 审片平移（VLM 布局审查） ─────────────

class TestReviewLayout:
    def _timeline(self):
        return _valid_timeline()

    @staticmethod
    def _vlm(response=None, error=None):
        """MagicMock + chat=AsyncMock — AsyncMock 的子属性 chat 不继承
        return_value，必须显式配置（踩坑实录：mock.chat() 返回的是
        新 AsyncMock 而非期望响应）。"""
        adapter = MagicMock()
        if error is not None:
            adapter.chat = AsyncMock(side_effect=error)
        else:
            adapter.chat = AsyncMock(return_value=response)
        return adapter

    def test_通过时返回None(self, monkeypatch):
        adapter = self._vlm(response={"content": json.dumps(
            {"verdict": "pass", "problems": []})})
        monkeypatch.setattr(lo, "_extract_frames", AsyncMock(return_value=[Path("f0.jpg")] * 3))
        monkeypatch.setattr(lo, "_encode_frame_b64", lambda p: "AAAA")
        result = asyncio.run(review_layout("out.mp4", self._timeline(), adapter))
        assert result is None

    def test_发现问题返回反馈文本(self, monkeypatch):
        adapter = self._vlm(response={"content": json.dumps({
            "verdict": "fail",
            "problems": ["第2帧：pip 窗口遮挡了配图中的关键文字"],
        })})
        monkeypatch.setattr(lo, "_extract_frames", AsyncMock(return_value=[Path("f0.jpg")] * 3))
        monkeypatch.setattr(lo, "_encode_frame_b64", lambda p: "AAAA")
        result = asyncio.run(review_layout("out.mp4", self._timeline(), adapter))
        assert result is not None
        assert "遮挡" in result

    def test_VLM不可用降级返回None(self, monkeypatch):
        adapter = self._vlm(error=Exception("dashscope down"))
        monkeypatch.setattr(lo, "_extract_frames", AsyncMock(return_value=[Path("f0.jpg")] * 3))
        monkeypatch.setattr(lo, "_encode_frame_b64", lambda p: "AAAA")
        result = asyncio.run(review_layout("out.mp4", self._timeline(), adapter))
        assert result is None

    def test_坏JSON降级通过(self, monkeypatch):
        adapter = self._vlm(response={"content": "看起来没问题！"})
        monkeypatch.setattr(lo, "_extract_frames", AsyncMock(return_value=[Path("f0.jpg")] * 3))
        monkeypatch.setattr(lo, "_encode_frame_b64", lambda p: "AAAA")
        assert asyncio.run(review_layout("out.mp4", self._timeline(), adapter)) is None


# ── 7. dynamic_layout skill — 闸门/执行/降级 ───────────────────

class TestDynamicLayoutSkill:
    def _skill(self):
        from capabilities.video.skills.dynamic_layout import DynamicLayoutSkill
        return DynamicLayoutSkill()

    def _collect(self, gen):
        out = []

        async def _drain():
            async for chunk in gen:
                out.append(chunk)

        asyncio.run(_drain())
        return out

    def test_规划模式_只出闸门事件不出片(self, monkeypatch, tmp_path):
        skill = self._skill()
        video_file = tmp_path / "v.mp4"
        video_file.write_bytes(b"v")
        compile_mock = AsyncMock()
        monkeypatch.setattr(
            "capabilities.video.skills.dynamic_layout.compile_layout", compile_mock,
        )
        monkeypatch.setattr(
            "capabilities.video.skills.dynamic_layout.generate_layout_plan",
            AsyncMock(return_value=(_valid_timeline(), _GOOD_PLAN)),
        )
        monkeypatch.setattr(
            "capabilities.video.skills.dynamic_layout._probe_duration",
            AsyncMock(return_value=22.0),
        )
        events = self._collect(skill.execute({
            "video_path": str(video_file),
            "brolls": ["img0.png"],
        }))
        assert len(events) == 1
        ev = events[0]
        assert ev.get("event") == "layout_plan_ready"
        assert ev.get("needs_confirmation") is True
        assert ev["data"]["timeline"]["segments"][0]["primitive"] == "full"
        compile_mock.assert_not_awaited()

    def test_auto_confirm_端到端合成(self, monkeypatch, tmp_path):
        skill = self._skill()
        video_file = tmp_path / "v.mp4"
        video_file.write_bytes(b"v")
        out_file = tmp_path / "out.mp4"
        out_file.write_bytes(b"x")
        monkeypatch.setattr(
            "capabilities.video.skills.dynamic_layout.generate_layout_plan",
            AsyncMock(return_value=(_valid_timeline(), _GOOD_PLAN)),
        )
        monkeypatch.setattr(
            "capabilities.video.skills.dynamic_layout.compile_layout",
            AsyncMock(return_value=out_file),
        )
        monkeypatch.setattr(
            "capabilities.video.skills.dynamic_layout._probe_duration",
            AsyncMock(return_value=22.0),
        )
        monkeypatch.setattr(
            "capabilities.video.skills.dynamic_layout._probe_resolution",
            AsyncMock(return_value=(1920, 1080)),
        )
        monkeypatch.setattr(
            "capabilities.video.skills.dynamic_layout._resolve_llm",
            MagicMock(return_value=object()),
        )
        monkeypatch.setattr(
            "capabilities.video.skills.dynamic_layout.review_layout",
            AsyncMock(return_value=None),
        )
        monkeypatch.setattr(
            "capabilities.video.skills.dynamic_layout._OUTPUT_DIR", tmp_path,
        )
        events = self._collect(skill.execute({
            "video_path": str(video_file),
            "gate_mode": "auto_confirm",
            "brolls": [],
        }))
        done = [e for e in events if e.get("event") == "layout_compose_done"]
        assert done, events
        assert done[0]["needs_confirmation"] is False

    def test_布局规划失败降级静态pip(self, monkeypatch, tmp_path):
        skill = self._skill()
        video_file = tmp_path / "v.mp4"
        video_file.write_bytes(b"v")
        out_file = tmp_path / "out.mp4"
        out_file.write_bytes(b"x")
        monkeypatch.setattr(
            "capabilities.video.skills.dynamic_layout.generate_layout_plan",
            AsyncMock(return_value=(None, "")),
        )
        monkeypatch.setattr(
            "capabilities.video.skills.dynamic_layout.compile_layout",
            AsyncMock(return_value=out_file),
        )
        monkeypatch.setattr(
            "capabilities.video.skills.dynamic_layout._probe_duration",
            AsyncMock(return_value=22.0),
        )
        monkeypatch.setattr(
            "capabilities.video.skills.dynamic_layout._probe_resolution",
            AsyncMock(return_value=(1920, 1080)),
        )
        monkeypatch.setattr(
            "capabilities.video.skills.dynamic_layout.review_layout",
            AsyncMock(return_value=None),
        )
        monkeypatch.setattr(
            "capabilities.video.skills.dynamic_layout._OUTPUT_DIR", tmp_path,
        )
        events = self._collect(skill.execute({
            "video_path": str(video_file),
            "gate_mode": "auto_confirm",
            "brolls": [],
        }))
        done = [e for e in events if e.get("event") == "layout_compose_done"]
        assert done
        # 降级说明出现在事件流里
        all_text = json.dumps(events, ensure_ascii=False)
        assert "降级" in all_text or "fallback" in all_text

    def test_确认回调执行用户计划(self, monkeypatch, tmp_path):
        skill = self._skill()
        video_file = tmp_path / "v.mp4"
        video_file.write_bytes(b"v")
        out_file = tmp_path / "out.mp4"
        out_file.write_bytes(b"x")
        compile_mock = AsyncMock(return_value=out_file)
        monkeypatch.setattr(
            "capabilities.video.skills.dynamic_layout.compile_layout", compile_mock,
        )
        monkeypatch.setattr(
            "capabilities.video.skills.dynamic_layout.generate_layout_plan",
            AsyncMock(),
        )
        monkeypatch.setattr(
            "capabilities.video.skills.dynamic_layout._probe_duration",
            AsyncMock(return_value=22.0),
        )
        monkeypatch.setattr(
            "capabilities.video.skills.dynamic_layout._probe_resolution",
            AsyncMock(return_value=(1920, 1080)),
        )
        monkeypatch.setattr(
            "capabilities.video.skills.dynamic_layout.review_layout",
            AsyncMock(return_value=None),
        )
        monkeypatch.setattr(
            "capabilities.video.skills.dynamic_layout._OUTPUT_DIR", tmp_path,
        )
        plan_data = _valid_timeline(broll=False).model_dump()  # color 主画面（brolls 为空时 shot 引用会被拒）
        events = self._collect(skill.execute({
            "video_path": str(video_file),
            "layout_plan": plan_data,
            "brolls": [],
        }))
        compile_mock.assert_awaited_once()
        # 用户确认的计划原样进编译（不被重新规划）
        args = compile_mock.await_args
        assert args.args[1].segments[0].primitive == LayoutPrimitive.FULL

    def test_确认计划校验不过_拒绝执行(self, monkeypatch, tmp_path):
        skill = self._skill()
        video_file = tmp_path / "v.mp4"
        video_file.write_bytes(b"v")
        compile_mock = AsyncMock()
        monkeypatch.setattr(
            "capabilities.video.skills.dynamic_layout.compile_layout", compile_mock,
        )
        monkeypatch.setattr(
            "capabilities.video.skills.dynamic_layout._probe_duration",
            AsyncMock(return_value=22.0),
        )
        events = self._collect(skill.execute({
            "video_path": str(video_file),
            "layout_plan": {"segments": []},  # 空计划
            "brolls": [],
        }))
        compile_mock.assert_not_awaited()
        assert events and not events[0].get("success", True)

    def test_视频不存在_失败事件(self):
        skill = self._skill()
        events = self._collect(skill.execute({"video_path": "no_such.mp4"}))
        assert events[0].get("success") is False
