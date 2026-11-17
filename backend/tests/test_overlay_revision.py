"""10.19 对话式修改层测试 — 意图解析 + patch 执行器 + skill 路由。

设计依据 docs/smart-cut-and-overlay.md 10.19（已批准方案）：
  两级路由：local 指令代码级 patch（其余段逐字节不动）/
           global 指令走 feedback 重规划（现状行为）。
  三重锁：LLM 只产 RevisionInstruction JSON（纯数据）
        → patch 执行器白名单（4 动作-ADD 后置 / 禁改键 / hex 色校验）
        → patch 后强制过 resolve_overlay_conflicts + validate_plan_rhythm。
  DELETE 对齐冲突消解先例：skipped=True 可追溯，不真删。
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

import capabilities.video.services.overlay_orchestrator as oo
from capabilities.video.services.overlay_orchestrator import (
    OverlayPlan,
    OverlaySegment,
    RevisionAction,
    RevisionInstruction,
    apply_revision,
    parse_revision_intent,
)


def _plan(*segments, palette=("#FF4D4D", "#4D79FF", "#FFD84D")):
    return OverlayPlan(video_id="v", segments=list(segments), palette=list(palette))


def _seg(sid, s, e, template="caption_pop", **params):
    return OverlaySegment(
        segment_id=sid,
        time_start=s,
        time_end=e,
        template=template,
        params=params,
    )


def _sample_plan():
    # 满足 validate_plan_rhythm：开场 3s 内 ≥2 段 + 结尾 3s 内有收束卡
    # （apply_revision patch 后强制跑自检，problems 须为空才能过干净路径）
    return _plan(
        _seg("seg0", 0.0, 0.5, "caption_pop", text="hi"),
        _seg("seg1", 0.5, 3.0, "caption_pop", text="开场", font_size=64),
        _seg("seg2", 4.0, 9.0, "full_card", text="卡片", color="#FF4D4D"),
        _seg("seg3", 10.0, 14.0, "keyword_icon", text="关键词"),
        _seg("seg4", 19.0, 22.0, "full_card", text="收束", color="#4D79FF"),
    )


def _by_id(plan, sid):
    return next(s for s in plan.segments if s.segment_id == sid)


# ── 1. parse_revision_intent — LLM 意图解析（三重锁第 1 层） ────


class _FakeLLM:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    async def chat(self, messages):
        self.calls.append(messages)
        if self.error:
            raise self.error
        return {"content": self.response}


_LOCAL_OK = json.dumps(
    {
        "scope": "local",
        "instructions": [
            {
                "segment_ids": ["seg2"],
                "action": "update",
                "params": {"text": "新卡片文案"},
                "locate_hint": "那个 full_card 卡片",
            }
        ],
    }
)
_GLOBAL_OK = json.dumps({"scope": "global", "reason": "整体风格调整"})


class TestParseRevisionIntent:
    def _run(self, llm, plan=None):
        return asyncio.run(
            parse_revision_intent(
                llm,
                "把卡片文案改一下",
                plan or _sample_plan(),
                22.0,
            )
        )

    def test_local指令解析成功(self):
        result, problems = self._run(_FakeLLM(_LOCAL_OK))
        assert problems == []
        assert result.route == "local"
        assert result.instructions[0].segment_ids == ["seg2"]
        assert result.instructions[0].action == RevisionAction.UPDATE

    def test_global指令无instructions(self):
        result, problems = self._run(_FakeLLM(_GLOBAL_OK))
        assert problems == []
        assert result.route == "global"
        assert result.instructions == []

    def test_坏JSON返回None带问题(self):
        result, problems = self._run(_FakeLLM("我觉得应该改大一点"))
        assert result is None
        assert problems

    def test_LLM异常返回None(self):
        result, _ = self._run(_FakeLLM(error=Exception("api down")))
        assert result is None

    def test_LLM缺失返回None(self):
        result, problems = self._run(None)
        assert result is None
        assert problems

    def test_越权action被拒(self):
        bad = json.dumps(
            {
                "scope": "local",
                "instructions": [
                    {
                        "segment_ids": ["seg1"],
                        "action": "add",
                        "params": {"template": "title_card"},
                    }
                ],
            }
        )
        result, problems = self._run(_FakeLLM(bad))
        assert result.route == "local"
        assert result.instructions == []  # 越权指令剔除
        assert any("add" in p for p in problems)

    def test_定位不存在的段被拒并回显hint(self):
        bad = json.dumps(
            {
                "scope": "local",
                "instructions": [
                    {
                        "segment_ids": ["seg99"],
                        "action": "delete",
                        "locate_hint": "那个蓝色的卡片",
                    }
                ],
            }
        )
        result, problems = self._run(_FakeLLM(bad))
        assert result.instructions == []
        assert any("seg99" in p and "蓝色的卡片" in p for p in problems)

    def test_prompt含plan摘要与原话(self):
        llm = _FakeLLM(_LOCAL_OK)
        self._run(llm)
        text = str(llm.calls[0][0]["content"])
        assert "seg2" in text and "full_card" in text
        assert "把卡片文案改一下" in text


# ── 2. apply_revision — patch 执行器（纯函数，三重锁第 2 层） ───


class TestApplyRevision:
    def test_update_只动目标段其余逐字节一致(self):
        plan = _sample_plan()
        before = {s.segment_id: s.model_dump() for s in plan.segments}
        patched, problems = apply_revision(
            plan,
            [
                RevisionInstruction(
                    segment_ids=["seg2"],
                    action=RevisionAction.UPDATE,
                    params={"text": "新文案", "font_size": 80},
                    raw_text="改卡片",
                ),
            ],
            22.0,
        )
        assert problems == []
        after = {s.segment_id: s.model_dump() for s in patched.segments}
        assert after["seg2"]["params"]["text"] == "新文案"
        assert after["seg2"]["params"]["font_size"] == 80
        # 其余段逐字节一致（局部修改不波及无辜 — 方案核心承诺）
        assert after["seg1"] == before["seg1"]
        assert after["seg3"] == before["seg3"]

    def test_update_禁改时间键(self):
        plan = _sample_plan()
        patched, problems = apply_revision(
            plan,
            [
                RevisionInstruction(
                    segment_ids=["seg1"],
                    action=RevisionAction.UPDATE,
                    params={"time_start": 9.0},
                    raw_text="挪时间",
                ),
            ],
            22.0,
        )
        assert patched.segments[0].skipped is False  # 未被动
        assert _by_id(patched, "seg1").time_start == 0.5  # 未被动
        assert any("time_start" in p for p in problems)

    def test_update_禁改template(self):
        plan = _sample_plan()
        patched, problems = apply_revision(
            plan,
            [
                RevisionInstruction(
                    segment_ids=["seg1"],
                    action=RevisionAction.UPDATE,
                    params={"template": "title_card"},
                    raw_text="换模板",
                ),
            ],
            22.0,
        )
        assert _by_id(patched, "seg1").template == "caption_pop"
        assert any("template" in p for p in problems)

    def test_update_非法颜色被拒(self):
        plan = _sample_plan()
        patched, problems = apply_revision(
            plan,
            [
                RevisionInstruction(
                    segment_ids=["seg2"],
                    action=RevisionAction.UPDATE,
                    params={"color": "红色"},
                    raw_text="改色",
                ),
            ],
            22.0,
        )
        assert _by_id(patched, "seg2").params["color"] == "#FF4D4D"
        assert any("color" in p for p in problems)

    def test_update_合法hex色放行(self):
        plan = _sample_plan()
        patched, problems = apply_revision(
            plan,
            [
                RevisionInstruction(
                    segment_ids=["seg2"],
                    action=RevisionAction.UPDATE,
                    params={"color": "#4D79FF"},
                    raw_text="改色",  # 盘内色（盘外色会被节奏自检如实上报）
                ),
            ],
            22.0,
        )
        assert problems == []
        assert _by_id(patched, "seg2").params["color"] == "#4D79FF"

    def test_delete_标skipped保留可追溯(self):
        plan = _sample_plan()
        patched, problems = apply_revision(
            plan,
            [
                RevisionInstruction(
                    segment_ids=["seg2"],
                    action=RevisionAction.DELETE,
                    raw_text="删掉",
                ),
            ],
            22.0,
        )
        assert problems == []
        assert len(patched.segments) == 5  # 保留不真删（对齐冲突消解先例）
        assert _by_id(patched, "seg2").skipped is True

    def test_move_绝对时间clamp(self):
        plan = _sample_plan()
        patched, problems = apply_revision(
            plan,
            [
                RevisionInstruction(
                    segment_ids=["seg3"],
                    action=RevisionAction.MOVE,
                    params={"time_start": -2.0, "time_end": 99.0},
                    raw_text="挪到结尾",
                ),
            ],
            22.0,
        )
        assert problems == []
        assert _by_id(patched, "seg3").time_start == 0.0
        assert _by_id(patched, "seg3").time_end == 22.0

    def test_move_缺end被拒(self):
        plan = _sample_plan()
        patched, problems = apply_revision(
            plan,
            [
                RevisionInstruction(
                    segment_ids=["seg3"],
                    action=RevisionAction.MOVE,
                    params={"time_start": 11.0},
                    raw_text="挪",
                ),
            ],
            22.0,
        )
        assert _by_id(patched, "seg3").time_start == 10.0
        assert problems

    def test_定位失败_该指令拒绝其余执行(self):
        plan = _sample_plan()
        patched, problems = apply_revision(
            plan,
            [
                RevisionInstruction(
                    segment_ids=["seg99"],
                    action=RevisionAction.DELETE,
                    raw_text="删",
                ),
                RevisionInstruction(
                    segment_ids=["seg1"],
                    action=RevisionAction.UPDATE,
                    params={"text": "改到了"},
                    raw_text="改",
                ),
            ],
            22.0,
        )
        assert any("seg99" in p for p in problems)
        assert _by_id(patched, "seg1").params["text"] == "改到了"  # 好指令照常执行
        assert _by_id(patched, "seg2").skipped is False

    def test_patch后过冲突消解与节奏自检(self):
        # patch 造成同带重叠 → resolve_overlay_conflicts 裁决 skipped
        plan = _sample_plan()
        patched, problems = apply_revision(
            plan,
            [
                RevisionInstruction(
                    segment_ids=["seg3"],
                    action=RevisionAction.MOVE,
                    params={"time_start": 4.5, "time_end": 8.5},
                    raw_text="挪到seg2同带",
                ),
            ],
            22.0,
        )
        moved = next(s for s in patched.segments if s.segment_id == "seg3")
        assert moved.time_start == 4.5
        # 冲突消解跑过（低优先级一方被标 skipped，具体哪方由优先级定）：
        overlapped = [
            s for s in patched.segments if s.time_start < 8.5 and s.time_end > 4.5 and s.skipped
        ]
        assert overlapped, "同带重叠必须被裁决"

    def test_空指令列表原样返回(self):
        plan = _sample_plan()
        patched, problems = apply_revision(plan, [], 22.0)
        assert problems == []
        assert patched == plan


# ── 3. skill 路由 — revision 进修改路径 / global / 护栏 / 反问 ──


class TestSkillRevisionRoute:
    def _skill(self):
        from capabilities.video.skills.talking_head_overlay import TalkingHeadOverlaySkill

        return TalkingHeadOverlaySkill()

    def _collect(self, gen):
        out = []

        async def _drain():
            async for chunk in gen:
                out.append(chunk)

        asyncio.run(_drain())
        return out

    def _patch_common(self, monkeypatch, tmp_path):
        video = tmp_path / "v.mp4"
        video.write_bytes(b"v")
        # skill 在 execute 内函数式 import → 必须 patch 源模块
        monkeypatch.setattr(
            "capabilities.video.adapters.silence_detect.probe_duration",
            AsyncMock(return_value=22.0),
        )
        return video

    def test_local路由_patch不重规划(self, monkeypatch, tmp_path):
        skill = self._skill()
        video = self._patch_common(monkeypatch, tmp_path)
        gen_mock = AsyncMock()
        monkeypatch.setattr(
            "capabilities.video.skills.talking_head_overlay.generate_overlay_plan",
            gen_mock,
            raising=False,
        )
        from capabilities.video.services.overlay_orchestrator import RevisionResult

        monkeypatch.setattr(
            "capabilities.video.skills.talking_head_overlay.parse_revision_intent",
            # 注意：必须返回 (result, problems) 二元组 — pydantic BaseModel
            # 自身可迭代（产出字段对），单值会被 `result, problems = ...` 静默解包错
            AsyncMock(
                return_value=(
                    RevisionResult(
                        route="local",
                        instructions=[
                            RevisionInstruction(
                                segment_ids=["seg1"],
                                action=RevisionAction.UPDATE,
                                params={"text": "新文案"},
                                raw_text="改文案",
                            )
                        ],
                    ),
                    [],
                )
            ),
            raising=False,
        )
        monkeypatch.setattr(
            "capabilities.video.skills.talking_head_overlay.apply_revision",
            MagicMock(return_value=(_sample_plan(), [])),
            raising=False,
        )
        # 跳过 whisper/timed_words 重建
        monkeypatch.setattr(
            "capabilities.video.skills.talking_head_overlay."
            "TalkingHeadOverlaySkill._build_timed_words",
            AsyncMock(return_value=([], [])),
            raising=False,
        )
        events = self._collect(
            skill.execute(
                {
                    "video_path": str(video),
                    "transcript": [
                        {
                            "start": 0,
                            "end": 2,
                            "text": "hi",
                            "words": [{"text": "hi", "start": 0, "end": 2}],
                        }
                    ],
                    "revision": {
                        "raw_text": "把开场文案改一下",
                        "current_plan": _sample_plan().model_dump(),
                        "revision_round": 1,
                    },
                }
            )
        )
        ready = [e for e in events if e.get("event") == "overlay_plan_ready"]
        assert ready, events
        assert ready[0]["data"].get("revision", {}).get("route") == "local"
        gen_mock.assert_not_awaited()  # local 不走重规划

    def test_global路由_走feedback重规划(self, monkeypatch, tmp_path):
        skill = self._skill()
        video = self._patch_common(monkeypatch, tmp_path)
        gen_mock = AsyncMock(return_value=(_sample_plan(), ""))
        monkeypatch.setattr(
            "capabilities.video.skills.talking_head_overlay.generate_overlay_plan",
            gen_mock,
            raising=False,
        )
        from capabilities.video.services.overlay_orchestrator import RevisionResult

        monkeypatch.setattr(
            "capabilities.video.skills.talking_head_overlay.parse_revision_intent",
            AsyncMock(
                return_value=(
                    RevisionResult(
                        route="global",
                        instructions=[],
                    ),
                    [],
                )
            ),
            raising=False,
        )
        monkeypatch.setattr(
            "capabilities.video.skills.talking_head_overlay."
            "TalkingHeadOverlaySkill._build_timed_words",
            AsyncMock(return_value=([], [])),
            raising=False,
        )
        monkeypatch.setattr(
            "capabilities.video.skills.talking_head_overlay."
            "TalkingHeadOverlaySkill._apply_constraints",
            MagicMock(side_effect=lambda p, plan: plan),
            raising=False,
        )
        events = self._collect(
            skill.execute(
                {
                    "video_path": str(video),
                    "transcript": [
                        {
                            "start": 0,
                            "end": 2,
                            "text": "hi",
                            "words": [{"text": "hi", "start": 0, "end": 2}],
                        }
                    ],
                    "revision": {
                        "raw_text": "整体再活泼一点",
                        "current_plan": _sample_plan().model_dump(),
                        "revision_round": 2,
                    },
                }
            )
        )
        ready = [e for e in events if e.get("event") == "overlay_plan_ready"]
        assert ready and ready[0]["data"].get("revision", {}).get("route") == "global"
        gen_mock.assert_awaited_once()
        assert gen_mock.await_args.kwargs.get("feedback") == "整体再活泼一点"

    def test_轮数超限拒绝(self, monkeypatch, tmp_path):
        skill = self._skill()
        video = self._patch_common(monkeypatch, tmp_path)
        events = self._collect(
            skill.execute(
                {
                    "video_path": str(video),
                    "revision": {
                        "raw_text": "再改",
                        "revision_round": 6,
                        "current_plan": _sample_plan().model_dump(),
                    },
                }
            )
        )
        assert events[0].get("event") == "revision_rejected"
        assert events[0]["data"]["error"]

    def test_解析失败反问不猜(self, monkeypatch, tmp_path):
        skill = self._skill()
        video = self._patch_common(monkeypatch, tmp_path)
        monkeypatch.setattr(
            "capabilities.video.skills.talking_head_overlay.parse_revision_intent",
            AsyncMock(return_value=(None, ["输出不是合法 JSON"]), raising=False),
        )
        events = self._collect(
            skill.execute(
                {
                    "video_path": str(video),
                    "transcript": [
                        {
                            "start": 0,
                            "end": 2,
                            "text": "hi",
                            "words": [{"text": "hi", "start": 0, "end": 2}],
                        }
                    ],
                    "revision": {
                        "raw_text": "那个改一下",
                        "revision_round": 1,
                        "current_plan": _sample_plan().model_dump(),
                    },
                }
            )
        )
        clarify = [e for e in events if e.get("event") == "revision_clarify"]
        assert clarify, events
        assert clarify[0]["needs_confirmation"] is True

    def test_apply有问题反问不静默(self, monkeypatch, tmp_path):
        skill = self._skill()
        video = self._patch_common(monkeypatch, tmp_path)
        from capabilities.video.services.overlay_orchestrator import RevisionResult

        monkeypatch.setattr(
            "capabilities.video.skills.talking_head_overlay.parse_revision_intent",
            AsyncMock(
                return_value=(
                    RevisionResult(
                        route="local",
                        instructions=[
                            RevisionInstruction(
                                segment_ids=["seg99"],
                                action=RevisionAction.DELETE,
                                raw_text="删",
                            )
                        ],
                    ),
                    [],
                )
            ),
            raising=False,
        )
        monkeypatch.setattr(
            "capabilities.video.skills.talking_head_overlay.apply_revision",
            MagicMock(return_value=(_sample_plan(), ["seg99 不在当前规划中"])),
            raising=False,
        )
        events = self._collect(
            skill.execute(
                {
                    "video_path": str(video),
                    "transcript": [
                        {
                            "start": 0,
                            "end": 2,
                            "text": "hi",
                            "words": [{"text": "hi", "start": 0, "end": 2}],
                        }
                    ],
                    "revision": {
                        "raw_text": "删掉那个",
                        "revision_round": 1,
                        "current_plan": _sample_plan().model_dump(),
                    },
                }
            )
        )
        clarify = [e for e in events if e.get("event") == "revision_clarify"]
        assert clarify and "seg99" in clarify[0]["data"]["problems"][0]


# ── 10.23-B：update 数值型字段类型校验（font_size='larger' 类无效值） ──


class TestUpdateNumericValidation:
    """UPDATE 段数值键必须数值且在像素合理域，否则拒整个指令
    （problems 路径与 bad_color 同款）。"""

    def _apply(self, params):
        plan = _plan(
            _seg("seg1", 0.5, 3.0, "caption_pop", text="开场", font_size=64),
            _seg("seg2", 4.0, 9.0, "full_card", text="卡片"),
            _seg("seg3", 10.0, 14.0, "keyword_icon", text="关键词"),
            _seg("seg4", 19.0, 22.0, "full_card", text="收束"),
        )
        ins = RevisionInstruction(
            action=RevisionAction.UPDATE,
            segment_ids=["seg1"],
            locate_hint="第一段",
            params=params,
        )
        return apply_revision(plan, [ins], 22.0)

    def test_rejects_string_font_size(self):
        patched, problems = self._apply({"font_size": "larger"})
        assert any("数值" in p for p in problems), problems
        # 拒绝 → 原值保留（不是删键）
        assert _by_id(patched, "seg1").params["font_size"] == 64

    def test_accepts_valid_int(self):
        patched, problems = self._apply({"font_size": 88})
        assert _by_id(patched, "seg1").params["font_size"] == 88

    def test_rejects_out_of_range(self):
        patched, problems = self._apply({"font_size": 500})
        assert any("数值" in p for p in problems), problems
        assert _by_id(patched, "seg1").params["font_size"] == 64
