"""转写同音字纠错 pass 测试（P2 验收回灌：同音错字上屏）。

P2 验收实证：whisper 把普通话口播转出同音/近音错字（「入夏」→「入虾」、
「空调直吹」→「执动」），场景字幕直接引用转写文本导致错字上屏。

分层测试：
  - apply_replacements：纯函数，wrong→right 对齐回词级 token（时间戳不动）
  - fix_homophones：LLM 调用 + 健壮性（失败/非 JSON → 原样返回 + warning）
集成场景（studio_agent.transcribe 工具结果带 corrections）在 test_studio_agent.py。
"""

import json

from capabilities.video.adapters.whisper_transcribe import (
    TranscribeSegment,
    TranscribeWord,
)


def _seg(words, start=0.0):
    """[(text, start, end), ...] → 单 segment（segment.text = token 拼接）。"""
    ws = [TranscribeWord(text=t, start=s, end=e) for t, s, e in words]
    text = "".join(w.text for w in ws)
    if ws:
        return TranscribeSegment(text=text, start=ws[0].start,
                                 end=ws[-1].end, words=ws)
    return TranscribeSegment(text=text, start=start, end=start, words=[])


class FakeLLM:
    """单次固定回复；记录调用数。reply 为 Exception 实例时抛出。"""

    def __init__(self, reply):
        self.reply = reply
        self.calls = 0

    async def chat(self, messages, response_format=None):
        self.calls += 1
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


# ── 纯函数：apply_replacements（对齐分摊） ────────────────────────────


def test_equal_length_maps_charwise_and_keeps_timestamps():
    """等长替换逐字映射：token 文本改、时间戳/segment.text 同步、原对象不动。"""
    from capabilities.video.adapters.studio_transcript_fix import apply_replacements

    seg = _seg([("入", 0.0, 1.0), ("虾", 1.0, 2.0), ("之", 2.0, 3.0), ("后", 3.0, 4.0)])
    out, corr, warns = apply_replacements([seg], [{"wrong": "入虾", "right": "入夏"}])
    assert corr == ["入虾→入夏"] and warns == []
    assert [w.text for w in out[0].words] == ["入", "夏", "之", "后"]
    assert [(w.start, w.end) for w in out[0].words] == \
        [(0.0, 1.0), (1.0, 2.0), (2.0, 3.0), (3.0, 4.0)]
    assert out[0].text == "入夏之后"  # segment.text 同步修正
    assert seg.text == "入虾之后" and seg.words[1].text == "虾"  # frozen 不被改动


def test_repeated_wrong_fixed_at_every_occurrence():
    """同一错字多处出现：逐条列出后每处都被修正（find 自动后移）。"""
    from capabilities.video.adapters.studio_transcript_fix import apply_replacements

    seg = _seg([("入", 0.0, 1.0), ("虾", 1.0, 2.0), ("又", 2.0, 3.0),
                ("入", 3.0, 4.0), ("虾", 4.0, 5.0)])
    out, corr, warns = apply_replacements(
        [seg], [{"wrong": "入虾", "right": "入夏"}, {"wrong": "入虾", "right": "入夏"}])
    assert corr == ["入虾→入夏", "入虾→入夏"] and warns == []
    assert [w.text for w in out[0].words] == ["入", "夏", "又", "入", "夏"]
    assert [(w.start, w.end) for w in out[0].words] == \
        [(0.0, 1.0), (1.0, 2.0), (2.0, 3.0), (3.0, 4.0), (4.0, 5.0)]
    assert out[0].text == "入夏又入夏"


def test_cross_token_span_maps_each_token():
    """wrong 覆盖 3 个 token：逐字分摊回各 token，时间戳不动。"""
    from capabilities.video.adapters.studio_transcript_fix import apply_replacements

    seg = _seg([("今", 0.0, 1.0), ("入", 1.0, 2.0), ("虾", 2.0, 3.0), ("气", 3.0, 4.0)])
    out, corr, warns = apply_replacements(
        [seg], [{"wrong": "入虾气", "right": "入夏起"}])
    assert corr == ["入虾气→入夏起"] and warns == []
    assert [w.text for w in out[0].words] == ["今", "入", "夏", "起"]
    assert [(w.start, w.end) for w in out[0].words] == \
        [(0.0, 1.0), (1.0, 2.0), (2.0, 3.0), (3.0, 4.0)]
    assert out[0].text == "今入夏起"


def test_unequal_length_splits_proportionally():
    """不等长替换按字符比例分摊：token 不丢、拼接 == right、时间戳不动。"""
    from capabilities.video.adapters.studio_transcript_fix import apply_replacements

    seg = _seg([("虾", 0.0, 1.0), ("之", 1.0, 2.0), ("后", 2.0, 3.0)])
    out, corr, warns = apply_replacements(
        [seg], [{"wrong": "虾之后", "right": "夏之后吧"}])
    assert corr == ["虾之后→夏之后吧"] and warns == []
    assert len(out[0].words) == 3  # token 不丢
    assert "".join(w.text for w in out[0].words) == "夏之后吧"
    assert [(w.start, w.end) for w in out[0].words] == \
        [(0.0, 1.0), (1.0, 2.0), (2.0, 3.0)]  # 时间戳一个不动


