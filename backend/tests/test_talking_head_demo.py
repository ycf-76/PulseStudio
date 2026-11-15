# -*- coding: utf-8 -*-
"""口播演示一键（talking_head_demo）编排 skill — TDD 先行。

背景（2026-11-08 用户实机反馈）：前端启动口播任务后成片只有原片
（无小窗/无幻灯片）。根因：前端靠 LLM 组装参数，注入示例只传
video_path → overlay 默认走字幕增强链，pip/matting/auto_deck/handoff
全没开。修复：确定性编排 skill（同 auto_short_video 先例）——
smart_cut 裁剪串联 → overlay PIP 演示链全参数直通（auto_confirm），
LLM 只需传 video_path。

覆盖：
  - 正常链：smart_cut 两轮（plan → refine）→ overlay 全参数
    （pip/matting/auto_deck/handoff/auto_confirm + 裁剪回传 + 身份透传）
    → 最终 success + output_path
  - smart_cut 失败 → 降级原片直通 overlay（绝不阻断出片）
  - overlay 无产出 → success False 带错误
  - cut_enabled=False → 跳过裁剪直接 overlay
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

import pytest


def _fake_smart_cut(calls: list):
    """SmartCutSkill mock：两轮契约（plan → refine → output/kept）。"""
    fake = MagicMock()

    async def _execute(params):
        calls.append(dict(params))
        if len(calls) == 1:
            return {"mode": "plan", "plan": {"candidates": ["c1", "c2"]}}
        return {"output_path": "D:/fake/cut.mp4", "kept_segments": [(0.0, 30.0)]}

    fake.execute = _execute
    return fake


def _fake_overlay(captured: list, events: list):
    """TalkingHeadOverlaySkill mock：记录参数 + 回放事件。"""
    fake = MagicMock()

    async def _gen(params):
        captured.append(dict(params))
        for e in events:
            yield e

    fake.execute = lambda params: _gen(params)
    return fake


def _demo():
    from capabilities.video.skills.talking_head_demo import TalkingHeadDemoSkill

    return TalkingHeadDemoSkill()


class TestTalkingHeadDemo:
    @pytest.mark.asyncio
    async def test_orchestrates_full_chain(self, tmp_path):
        """正常链：裁剪两轮 → overlay 全参数 → 最终 success。"""
        video = tmp_path / "src.mp4"
        video.write_bytes(b"")
        cut_calls, overlay_captured = [], []
        events = [
            {"event": "deck_ready", "data": {"count": 5, "theme": "midnight"},
             "needs_confirmation": False},
            {"event": "pip_done",
             "data": {"output_path": "D:/fake/out.mp4", "pip_position": "top_right",
                      "matting": True, "handoff": {"t0": 7.0}},
             "needs_confirmation": False},
        ]
        with patch(
            "capabilities.video.skills.talking_head_demo.SmartCutSkill",
            return_value=_fake_smart_cut(cut_calls),
        ), patch(
            "capabilities.video.skills.talking_head_demo.TalkingHeadOverlaySkill",
            return_value=_fake_overlay(overlay_captured, events),
        ):
            results = [chunk async for chunk in _demo().execute({
                "video_path": str(video),
                "_user_id": "u1", "_workflow_id": "w1",
            })]

        # smart_cut 两轮：plan 带 chain_with_overlay，refine 吃 candidates
        assert len(cut_calls) == 2
        assert cut_calls[0]["detect_fluff"] is True
        assert cut_calls[0]["chain_with_overlay"] is True
        assert cut_calls[1]["refine_candidates"] == ["c1", "c2"]
        # overlay 全参数：前端链路此前缺的正是这些
        op = overlay_captured[0]
        assert op["pip"] is True
        assert op["matting"] is True
        assert op["auto_deck"] is True
        assert op["handoff"] is True
        assert op["gate_mode"] == "auto_confirm"      # 无人值守直通
        assert op["deck_theme"] == "auto"
        assert op["pip_position"] == "top_right"
        assert op["cut_video_path"] == "D:/fake/cut.mp4"
        assert op["kept_segments"] == [(0.0, 30.0)]
        # 身份透传（落盘分层/SSE 归属）
        assert op["_user_id"] == "u1"
        assert op["_workflow_id"] == "w1"
        # 最终结果带成片路径
        assert results[-1]["success"] is True
        assert results[-1]["output_path"] == "D:/fake/out.mp4"

    @pytest.mark.asyncio
    async def test_smart_cut_failure_falls_back_to_original(self, tmp_path):
        """smart_cut 挂 → 降级原片直通 overlay，绝不阻断出片。"""
        video = tmp_path / "src.mp4"
        video.write_bytes(b"")
        fake_cut = MagicMock()

        async def _boom(params):
            raise RuntimeError("whisper down")

        fake_cut.execute = _boom
        overlay_captured = []
        events = [{"event": "pip_done",
                   "data": {"output_path": "D:/fake/out.mp4"},
                   "needs_confirmation": False}]
        with patch(
            "capabilities.video.skills.talking_head_demo.SmartCutSkill",
            return_value=fake_cut,
        ), patch(
            "capabilities.video.skills.talking_head_demo.TalkingHeadOverlaySkill",
            return_value=_fake_overlay(overlay_captured, events),
        ):
            results = [chunk async for chunk in _demo().execute({
                "video_path": str(video),
            })]
        assert len(overlay_captured) == 1
        assert "cut_video_path" not in overlay_captured[0]
        assert "kept_segments" not in overlay_captured[0]
        assert results[-1]["success"] is True

    @pytest.mark.asyncio
    async def test_overlay_no_output_reports_error(self, tmp_path):
        """overlay 无 pip_done（内部失败）→ success False 带错误。"""
        video = tmp_path / "src.mp4"
        video.write_bytes(b"")
        overlay_captured = []
        events = [{"event": "overlay_error",
                   "data": {"error": "render failed"},
                   "needs_confirmation": False}]
        with patch(
            "capabilities.video.skills.talking_head_demo.SmartCutSkill",
            return_value=_fake_smart_cut([]),
        ), patch(
            "capabilities.video.skills.talking_head_demo.TalkingHeadOverlaySkill",
            return_value=_fake_overlay(overlay_captured, events),
        ):
            results = [chunk async for chunk in _demo().execute({
                "video_path": str(video),
            })]
        assert results[-1]["success"] is False
        assert "render failed" in results[-1]["error"]

    @pytest.mark.asyncio
    async def test_cut_disabled_skips_smart_cut(self, tmp_path):
        video = tmp_path / "src.mp4"
        video.write_bytes(b"")
        cut_calls, overlay_captured = [], []
        events = [{"event": "pip_done",
                   "data": {"output_path": "D:/fake/out.mp4"},
                   "needs_confirmation": False}]
        with patch(
            "capabilities.video.skills.talking_head_demo.SmartCutSkill",
            return_value=_fake_smart_cut(cut_calls),
        ), patch(
            "capabilities.video.skills.talking_head_demo.TalkingHeadOverlaySkill",
            return_value=_fake_overlay(overlay_captured, events),
        ):
            results = [chunk async for chunk in _demo().execute({
                "video_path": str(video), "cut_enabled": False,
            })]
        assert cut_calls == []
        assert "cut_video_path" not in overlay_captured[0]
        assert results[-1]["success"] is True

    @pytest.mark.asyncio
    async def test_missing_video_reports(self, tmp_path):
        results = [chunk async for chunk in _demo().execute({
            "video_path": str(tmp_path / "no.mp4"),
        })]
        assert results[-1]["success"] is False
        assert "not found" in results[-1]["error"]
