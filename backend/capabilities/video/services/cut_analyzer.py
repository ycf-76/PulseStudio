"""Cut analyzer service — detection strategy orchestration.

设计依据：
  - ossclip analyze 阶段：只跑检测，不执行剪辑
  - fillercut 的两级填充词检测（纯语气词 + 指示词类）
  - video-subtitle-cutter 的重复/口误检测
  - 隐患 #3 对策：stable-ts 时间戳误差容忍区间 + 短词降权
  - 隐患 #4 对策：短语级填充词检测

核心功能：
  - 4 种检测策略编排（静音/填充词/重复/LLM语义）
  - 候选片段去重合并
  - 保留片段计算
"""

from __future__ import annotations

import json
import logging
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class CutType(str, Enum):
    """待删片段类型 — 对齐 ossclip marker 颜色编码。"""

    SILENCE = "silence"
    FILLER = "filler"
    REPEAT = "repeat"
    FALSE_START = "false_start"
    FLUFF = "fluff"


class CutCandidate(BaseModel):
    """一个待删减的候选片段。对齐 ossclip marker 格式。"""

    start: float = Field(ge=0, description="开始时间（秒）")
    end: float = Field(ge=0, description="结束时间（秒）")
    cut_type: CutType = Field(description="删减类型")
    confidence: float = Field(ge=0, le=1, description="置信度 0-1，对齐 ossclip conf")
    reason: str = Field(description="原因描述")
    text: str = Field(default="", description="该片段原文（如有）")
    removable: bool = Field(default=True, description="用户可取消勾选")

    def __post_init_post_parse__(self):
        if self.end < self.start:
            raise ValueError(f"CutCandidate end ({self.end}) must be >= start ({self.start})")


class WordTimestamp(BaseModel):
    """逐词时间戳 — 对齐 WhisperX/whisper-cpp 输出格式。"""

    text: str
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    probability: float = Field(default=1.0, ge=0, le=1)


TIMESTAMP_TOLERANCE_MS = 100
MIN_WORD_DURATION_MS = 200

FILLER_WORDS_STRICT: frozenset[str] = frozenset(
    {
        "嗯",
        "啊",
        "呃",
        "额",
        "哦",
        "um",
        "uh",
        "hmm",
        "mm",
    }
)

FILLER_WORDS_CONTEXTUAL: frozenset[str] = frozenset(
    {
        "那个",
        "这个",
        "就是说",
        "怎么说呢",
        "你知道吗",
        "like",
        "you know",
        "basically",
        "actually",
        "right",
    }
)

FILLER_PHRASES: frozenset[str] = frozenset(
    {
        "那个那个",
        "这个这个",
        "然后呢",
        "你知道吗",
        "you know",
        "I mean",
        "like like",
        "basically basically",
    }
)


def _effective_gap(prev_end: float, next_start: float) -> float:
    """计算有效间隔，扣除时间戳误差。

    隐患 #3 对策：给 Whisper 时间戳加 ±100ms 误差容忍区间。
    effective = observed - 2 * tolerance，取保守值减少误删。
    """
    observed_gap = next_start - prev_end
    tolerance_s = TIMESTAMP_TOLERANCE_MS / 1000.0
    effective = observed_gap - 2 * tolerance_s
    return max(0, effective)


def detect_silence_candidates(
    silence_segments: list[tuple[float, float, float]],
) -> list[CutCandidate]:
    """策略 1：静音检测 — 对齐 fillercut + cutlist。

    Args:
        silence_segments: [(start, end, duration), ...] 来自 silence_detect adapter

    Returns:
        静音候选片段列表
    """
    candidates = []
    for start, end, duration in silence_segments:
        # 10.28：候选收缩 — >0.6s 的静音段两端各保留 0.2s 呼吸缓冲
        # （全量删会语速失真像机关枪）；≤0.6s 的短停顿是句间自然节奏，
        # 不出候选
        if duration <= 0.6:
            continue
        s, e = start + 0.2, end - 0.2
        if e - s < 0.15:
            continue
        candidates.append(
            CutCandidate(
                start=s,
                end=e,
                cut_type=CutType.SILENCE,
                confidence=0.95,
                reason=f"silence -{duration:.2f}s",
                text="",
            )
        )
    return candidates