def test_unmatched_replacement_ignored_with_warning():
    """找不到匹配（如 LLM 幻觉词）→ 忽略 + warning，segments 原样。"""
    from capabilities.video.adapters.studio_transcript_fix import apply_replacements

    seg = _seg([("入", 0.0, 1.0), ("虾", 1.0, 2.0)])
    out, corr, warns = apply_replacements(
        [seg], [{"wrong": "不存在的词", "right": "无所谓"}])
    assert corr == [] and warns and "未命中" in warns[0]
    assert [w.text for w in out[0].words] == ["入", "虾"]


def test_cross_segment_replacement_ignored_with_warning():
    """跨 segment 的替换忽略（对齐只做 segment 内），记录 warning。"""
    from capabilities.video.adapters.studio_transcript_fix import apply_replacements

    s1 = _seg([("入", 0.0, 1.0), ("虾", 1.0, 2.0)])
    s2 = _seg([("之", 2.0, 3.0), ("后", 3.0, 4.0)])
    out, corr, warns = apply_replacements(
        [s1, s2], [{"wrong": "入虾之后", "right": "入夏之后"}])
    assert corr == [] and warns and "跨" in warns[0]
    assert out[0].text == "入虾" and out[1].text == "之后"


def test_empty_replacements_returns_asis():
    """无错字（空数组）→ 原样返回。"""
    from capabilities.video.adapters.studio_transcript_fix import apply_replacements

    seg = _seg([("入", 0.0, 1.0), ("虾", 1.0, 2.0)])
    out, corr, warns = apply_replacements([seg], [])
    assert out == [seg] and corr == [] and warns == []


def test_malformed_replacement_defensive():
    """LLM 输出的畸形条目（非 dict / 缺字段 / 空串）逐条 warning 跳过，不崩。"""
    from capabilities.video.adapters.studio_transcript_fix import apply_replacements

    seg = _seg([("入", 0.0, 1.0)])
    out, corr, warns = apply_replacements(
        [seg], ["not-a-dict", {}, {"wrong": "", "right": "x"}])
    assert corr == [] and len(warns) == 3
    assert [w.text for w in out[0].words] == ["入"]


# ── fix_homophones：LLM 调用与健壮性 ──────────────────────────────────


async def test_fix_applies_llm_replacements_and_emits():
    """LLM 返回 replacements → 纠错生效；emit 通知带 tool 字段（供循环事件断言）。"""
    from capabilities.video.adapters.studio_transcript_fix import fix_homophones

    seg = _seg([("入", 0.0, 1.0), ("虾", 1.0, 2.0), ("之", 2.0, 3.0)])
    llm = FakeLLM(json.dumps(
        {"replacements": [{"wrong": "入虾", "right": "入夏"}]}, ensure_ascii=False))
    events = []
    out, corr, warns = await fix_homophones(
        [seg], llm, lambda phase, payload: events.append((phase, payload)))
    assert llm.calls == 1
    assert corr == ["入虾→入夏"] and warns == []
    assert [w.text for w in out[0].words] == ["入", "夏", "之"]
    assert events and events[0][0] == "transcript_fix"
    assert events[0][1]["tool"] == "transcribe"
    assert events[0][1]["corrections"] == ["入虾→入夏"]


async def test_llm_failure_returns_original_with_warning():
    """LLM 调用异常 → 原转写直接返回 + warning，不阻塞（尽力而为的 pass）。"""
    from capabilities.video.adapters.studio_transcript_fix import fix_homophones

    seg = _seg([("入", 0.0, 1.0), ("虾", 1.0, 2.0)])
    llm = FakeLLM(RuntimeError("connection reset"))
    out, corr, warns = await fix_homophones([seg], llm, None)
    assert out == [seg] and corr == [] and warns and "纠错" in warns[0]


async def test_non_json_output_returns_original_with_warning():
    """LLM 输出非 JSON → 原样返回 + warning。"""
    from capabilities.video.adapters.studio_transcript_fix import fix_homophones

    seg = _seg([("入", 0.0, 1.0)])
    llm = FakeLLM("这不是 JSON")
    out, corr, warns = await fix_homophones([seg], llm, None)
    assert out == [seg] and corr == [] and warns


async def test_llm_dict_envelope_content_supported():
    """dict 信封（真适配层如 DeepSeekAdapter 返回 {"content": str}）兼容。"""
    from capabilities.video.adapters.studio_transcript_fix import fix_homophones

    seg = _seg([("虾", 0.0, 1.0)])
    llm = FakeLLM({"content": json.dumps({"replacements": []})})
    out, corr, warns = await fix_homophones([seg], llm, None)
    assert out == [seg] and corr == [] and warns == []


async def test_no_words_skips_llm_entirely():
    """无词级 token（无法对齐）→ 不调 LLM，原样返回（现有测试脚本零影响）。"""
    from capabilities.video.adapters.studio_transcript_fix import fix_homophones

    seg = TranscribeSegment(text="你好世界", start=0.0, end=1.0, words=[])
    llm = FakeLLM('{"replacements": []}')
    out, corr, warns = await fix_homophones([seg], llm, None)
    assert llm.calls == 0 and out == [seg] and corr == [] and warns == []
