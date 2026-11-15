"""转写同音字纠错 pass — P2 验收回灌（whisper 同音错字上屏）。

P2 验收实证：whisper 把普通话口播转出同音/近音错字（「入夏」→「入虾」、
「空调直吹」→「执动」），场景字幕直接引用转写文本导致错字上屏。本 pass
内置于 studio_agent 的 transcribe 工具（speech.json 落盘前执行，对 agent
透明，不加新工具、不改协议）：

  LLM 校对员给出 wrong→right 列表 → 代码侧对齐回词级 token：
  把全部 token 文本拼成全文（记录每字符所属 token），wrong 的首次出现
  区间内的字符替换为 right（按各 token 在区间内的字符数占比分摊，等长
  即逐字映射），token 一个不丢、start/end 时间戳一个不动。

健壮性：LLM 失败/超时/输出非 JSON → 原转写直接返回 + warning，不阻塞
transcribe 工具（纠错是尽力而为的 pass，不是关卡）。
风格中立：校对 prompt 不含任何打法/风格内容。
"""

from __future__ import annotations

import dataclasses
import json
import logging

logger = logging.getLogger(__name__)

# 校对员 system prompt（裁决给定语义：只修同音/近音错字，不改句意标点）
_PROOFREAD_SYSTEM_PROMPT = (
    "你是字幕校对员。输入是语音识别（ASR）的转写全文，其中可能混入同音字、"
    "近音字或明显识别错误的字词。只修正这类错字：不改变句意、尽量不增删字数、"
    '保留原有标点。输出一个 JSON 对象：{"replacements": '
    '[{"wrong": "入虾", "right": "入夏"}]}，wrong 必须是原文中连续出现的'
    '字词片段，right 是修正后的文本；同一错字在文中多处出现时，每一处都'
    '单独列一条（逐处列出，不要合并）；没有错字时输出 {"replacements": []}。'
    "只输出 JSON。"
)


def _concat_words(segments) -> tuple[str, list[int]]:
    """全部词级 token 拼接为全文 → (full_text, char2tok)。

    char2tok[i] = 全文第 i 个字符所属的全局 token 序号。
    """
    texts: list[str] = []
    char2tok: list[int] = []
    g = 0
    for seg in segments:
        for w in seg.words:
            texts.append(w.text)
            char2tok.extend([g] * len(w.text))
            g += 1
    return "".join(texts), char2tok


def _alloc_counts(lens: list[int], m: int) -> list[int]:
    """把 m 个字符按 lens 占比分摊给各 token（最大余数法，总和恒等于 m）。

    等长（m == sum(lens)）时退化为逐字映射；分摊结果允许某 token 得 0 字符
    （token 不丢、时间戳不动，仅文本长度变化）。
    """
    if not lens:
        return []
    if m == sum(lens):
        return list(lens)
    total = sum(lens)
    if total == 0:  # 防御：区间内全是空 token，均分
        base, rem = divmod(m, len(lens))
        counts = [base] * len(lens)
        for i in range(rem):
            counts[i] += 1
        return counts
    raw = [m * L / total for L in lens]
    counts = [int(x) for x in raw]
    rem = m - sum(counts)
    order = sorted(range(len(lens)), key=lambda i: (-(raw[i] - int(raw[i])), i))
    for i in order[:max(0, rem)]:
        counts[i] += 1
    return counts