def detect_filler_candidates(
    words: list[WordTimestamp],
    filler_pause: float = 0.35,
) -> list[CutCandidate]:
    """策略 2：两级填充词检测 — 对齐语言学文献 + fillercut。

    第一级：纯语气词（嗯/啊/呃）→ 直接标记（误判率极低）
    第二级：指示词类（那个/这个）→ 需 FILLER_PAUSE 约束
    短语级：连续填充词（"那个那个"）→ 短语匹配

    隐患 #3 对策：短词（<200ms）时间戳不准，降低置信度
    隐患 #4 对策：短语级检测解决连说无停顿问题
    """
    candidates = []

    for i, word in enumerate(words):
        text = word.text.strip().lower()
        duration_ms = (word.end - word.start) * 1000

        if text in FILLER_WORDS_STRICT:
            confidence = 0.95
            if round(duration_ms) < MIN_WORD_DURATION_MS:
                confidence = max(0.3, confidence - 0.3)

            candidates.append(
                CutCandidate(
                    start=word.start,
                    end=word.end,
                    cut_type=CutType.FILLER,
                    confidence=confidence,
                    reason=f"filler '{word.text}'",
                    text=word.text,
                )
            )
            continue

        if text in FILLER_WORDS_CONTEXTUAL:
            prev_gap = _effective_gap(words[i - 1].end, word.start) if i > 0 else 0
            next_gap = _effective_gap(word.end, words[i + 1].start) if i < len(words) - 1 else 0

            if prev_gap >= filler_pause or next_gap >= filler_pause:
                confidence = 0.7
                if round(duration_ms) < MIN_WORD_DURATION_MS:
                    confidence = max(0.3, confidence - 0.3)

                candidates.append(
                    CutCandidate(
                        start=word.start,
                        end=word.end,
                        cut_type=CutType.FILLER,
                        confidence=confidence,
                        reason=f"filler '{word.text}' (contextual)",
                        text=word.text,
                    )
                )

    candidates.extend(_detect_filler_phrases(words))

    return candidates


def _detect_filler_phrases(words: list[WordTimestamp]) -> list[CutCandidate]:
    """短语级填充词检测 — 隐患 #4 对策。

    解决"那个那个"连说时中间没有停顿、第二级检测漏掉的问题。
    中文短语无空格分隔，用滑动窗口拼接文本匹配。
    """
    candidates = []
    for phrase in FILLER_PHRASES:
        phrase_lower = phrase.lower()
        phrase_len = len(phrase_lower)

        for window_size in range(1, min(len(words) + 1, phrase_len + 1)):
            for i in range(len(words) - window_size + 1):
                window_text = "".join(w.text.strip().lower() for w in words[i : i + window_size])
                if window_text == phrase_lower:
                    start = words[i].start
                    end = words[i + window_size - 1].end
                    already_covered = any(c.start <= start and c.end >= end for c in candidates)
                    if not already_covered:
                        candidates.append(
                            CutCandidate(
                                start=start,
                                end=end,
                                cut_type=CutType.FILLER,
                                confidence=0.8,
                                reason=f"filler phrase '{phrase}'",
                                text=phrase,
                            )
                        )
    return candidates


