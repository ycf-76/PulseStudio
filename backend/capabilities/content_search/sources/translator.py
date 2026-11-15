"""免费翻译层：调 MyMemory API，无 key，国内可访问。

端点：https://api.mymemory.translated.net/get
参数：
- q=待翻译文本
- langpair=源语言|目标语言（如 en|zh-CN）

特点：
- 无需 API key
- 免费配额：每天 5000 词（匿名），注册后 50000 词
- 国内可直连，无需翻墙
- 译文质量稳定

缓存策略：
- 按 (text_hash, target_lang) 缓存到内存 dict
- 进程重启缓存失效（MVP 阶段够用，后续可换 SQLite）

注意：MyMemory 单次请求文本长度建议 < 500 字符，超长文本会被截断。
translator 内部会自动截断到 500 字符。
"""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_MYMEMORY_URL = "https://api.mymemory.translated.net/get"
_CACHE: dict[str, str] = {}
# 并发限制：MyMemory 单次往返 ~1s，20 条结果的标题+摘要约 40 次请求，
# 3 并发要排 14 轮（实测 5s 以上）。放到 32 后基本一轮打完（~1s）。
_SEMAPHORE = asyncio.Semaphore(32)
# 单次翻译请求超时（秒）。原来是 15s，一次批量翻译被个别慢请求拖住时
# 会把整个搜索超时预算吃光；MyMemory 正常往返在 1s 内，5s 足够。
_REQUEST_TIMEOUT = 5.0
# 单次翻译文本最大长度（MyMemory 建议值）
_MAX_TEXT_LEN = 500

# 熔断：MyMemory 一旦连不上（SSL 中断 / 超时），后续请求会连续卡在同一个
# 失败路径上。批量翻译时每条文本各等一次超时、又被 _SEMAPHORE 限到 3 并发，
# 10 条结果就能把一次搜索拖到 60s 以上，上层只能判超时。
# 这里记下最早可重试的时间点，熔断期内直接返回原文，不再发请求。
_FAIL_COOLDOWN = 60.0
_down_until: float = 0.0

# 延迟翻译开关（ContextVar，按请求上下文隔离，不影响并发中的其他请求）。
# 多平台综合搜索时，每个源各自翻译 limit 条 → 10 个英文源 × (标题+摘要)
# = 数百次翻译请求，绝大多数结果最后会被排序截断丢掉。
# 打开这个开关后源内部翻译直接返回原文，由调用方在截断之后再翻译最终 N 条。
_DEFER_TRANSLATION: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "defer_translation", default=False
)


def defer_translation(enabled: bool) -> contextvars.Token:
    """开关延迟翻译（返回 token，配合 reset_defer_translation 恢复）。"""
    return _DEFER_TRANSLATION.set(enabled)


def reset_defer_translation(token: contextvars.Token) -> None:
    """恢复上一次的延迟翻译开关。"""
    _DEFER_TRANSLATION.reset(token)


def is_deferred() -> bool:
    """当前上下文是否处于延迟翻译状态。"""
    return _DEFER_TRANSLATION.get()


def _hash_key(text: str, target_lang: str) -> str:
    """生成缓存 key。"""
    h = hashlib.md5(text.encode("utf-8")).hexdigest()
    return f"{h}:{target_lang}"


def _detect_source_lang(text: str) -> str:
    """简单源语言检测：含中文字符返回 zh，否则返回 en。

    MyMemory 要求显式指定源语言（不支持 auto），这里用简单启发式。
    HackerNews 内容基本都是英文，默认 en。
    """
    for ch in text:
        if "\u4e00" <= ch <= "\u9fff":
            return "zh"
    return "en"