def apply_replacements(segments, replacements):
    """把 replacements 对齐回词级 token（纯函数；frozen dataclass 重建）。

    返回 (new_segments, corrections, warnings)：
      - corrections: 实际生效的替换，形如 ["入虾→入夏"]（供 transcribe
        工具结果透出，agent 可见纠了什么）
      - warnings: 被忽略的替换及原因

    对齐规则：wrong 在全文的首次出现区间内，token 的被覆盖字符替换为
    right 的按占比分摊切片，token 中区间外的字符（前缀/后缀）保留；
    替换只作用于单个 segment 内（跨 segment 忽略）；segment.text 含
    wrong 时同步 replace（仅首次）。
    """
    corrections: list[str] = []
    warnings: list[str] = []
    out = list(segments)
    for rep in replacements or []:
        if not isinstance(rep, dict):
            warnings.append(f"替换条目非对象（跳过）：{rep!r}")
            continue
        wrong = rep.get("wrong")
        right = rep.get("right")
        if not (isinstance(wrong, str) and wrong) or \
                not (isinstance(right, str) and right):
            warnings.append(f"替换条目 wrong/right 非空字符串（跳过）：{rep!r}")
            continue

        full, char2tok = _concat_words(out)
        idx = full.find(wrong)
        if idx < 0:
            warnings.append(f"替换未命中（跳过）：{wrong}→{right}")
            continue
        n, m = len(wrong), len(right)
        hi = idx + n

        # 每个 token 的旧全文字符区间
        tok_spans: list[tuple[int, int]] = []
        pos = 0
        for seg in out:
            for w in seg.words:
                tok_spans.append((pos, pos + len(w.text)))
                pos += len(w.text)

        span_tok_ids = sorted(set(char2tok[idx:hi]))
        # token 所属 segment（跨 segment 的替换太脆弱，忽略）
        seg_of: dict[int, int] = {}
        g = 0
        for si, seg in enumerate(out):
            for _ in seg.words:
                seg_of[g] = si
                g += 1
        seg_ids = {seg_of[t] for t in span_tok_ids}
        if len(seg_ids) > 1:
            warnings.append(f"替换跨 segment（跳过）：{wrong}→{right}")
            continue
        si = seg_ids.pop()

        # right 字符按各 token 在区间内的字符数占比分摊
        lens = [max(0, min(tok_spans[t][1], hi) - max(tok_spans[t][0], idx))
                for t in span_tok_ids]
        counts = _alloc_counts(lens, m)
        assigned: dict[int, str] = {}
        p = 0
        for t, c in zip(span_tok_ids, counts):
            assigned[t] = right[p:p + c]
            p += c

        # 重建该 segment：区间外字符保留 + 分摊切片（时间戳不动）
        seg = out[si]
        g0 = sum(len(s.words) for s in out[:si])
        new_words = []
        for k, w in enumerate(seg.words):
            t = g0 + k
            if t not in assigned:
                new_words.append(w)
                continue
            cs, ce = tok_spans[t]
            new_text = full[cs:max(cs, idx)] + assigned[t] + full[min(ce, hi):ce]
            new_words.append(dataclasses.replace(w, text=new_text))
        new_seg = dataclasses.replace(seg, words=new_words)
        if wrong in new_seg.text:  # segment.text 同步（文本与 words 通常一致）
            new_seg = dataclasses.replace(
                new_seg, text=new_seg.text.replace(wrong, right, 1))
        out[si] = new_seg
        corrections.append(f"{wrong}→{right}")
    return out, corrections, warnings


async def _llm_replacements(llm, full_text: str) -> list:
    """调 LLM 校对 → replacements 列表；任何异常/畸形输出都抛出。"""
    resp = await llm.chat(
        [{"role": "system", "content": _PROOFREAD_SYSTEM_PROMPT},
         {"role": "user", "content": f"转写全文：\n{full_text}"}],
        response_format={"type": "json_object"})
    content = resp if isinstance(resp, str) else \
        (resp.get("content") if isinstance(resp, dict) else None)
    if not isinstance(content, str):
        raise ValueError(f"LLM 返回非文本: {type(resp).__name__}")
    obj = json.loads(content)
    reps = obj.get("replacements") if isinstance(obj, dict) else None
    if not isinstance(reps, list):
        raise ValueError("LLM 输出缺少 replacements 数组")
    return reps


async def fix_homophones(segments, llm, emit=None):
    """转写同音字纠错 pass（尽力而为）。

    返回 (segments, corrections, warnings)。LLM 失败/非 JSON → 原转写
    直接返回 + warning；无词级 token（无法对齐）→ 不调 LLM 原样返回。
    emit 仅在实际发生纠错/忽略时通知一次（phase=transcript_fix，
    payload 带 tool 字段，兼容循环事件统一断言）。
    """
    full, _ = _concat_words(segments)
    if not full.strip():
        return segments, [], []
    try:
        reps = await _llm_replacements(llm, full)
    except Exception as exc:
        warn = f"同音字纠错跳过（LLM 调用/解析失败）: {exc}"
        logger.warning(warn)
        return segments, [], [warn]
    new_segs, corrections, warnings = apply_replacements(segments, reps)
    if corrections or warnings:
        logger.info("transcript fix: %s (warnings=%d)", corrections, len(warnings))
        if emit is not None:
            try:
                emit("transcript_fix", {"tool": "transcribe",
                                        "corrections": corrections,
                                        "warnings": warnings})
            except Exception:
                logger.debug("transcript_fix emit failed", exc_info=True)
    return new_segs, corrections, warnings
