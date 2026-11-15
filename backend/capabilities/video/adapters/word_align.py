"""词级时间戳对齐 — jieba 分词 × whisper 字级时间戳。

设计依据（ADR-002）：
  - ossclip 的 word-timed kinetic captions：字幕动画由词级时间戳驱动
  - whisper（stable-ts/faster-whisper）对中文输出字级时间戳（每字一个 word）
  - jieba 是中文分词事实标准

算法（字符流展开对齐）：
  1. jieba 分词 → 词列表（保留标点附着在词上）
  2. 词列表展开为有效字符流（剥离空白；标点保留位置但单独成"词"）
  3. whisper words 同样展开为有效字符流（剥离空白和纯标点 word）
  4. 两条字符流按位置 zip，词时间戳 = 首字符时间 → 末字符时间
  5. 匹配失败（长度失衡按位置截断）/ words 为空 → 均分降级

降级链：jieba 缺失 → 逐字；words 为空 → 均分；duration=0 → 全 0。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# 有效字符：CJK / 字母 / 数字（对齐流中剥离标点与空白，两边对称）
_VALID_CHAR = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbfA-Za-z0-9]")


@dataclass
class TimedWord:
    """带时间戳的词 — 字幕动画的驱动单元。"""

    text: str
    start: float
    end: float


def _jieba_available() -> bool:
    try:
        import jieba  # noqa: F401
        return True
    except ImportError:
        return False


def split_words(text: str) -> list[str]:
    """中文 jieba 分词；英文按空格。jieba 缺失 → 中日韩逐字降级。"""
    text = text.strip()
    if not text:
        return []

    if _jieba_available():
        import jieba
        return [w for w in jieba.cut(text) if w.strip()]

    # 降级：CJK 逐字，其余按空格
    result: list[str] = []
    buf = ""
    for ch in text:
        if re.match(r"[\u4e00-\u9fff\u3400-\u4dbf]", ch):
            if buf:
                result.append(buf)
                buf = ""
            result.append(ch)
        elif ch.isspace():
            if buf:
                result.append(buf)
                buf = ""
        else:
            buf += ch
    if buf:
        result.append(buf)
    return result


def _expand_words(words: list) -> list[tuple[float, float]]:
    """whisper words → (start, end) 有效字符流（剥离空白与标点，与文本流对称）。"""
    stream: list[tuple[float, float]] = []
    for w in words:
        t = (w.text or "").strip()
        if not t:
            continue
        for ch in t:
            if _VALID_CHAR.match(ch):
                stream.append((w.start, w.end))
    return stream


def _even_fallback(word_texts: list[str], duration: float) -> list[TimedWord]:
    """均分降级：按词数平分 duration。"""
    n = len(word_texts)
    if n == 0:
        return []
    step = duration / n
    return [
        TimedWord(text=t, start=i * step, end=(i + 1) * step)
        for i, t in enumerate(word_texts)
    ]


def align_word_timing(
    text: str,
    words: list,
    duration: float,
) -> list[TimedWord]:
    """jieba 分词结果与 whisper 字级时间戳对齐 → 词级时间戳。

    Args:
        text: 字幕文本
        words: whisper 输出的 TranscribeWord 列表（中文字级/英文词级均可）
        duration: 字幕段总时长（降级均分用）

    Returns:
        TimedWord 列表（text 为分词后的词，start/end 为该词的起止秒）
    """
    word_texts = split_words(text)
    if not word_texts:
        return []

    # 展开为 (词索引, 字符) 流：只保留有效字符（CJK/字母/数字），
    # 标点不占位 — whisper 流同样剥离标点，保证两边位置对齐
    indexed: list[tuple[int, str]] = []
    for i, w in enumerate(word_texts):
        for ch in w:
            if _VALID_CHAR.match(ch):
                indexed.append((i, ch))

    stream = _expand_words(words) if words else []

    if not stream or not indexed:
        return _even_fallback(word_texts, duration)

    # 按字符位置对齐（zip 到较短者；失衡即按位置截断）
    n = min(len(indexed), len(stream))
    if n == 0:
        return _even_fallback(word_texts, duration)

    # 字符数严重不匹配（>30% 偏差）→ 均分降级，避免错误对齐
    if abs(len(indexed) - len(stream)) > max(2, 0.3 * len(indexed)):
        logger.debug(
            "word align mismatch: text_chars=%d word_chars=%d, fallback to even",
            len(indexed), len(stream),
        )
        return _even_fallback(word_texts, duration)

    # 词索引 → [start, end]
    bounds: dict[int, list[float]] = {}
    for pos in range(n):
        word_idx, _ch = indexed[pos]
        start, end = stream[pos]
        if word_idx not in bounds:
            bounds[word_idx] = [start, end]
        else:
            b = bounds[word_idx]
            b[0] = min(b[0], start)
            b[1] = max(b[1], end)

    # 未匹配到的词（截断尾巴）：用相邻词时间补
    last_t = stream[n - 1][1]
    result: list[TimedWord] = []
    prev_end = 0.0
    for i, wt in enumerate(word_texts):
        if i in bounds:
            start, end = bounds[i]
        else:
            start = prev_end
            end = last_t if i == len(word_texts) - 1 else prev_end
        result.append(TimedWord(text=wt, start=start, end=max(end, start)))
        prev_end = result[-1].end

    return result