def detect_repeat_candidates(
    words: list[WordTimestamp],
) -> list[CutCandidate]:
    """策略 3：重复/口误检测 — 对齐 video-subtitle-cutter。

    检测：
      - 单词重复（"增长增长"）
      - 短语重复（2-4 词窗口，拼接文本比较）
    """
    candidates = []

    for i in range(len(words) - 1):
        if words[i].text.strip().lower() == words[i + 1].text.strip().lower():
            text = words[i].text
            candidates.append(
                CutCandidate(
                    start=words[i].start,
                    end=words[i + 1].end,
                    cut_type=CutType.REPEAT,
                    confidence=0.85,
                    reason=f"repeat '{text}{text}'",
                    text=text + text,
                )
            )

    for window_size in range(2, min(5, len(words) // 2 + 1)):
        for i in range(len(words) - window_size * 2 + 1):
            phrase_a = "".join(w.text.strip() for w in words[i : i + window_size]).lower()
            phrase_b = "".join(
                w.text.strip() for w in words[i + window_size : i + window_size * 2]
            ).lower()
            if phrase_a == phrase_b and phrase_a.strip():
                candidates.append(
                    CutCandidate(
                        start=words[i].start,
                        end=words[i + window_size * 2 - 1].end,
                        cut_type=CutType.REPEAT,
                        confidence=0.8,
                        reason=f"repeat '{phrase_a} {phrase_b}'",
                        text=phrase_a + " " + phrase_b,
                    )
                )

    return candidates


def merge_candidates(
    candidates: list[CutCandidate],
    total_duration: float,
) -> list[CutCandidate]:
    """合并重叠的候选片段，按 start 排序。

    重叠处理：保留置信度更高的，扩展边界到两个片段的并集。
    """
    if not candidates:
        return []

    sorted_cands = sorted(candidates, key=lambda c: (c.start, -c.confidence))
    merged: list[CutCandidate] = [sorted_cands[0]]

    for cand in sorted_cands[1:]:
        last = merged[-1]
        if cand.start <= last.end:
            if cand.confidence > last.confidence:
                merged[-1] = CutCandidate(
                    start=min(last.start, cand.start),
                    end=max(last.end, cand.end),
                    cut_type=cand.cut_type,
                    confidence=cand.confidence,
                    reason=f"{last.reason}; {cand.reason}",
                    text=f"{last.text} {cand.text}".strip(),
                )
            else:
                merged[-1] = CutCandidate(
                    start=min(last.start, cand.start),
                    end=max(last.end, cand.end),
                    cut_type=last.cut_type,
                    confidence=last.confidence,
                    reason=f"{last.reason}; {cand.reason}",
                    text=f"{last.text} {cand.text}".strip(),
                )
        else:
            merged.append(cand)

    return merged


class LLMFluffResult(BaseModel):
    """LLM 语义分析的单个结果。"""

    index: int = Field(ge=0, description="片段索引")
    verdict: Literal["keep", "cut", "uncertain"] = Field(description="判定结果")
    type: Literal["false_start", "fluff", "connector_filler", "maybe_filler"] = Field(
        default="fluff", description="问题类型"
    )
    reason: str = Field(default="", description="判定原因")


class LLMFluffResponse(BaseModel):
    """LLM 语义分析的完整响应。"""

    results: list[LLMFluffResult] = Field(default_factory=list)


FLUFF_ANALYSIS_PROMPT = """分析以下口播片段，识别应删减的片段。

片段列表：
{segments_json}

识别以下问题：
1. FALSE STARTS: 不完整句子重启（"我觉得—不对，应该是..."）
2. FLUFF: 废话/离题/冗余（与主题无关的闲聊）
3. CONNECTOR FILLERS: 连词/副词用作填充（"所以…就是说…"中的无意义连接）

重要约束：
1. 不要为了缩短时长而删减——只删除真正有问题的地方
2. 保留口播的自然节奏和语气
3. 如果一段话虽然"废话"但有情感铺垫作用，标记为 uncertain 而非 cut
4. 目标删减比例不超过原时长的 30%

只输出 JSON（不要 markdown 代码块）：
[{{"index": 0, "verdict": "keep"|"cut"|"uncertain", "type": "false_start"|"fluff"|"connector_filler"|"maybe_filler", "reason": "..."}}]"""


MAX_LLM_RETRIES = 2


async def detect_fluff_candidates(
    transcript_segments: list[dict[str, Any]],
    llm: Any = None,
) -> tuple[list[CutCandidate], list[dict[str, Any]]]:
    """策略 4：LLM 语义分析 — 对齐 video-subtitle-cutter。

    隐患 #9 对策：outputguard 校验 + 2 次重试 + 降级跳过。
    隐患 #10 对策：prompt 约束防止过度删减。

    Args:
        transcript_segments: [{"index": 0, "start": 0.0, "end": 3.0, "text": "..."}, ...]
        llm: LLM 客户端（None → 跳过）

    Returns:
        (候选片段列表, LLM 原始响应用于调试)
    """
    if not llm or not transcript_segments:
        return [], []

    from app.adapters.llm_base import llm_prompt_to_text

    segments_json = json.dumps(transcript_segments, ensure_ascii=False)
    prompt = FLUFF_ANALYSIS_PROMPT.format(segments_json=segments_json)

    for attempt in range(MAX_LLM_RETRIES + 1):
        try:
            result_text = await llm_prompt_to_text(llm, prompt)

            json_start = result_text.find("[")
            json_end = result_text.rfind("]") + 1
            if json_start < 0 or json_end <= json_start:
                raise ValueError("LLM output contains no JSON array")

            raw_results = json.loads(result_text[json_start:json_end])

            candidates = []
            for item in raw_results:
                verdict = item.get("verdict", "keep")
                if verdict not in ("cut", "uncertain"):
                    continue

                idx = item.get("index", -1)
                if idx < 0 or idx >= len(transcript_segments):
                    continue

                seg = transcript_segments[idx]
                cut_type_map = {
                    "false_start": CutType.FALSE_START,
                    "fluff": CutType.FLUFF,
                    "connector_filler": CutType.FILLER,
                    "maybe_filler": CutType.FILLER,
                }
                cut_type = cut_type_map.get(item.get("type", "fluff"), CutType.FLUFF)

                confidence = 0.6 if verdict == "cut" else 0.4

                candidates.append(
                    CutCandidate(
                        start=seg.get("start", 0),
                        end=seg.get("end", 0),
                        cut_type=cut_type,
                        confidence=confidence,
                        reason=f"llm: {item.get('reason', verdict)}",
                        text=seg.get("text", ""),
                        removable=True,
                    )
                )

            logger.info(
                f"LLM fluff analysis: {len(candidates)} candidates from {len(raw_results)} results"
            )
            return candidates, raw_results

        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(
                f"LLM output format invalid (attempt {attempt + 1}/{MAX_LLM_RETRIES + 1}): {e}"
            )
            prompt = (
                f"上次输出格式有误：{e}\n"
                "请严格按 JSON 数组格式重新输出，不要添加任何额外文字。\n"
                f"原始请求：{prompt}"
            )
        except Exception as e:
            logger.warning(f"LLM fluff analysis failed (attempt {attempt + 1}): {e}")
            break

    logger.warning(f"LLM fluff analysis failed after {MAX_LLM_RETRIES + 1} attempts, skipping")
    return [], []


def compute_kept_segments(
    candidates: list[CutCandidate],
    total_duration: float,
) -> list[tuple[float, float]]:
    """从候选删减片段计算保留片段列表。

    Args:
        candidates: 已确认的待删片段（已合并去重）
        total_duration: 视频总时长

    Returns:
        保留片段列表 [(start, end), ...]，按时间排序
    """
    if not candidates:
        return [(0.0, total_duration)]

    sorted_cands = sorted(candidates, key=lambda c: c.start)
    kept: list[tuple[float, float]] = []
    prev_end = 0.0

    for cand in sorted_cands:
        if cand.start > prev_end:
            kept.append((prev_end, cand.start))
        prev_end = max(prev_end, cand.end)

    if prev_end < total_duration:
        kept.append((prev_end, total_duration))

    return kept