async def translate_text(
    text: str,
    target_lang: str = "zh-CN",
    source_lang: str = "",
    client: httpx.AsyncClient | None = None,
) -> str:
    """翻译单条文本。

    Args:
        text: 待翻译文本（空字符串或纯数字直接返回原文）
        target_lang: 目标语言代码（默认 zh-CN）
        source_lang: 源语言（空则自动检测；MyMemory 不支持 auto，需显式指定）
        client: 复用 httpx.AsyncClient（不传则临时创建）

    Returns:
        译文。翻译失败时返回原文（降级，不抛异常）。
    """
    if not text or not text.strip():
        return text
    global _down_until
    # 纯数字/标点/URL 不翻译
    stripped = text.strip()
    if all(c.isdigit() or c in ".,;:!?-_/ " for c in stripped):
        return text
    # URL 不翻译
    if stripped.startswith(("http://", "https://")):
        return text

    # 截断超长文本
    if len(stripped) > _MAX_TEXT_LEN:
        stripped = stripped[:_MAX_TEXT_LEN]

    # 源语言检测
    if not source_lang:
        source_lang = _detect_source_lang(stripped)
    # 目标语言对 MyMemory 用 zh-CN → zh-CN
    tl = "zh-CN" if target_lang in ("zh-CN", "zh", "zh_CN") else target_lang

    # 已经是目标语言 → 直接返回，不发请求。
    # 中文源（sogou / tavily_site / hotboard）结果本来就是中文，
    # 之前无条件翻译会为每条标题和摘要各发一次注定无意义的请求。
    if source_lang == "zh" and tl == "zh-CN":
        return text

    # 查缓存
    cache_key = _hash_key(text, target_lang)
    if cache_key in _CACHE:
        return _CACHE[cache_key]

    # 熔断期内不发请求，直接返回原文
    if time.monotonic() < _down_until:
        return text

    own_client = client is None
    if own_client:
        from capabilities.content_search.sources.base import (
            create_async_client,
            get_proxy_url,
        )
        client = await create_async_client(
            timeout=httpx.Timeout(_REQUEST_TIMEOUT),
            headers={"User-Agent": "multi-agent-xhs-platform/1.0"},
            proxy=get_proxy_url() or None,
            trust_env=False,
        )

    try:
        async with _SEMAPHORE:
            params = {
                "q": stripped,
                "langpair": f"{source_lang}|{tl}",
            }
            resp = await client.get(
                _MYMEMORY_URL,
                params=params,
                timeout=httpx.Timeout(_REQUEST_TIMEOUT),
            )
            resp.raise_for_status()
            data: Any = resp.json()

        # MyMemory 返回格式：{"responseData": {"translatedText": "译文", ...}, ...}
        if isinstance(data, dict):
            response_data = data.get("responseData") or {}
            translated = (response_data.get("translatedText") or "").strip()
            # MyMemory 配额耗尽或异常时会返回 WARNING 或 QUOTA EXCEEDED 文本
            if translated and "QUOTA" not in translated.upper() and "WARNING" not in translated.upper():
                _CACHE[cache_key] = translated
                return translated
        # 解析失败，返回原文
        return text
    except Exception as e:
        _down_until = time.monotonic() + _FAIL_COOLDOWN
        logger.warning(
            f"[translate] MyMemory 不可用，{int(_FAIL_COOLDOWN)}s 内跳过翻译"
            f"（返回原文）: {type(e).__name__}: {e}"
        )
        return text
    finally:
        if own_client and client is not None:
            await client.aclose()


async def translate_batch(
    texts: list[str],
    target_lang: str = "zh-CN",
    client: httpx.AsyncClient | None = None,
) -> list[str]:
    """批量翻译（并发 + 缓存 + 限流 + 去重）。

    去重是必要的：搜索结果里 summary 常常就是 title 本身（或 title 的前缀），
    逐条翻译会为同一段文本重复发请求。这里先按文本去重再并发翻译，
    最后按原顺序回填，请求数最多减半。

    Args:
        texts: 待翻译文本列表
        target_lang: 目标语言
        client: 复用 httpx.AsyncClient

    Returns:
        译文列表（顺序与输入一致）
    """
    if not texts:
        return []
    if _DEFER_TRANSLATION.get():
        # 延迟翻译：源内部翻译整体跳过，由调用方在结果截断后统一翻译
        return list(texts)
    unique = {t for t in texts if t}
    if not unique:
        return list(texts)
    translated = await asyncio.gather(
        *[translate_text(t, target_lang=target_lang, client=client) for t in unique]
    )
    mapping = dict(zip(unique, translated))
    return [mapping.get(t, t) for t in texts]


def clear_cache() -> None:
    """清空翻译缓存（测试用）。"""
    _CACHE.clear()