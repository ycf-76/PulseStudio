"""词级时间戳对齐测试 — TDD 先行。

覆盖（对齐验收标准 A1：中文逐词字幕正常，误差 ≤0.1s）：
  - jieba 分词 + 字级时间戳 → 词级时间戳
  - 标点/空白剥离后位置对齐
  - 降级链：words 为空 → 均分；jieba 缺失 → 逐字
  - 时间戳单调性
"""

from __future__ import annotations

import pytest

from capabilities.video.adapters.word_align import (
    TimedWord,
    align_word_timing,
    split_words,
)


def _chars(text: str, start: float, step: float):
    """构造字级时间戳（模拟 stable-ts 中文输出：每字一个 word）。"""
    from capabilities.video.adapters.whisper_transcribe import TranscribeWord

    return [
        TranscribeWord(text=c, start=start + i * step, end=start + (i + 1) * step, probability=1.0)
        for i, c in enumerate(text)
        if not c.isspace()
    ]


# ── 1. split_words ────────────────────────────────────────


class TestSplitWords:
    def test_中文分词_返回多词(self):
        words = split_words("今天天气真不错")
        assert len(words) >= 2  # 不再是整句一个"词"
        assert "".join(words) == "今天天气真不错"

    def test_英文_按空格分(self):
        assert split_words("hello world foo") == ["hello", "world", "foo"]

    def test_空字符串(self):
        assert split_words("") == []

    def test_词不含空白(self):
        for w in split_words("你好 世界  再见"):
            assert w.strip() == w


# ── 2. align_word_timing：字级时间戳 → 词级 ───────────────


class TestAlignWordTiming:
    def test_中文字级_拼接为词级(self):
        """场景: whisper 输出字级时间戳，jieba 分出多个词
        预期: 每个词的 start/end 来自其首末字符
        """
        text = "今天天气真不错"
        chars = _chars(text, start=10.0, step=0.2)
        result = align_word_timing(text, chars, duration=2.0)

        assert len(result) >= 2
        assert "".join(w.text for w in result) == "今天天气真不错"
        # 首词 start = 第一个字符的 start
        assert result[0].start == pytest.approx(10.0)
        # 末词 end = 最后一个字符的 end
        assert result[-1].end == pytest.approx(10.0 + 7 * 0.2)
        # 词时间单调不减
        for a, b in zip(result, result[1:]):
            assert a.start <= b.start
            assert a.end <= b.end

    def test_标点剥离_位置仍对齐(self):
        """场景: text 带标点，whisper 输出无标点
        预期: 剥离标点后字符流对齐，词时间不漂移
        """
        text = "增长200%，非常惊人！"
        chars = _chars("增长200非常惊人", start=0.0, step=0.1)
        result = align_word_timing(text, chars, duration=2.0)

        assert (
            "".join(w.text for w in result).replace("，", "").replace("！", "")
            == "增长200%非常惊人"
        )
        assert result[0].start == pytest.approx(0.0, abs=0.05)

    def test_词级whisper输出_直接映射(self):
        """场景: whisper 直接输出词级（英文/部分引擎）
        预期: 词时间戳直接取自对应 word
        """
        from capabilities.video.adapters.whisper_transcribe import TranscribeWord

        words = [
            TranscribeWord(text="hello", start=1.0, end=1.5, probability=1.0),
            TranscribeWord(text="world", start=1.5, end=2.2, probability=1.0),
        ]
        result = align_word_timing("hello world", words, duration=2.0)
        assert [w.text for w in result] == ["hello", "world"]
        assert result[0].start == pytest.approx(1.0)
        assert result[1].end == pytest.approx(2.2)

    def test_空时间戳_均分降级(self):
        """场景: 无词级时间戳
        预期: 按词数均分 duration
        """
        result = align_word_timing("今天天气真不错", [], duration=3.0)
        assert len(result) >= 2
        assert result[0].start == pytest.approx(0.0)
        assert result[-1].end == pytest.approx(3.0)
        # 相邻词首尾相接
        for a, b in zip(result, result[1:]):
            assert a.end == pytest.approx(b.start)

    def test_时长为零_不崩溃(self):
        result = align_word_timing("你好", _chars("你好", 0.0, 0.1), duration=0.0)
        assert len(result) >= 1
        assert result[0].start >= 0.0


# ── 3. TimedWord 模型 ─────────────────────────────────────


class TestTimedWord:
    def test_dataclass_fields(self):
        w = TimedWord(text="测试", start=0.5, end=0.9)
        assert (w.text, w.start, w.end) == ("测试", 0.5, 0.9)
