"""Trending search skill（多平台版本）。

替代 XhsSearchSkill，支持从多个平台（Reddit / HackerNews / 小红书）搜索热门内容。
返回统一的 TrendingContent 数据结构，下游 analyze 节点不关心数据来自哪个平台。

设计：
- 通过 SourceManager 获取 ContentSource
- 默认平台由 settings.default_source_platform 决定
- LLM 可以通过 platform 参数指定平台（如 "reddit" / "hackernews"）
- 过滤逻辑：按互动量（likes + comments）过滤低热度内容
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from app.core.skill_base import Skill
from app.core.skill_registry import register
from app.engine.schemas import Permission

if TYPE_CHECKING:
    from app.adapters.llm_base import LLMProtocol

logger = logging.getLogger(__name__)

# 关键词里出现 URL 时，关键词搜索没有意义——用户要的是该页面本身的内容。
# 这里只做「这是不是一个链接」的识别，不判断平台（平台正则不参与准入决策）。
_URL_RE = re.compile(r"https?://[^\s<>\"'）)】]+", re.IGNORECASE)

# ===== 多平台并发调度参数 =====
# 全局并发数：24 个源按 6 并发排成 4 波，每波只要有一个慢源就要吃满一轮超时，
# 实测 70s；放开到 16 后绝大多数源能在第一波内启动。
_MAX_CONCURRENT = 16
# 单源超时：超出即视为该源本轮无结果，不拖累整体。
_PER_PLATFORM_TIMEOUT = 10.0
# 整体预算：到点直接收下已完成的结果，取消未完成的源（部分结果好过一直等）。
# 共享 SSL 上下文后正常一轮 8s 左右跑完，12s 只是兜底上限。
_TOTAL_BUDGET = 12.0
# 多平台模式下每个源最多取的条数。24 个源 × 10 条 = 240 候选，
# 最终只留 limit 条，足够排序；而每个源取满 20 条会让上游（尤其 Tavily
# 带 include_images）多跑近一倍时间，是尾部延迟的主要来源。
_PER_SOURCE_LIMIT_CAP = 10
# 单平台搜索（用户只选了一个源）的超时，避免单个源卡死把整个请求挂住。
_SINGLE_PLATFORM_TIMEOUT = 20.0


def _extract_url(text: str) -> str:
    """从输入里抽出第一个 URL；没有则返回空串。"""
    m = _URL_RE.search(text or "")
    return m.group(0).rstrip(".,;，。；") if m else ""


class TrendingSearchInput(BaseModel):
    """Input for trending search skill."""

    keyword: str = Field(
        ...,
        description=(
            "搜索关键词 / 主题关键词。若用户直接给了链接（http/https），"
            "原样传入即可，会走链接采集而不是关键词搜索。"
        ),
    )
    limit: int = Field(default=20, ge=1, le=100, description="返回条数上限")
    platform: str = Field(
        default="",
        description=(
            "搜索数据源（空 = 综合搜索）：reddit / hackernews / xiaohongshu 等。"
            "仅对关键词搜索生效；keyword 是链接时忽略此字段。"
        ),
    )
    time_range: str = Field(
        default="week",
        description="时间范围：day / week / month / year / all",
    )
    min_interactions: int = Field(
        default=10,
        ge=0,
        description="最低互动量阈值（likes + comments），过滤低热度内容",
    )


@register
class TrendingSearchSkill(Skill):
    """多平台热门内容搜索 skill。

    通过 SourceManager 分发到对应平台的 ContentSource。
    自动过滤低互动量内容，按互动量降序排序。
    """

    node_type = "search"
    name = "trending_search"
    description = (
        "跨平台热点搜索。仅在用户明确要求搜索热点/趋势/竞品时调用。"
        "不要在以下场景调用：用户只要求分析已有内容、基于已有内容写文案、"
        "追问/确认/闲聊、用户已提供参考素材。"
        "输入 keyword 返回热门笔记列表和趋势数据。"
        "若 keyword 里带 http/https 链接（用户给了具体网页），会直接采集该页面正文并"
        "作为单条结果返回，不受平台限制——任何公开网页都能采。"
    )
    input_schema = TrendingSearchInput
    required_permissions = [Permission.XHS_SEARCH]  # 复用搜索权限

    def __init__(self, llm: LLMProtocol | None = None) -> None:
        super().__init__()
        self.llm = llm

    async def execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """执行多平台综合搜索。

        流程：
        1. platform 参数为空时，并发搜索所有已注册平台（综合返回，标注来源）
           platform 参数非空时，只搜指定平台
        2. 合并结果，过滤低互动量，按互动量降序排序
        3. 截断到 limit
        4. 空结果 fallback：对默认平台调 get_trending（热门榜兜底），让工作流能继续跑
           disable_fallback=True 时跳过此步（供上层 graph.py 自行控制兜底时机）
        """
        in_ = TrendingSearchInput.model_validate(inputs)
        disable_fallback = inputs.get("disable_fallback", False)

        # 输入是链接 → 直接采集该页面，不拿链接串去搜索引擎里搜。
        # 这是「搜索」入口不再受平台正则限制的关键：任何公开网页都能进。
        url = _extract_url(in_.keyword)
        if url:
            return await self._collect_url_as_result(url, in_.platform)

        # platform 为空 → 多平台综合搜索；非空 → 单平台搜索
        # 多平台只按 limit 取：24 个源各取 2 倍（960 条）最后只留 limit 条，
        # 多出来的 95% 会被丢弃，却要陪着抓封面图、逐条翻译，白白拉长耗时。
        # 单平台仍取 2 倍，因为只有一个源，需要更多候选来过滤。
        if in_.platform:
            raw_results, searched_platforms = await self._search_single(
                in_.platform, in_.keyword, in_.limit * 2, in_.time_range
            )
        else:
            # 多平台搜索期间关掉「源内翻译」，改由截断后统一翻译最终 N 条
            from capabilities.content_search.sources.translator import (
                defer_translation,
                reset_defer_translation,
            )

            _tok = defer_translation(True)
            try:
                raw_results, searched_platforms = await self._search_all_platforms(
                    in_.keyword, in_.limit, in_.time_range
                )
            finally:
                reset_defer_translation(_tok)

        # 转成 dict 并计算互动量
        items: list[dict[str, Any]] = []
        for content in raw_results:
            d = content.to_dict()
            d["interactions"] = d["likes"] + d["comments"] + d["shares"]
            items.append(d)

        # 过滤低互动量
        before = len(items)
        items = [it for it in items if it["interactions"] >= in_.min_interactions]
        dropped_low = before - len(items)

        # 按互动量降序排序
        items.sort(key=lambda x: x["interactions"], reverse=True)
        final_results = items[:in_.limit]

        # ===== 空结果 fallback：热门榜兜底 =====
        # 所有关键词搜索都 0 结果时，对【用户指定的平台】调 get_trending，
        # 让工作流能继续跑（analyze/image_gen 有素材可用）
        # 红线：只 fallback 到用户指定的平台，绝不偷偷切到默认平台（小红书）。
        # disable_fallback=True 时跳过（供上层 graph.py 先做翻译重试，再自行兜底）
        fallback_used = False
        if not final_results and not disable_fallback:
            final_results, fallback_platform = await self._fallback_to_trending(
                in_.platform, in_.limit, in_.min_interactions
            )
            fallback_used = bool(final_results)
            if fallback_used:
                logger.info(
                    f"[skill] trending_search: keyword '{in_.keyword}' 无结果，"
                    f"已用 {fallback_platform} 热门榜兜底，返回 {len(final_results)} 条"
                )
        elif not in_.platform:
            # 多平台路径：只翻译最终保留的 N 条（源内翻译已被 defer 跳过）
            await self._translate_final_results(final_results)

        summary = self._summarize_result_multi(
            platforms=searched_platforms,
            total_raw=len(raw_results),
            final_count=len(final_results),
            dropped_low=dropped_low,
            min_interactions=in_.min_interactions,
            first_title=final_results[0]["title"] if final_results else "",
            fallback_used=fallback_used,
            keyword=in_.keyword,
        )
        logger.info(
            f"[skill] trending_search platforms={searched_platforms}: "
            f"raw={len(raw_results)} → low_interactions dropped={dropped_low} "
            f"→ final={len(final_results)} (min_interactions={in_.min_interactions})"
            + (" [fallback=trending]" if fallback_used else "")
        )

        return {
            "results": final_results,
            "summary": summary,
            "count": len(final_results),
            "filter_stats": {
                "raw_count": len(raw_results),
                "dropped_low_interactions": dropped_low,
                "min_interactions": in_.min_interactions,
                "searched_platforms": searched_platforms,
                "fallback_used": fallback_used,
            },
            "platform": ",".join(searched_platforms) if searched_platforms else "none",
        }

    async def _translate_final_results(
        self,
        results: list[dict[str, Any]],
        budget: float = 4.0,
    ) -> None:
        """把最终保留结果的标题/摘要翻译成中文（原地修改）。

        多平台搜索时源内部翻译被 defer 跳过，这里只对真正返回给用户的
        limit 条做翻译：请求量从「源数 × limit × 2」降到「limit × 2」。
        中文内容在 translate_text 里会直接短路返回，不产生请求。
        """
        if not results:
            return
        import httpx

        from capabilities.content_search.sources.base import (
            create_async_client,
            get_proxy_url,
        )
        from capabilities.content_search.sources.translator import translate_batch

        titles = [r.get("title") or "" for r in results]
        summaries = [
            (r.get("summary") or "") if (r.get("summary") or "") != (r.get("title") or "") else ""
            for r in results
        ]
        t_start = asyncio.get_running_loop().time()

        try:
            client = await create_async_client(
                timeout=httpx.Timeout(5.0, connect=3.0),
                headers={"User-Agent": "multi-agent-xhs-platform/1.0"},
                proxy=get_proxy_url() or None,
                trust_env=False,
            )
            try:
                zh_titles, zh_summaries = await asyncio.wait_for(
                    asyncio.gather(
                        translate_batch(titles, target_lang="zh-CN", client=client),
                        translate_batch(summaries, target_lang="zh-CN", client=client),
                    ),
                    timeout=budget,
                )
            finally:
                await client.aclose()
        except asyncio.TimeoutError:
            logger.warning(f"[skill] final translation hit budget ({budget}s), keeping original text")
            return
        except Exception as e:
            logger.warning(f"[skill] final translation failed: {e}")
            return

        for r, zh_t, zh_s in zip(results, zh_titles, zh_summaries):
            if zh_t:
                r["title"] = zh_t
            if zh_s:
                r["summary"] = zh_s

        logger.info(
            f"[skill] final translation done in "
            f"{asyncio.get_running_loop().time() - t_start:.2f}s for {len(results)} items"
        )

    async def _collect_url_as_result(self, url: str, platform: str) -> dict[str, Any]:
        """把一条链接采集成搜索结果结构（单条）。

        走 work_collector 的完整降级链：已知平台深度解析 → 通用网页抓取
        （正文过短自动升级浏览器渲染）→ link-grabber。不按平台白名单拒绝。
        """
        from app.services.work_collector import collect_from_url

        # 不把用户选的「搜索平台」透传给采集器：选了 sogou 不代表这个链接
        # 属于 sogou。平台识别只作为路由提示，由采集器自己判。
        data = await collect_from_url(url)
        if not data:
            return {
                "results": [],
                "summary": f"链接采集失败：{url}",
                "count": 0,
                "filter_stats": {"raw_count": 0, "searched_platforms": [], "fallback_used": False},
                "platform": platform or "web",
                "error": "链接采集失败（页面不可达或无可提取正文）",
            }

        content_text = data.get("content_text") or ""
        result = {
            "platform": data.get("platform") or "web",
            "content_id": data.get("note_id") or url,
            "title": data.get("title") or url,
            "summary": content_text[:200],
            "content": content_text,
            "author": data.get("author_nickname") or "",
            "url": data.get("source_url") or url,
            "likes": int(data.get("likes") or 0),
            "comments": int(data.get("comments") or 0),
            "shares": int(data.get("shares") or 0),
            "views": 0,
            "collects": int(data.get("collects") or 0),
            "author_fans": int(data.get("author_fans") or 0),
            "cover_img": data.get("cover_img_url") or "",
            "published_at": "",
            "tags": data.get("tags") or [],
            "title_original": "",
            "summary_original": "",
            "interactions": int(data.get("likes") or 0)
            + int(data.get("comments") or 0)
            + int(data.get("shares") or 0),
            "collect_source": data.get("source") or "",
            "images": data.get("image_urls") or [],
        }
        return {
            "results": [result],
            "summary": f"已采集链接：{result['title'][:40]}（{len(content_text)} 字）",
            "count": 1,
            "filter_stats": {
                "raw_count": 1,
                "dropped_low_interactions": 0,
                "searched_platforms": [result["platform"]],
                "fallback_used": False,
                "collect_source": result["collect_source"],
            },
            "platform": result["platform"],
        }

    async def _search_single(
        self,
        platform: str,
        keyword: str,
        limit: int,
        time_range: str,
    ) -> tuple[list, list[str]]:
        """单平台搜索。返回 (results, [platform_name])。"""
        source = self._get_source(platform)
        if source is None:
            available = self._list_platforms()
            logger.warning(
                f"[skill] platform '{platform}' not available, "
                f"registered: {available}"
            )
            return [], []
        try:
            # 单平台也必须加超时：源卡住（反爬/登录态失效/上游挂起）时，
            # 原来会无限期挂着，用户看到的就是「搜索一直转圈」。
            results = await asyncio.wait_for(
                source.search_trending(keyword, limit, time_range),
                timeout=_SINGLE_PLATFORM_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.warning(
                f"[skill] trending_search timed out on {source.name} "
                f"({_SINGLE_PLATFORM_TIMEOUT}s), returning empty"
            )
            return [], [source.name]
        except RuntimeError as e:
            err_msg = str(e)
            logger.error(f"[skill] trending_search failed on {source.name}: {err_msg}")
            if "local client" in err_msg or "无 local client" in err_msg:
                logger.warning(f"[skill] {source.name} source unavailable (client not connected), returning empty")
                return [], [source.name]
            raise
        except Exception as e:
            logger.error(f"[skill] trending_search failed on {source.name}: {e}")
            raise
        return results, [source.name]

    async def _search_all_platforms(
        self,
        keyword: str,
        limit: int,
        time_range: str,
    ) -> tuple[list, list[str]]:
        """并发搜索所有已注册平台，合并结果。

        每个平台各抓 limit 条，合并后由上层统一排序截断。
        单平台失败不影响其他平台。

        调度规则（三层限流，缺一层就会慢）：
        1. 全局并发 _MAX_CONCURRENT：避免连接池打满
        2. 分组限流：共享同一上游配额（同一 API Key）的源进同一个组信号量，
           否则同一 key 被同时打爆 → 429 → 退避重试 → 单源直接吃满超时
        3. 整体预算 _TOTAL_BUDGET：到点收下已完成的结果，取消未完成的源
        """
        from capabilities.content_search.sources.manager import source_manager

        platforms = source_manager.list_platforms()
        if not platforms:
            logger.warning("[skill] no content source registered")
            return [], []

        # 全网搜索排除小红书：小红书需 Playwright + 登录态，易触发风控，
        # 由用户显式选择 xiaohongshu / xiaohongshu_spider 平台时单独搜索，不混入全网并发
        platforms = [p for p in platforms if p != "xiaohongshu"]
        if not platforms:
            logger.warning("[skill] no content source after excluding xiaohongshu")
            return [], []

        # 英文平台列表：这些平台内容以英文为主，中文关键词需翻译
        _EN_PLATFORMS = {
            "hackernews", "github", "reddit", "pinterest", "instagram",
            "twitter", "youtube", "tiktok", "medium", "devto",
        }

        # 搜索并发控制参数见模块顶部常量（_MAX_CONCURRENT / _PER_PLATFORM_TIMEOUT /
        # _TOTAL_BUDGET），便于统一调参与测试覆盖

        # 中文关键词 → 英文关键词映射（本地翻译，不依赖外部 API）
        _ZH_EN_MAP = {
            "教育": "education", "学习": "learning", "课程": "course",
            "培训": "training", "考试": "exam", "学校": "school",
            "大学": "university", "英语": "english", "数学": "math",
            "编程": "programming", "科技": "technology", "人工智能": "artificial intelligence",
            "AI": "AI", "机器学习": "machine learning", "深度学习": "deep learning",
            "大数据": "big data", "云计算": "cloud computing", "区块链": "blockchain",
            "创业": "startup", "投资": "investment", "金融": "finance",
            "健康": "health", "医疗": "medical", "减肥": "weight loss",
            "健身": "fitness", "美食": "food", "旅行": "travel",
            "穿搭": "fashion", "美妆": "beauty", "护肤": "skincare",
            "职场": "career", "副业": "side hustle", "赚钱": "make money",
            "读书": "reading", "写作": "writing", "设计": "design",
            "摄影": "photography", "音乐": "music", "电影": "movie",
            "游戏": "gaming", "宠物": "pet", "家居": "home decor",
            "育儿": "parenting", "心理": "psychology", "社交": "social media",
            "电商": "ecommerce", "直播": "livestream", "短视频": "short video",
        }

        def _translate_keyword_zh_to_en(kw: str) -> str:
            """本地中文关键词翻译为英文（用于英文平台搜索）。

            策略：逐词替换，保留英文部分不变。
            """
            result = kw
            for zh, en in sorted(_ZH_EN_MAP.items(), key=lambda x: -len(x[0])):
                if zh in result:
                    result = result.replace(zh, en)
            # 清理多余空格
            result = " ".join(result.split())
            # 如果翻译后和原文一样（全是中文），尝试 MyMemory
            if result == kw and any("\u4e00" <= ch <= "\u9fff" for ch in kw):
                return kw  # fallback to original
            return result

        # 预翻译中文关键词（用于英文平台搜索）
        en_keyword: str | None = None
        has_chinese = any("\u4e00" <= ch <= "\u9fff" for ch in keyword)
        if has_chinese:
            en_keyword = _translate_keyword_zh_to_en(keyword)
            if en_keyword != keyword:
                logger.info(f"[skill] translated keyword '{keyword}' → '{en_keyword}' for EN platforms")
            else:
                logger.info(f"[skill] keyword '{keyword}' could not be translated, using original for EN platforms")

        async def _search_one(plat: str) -> tuple[list, str]:
            src = source_manager.get_source(plat)
            if src is None:
                return [], plat
            per_limit = min(limit, _PER_SOURCE_LIMIT_CAP)
            t_start = asyncio.get_running_loop().time()
            try:
                kw = en_keyword if (plat in _EN_PLATFORMS and en_keyword) else keyword
                r = await asyncio.wait_for(
                    src.search_trending(kw, per_limit, time_range),
                    timeout=_PER_PLATFORM_TIMEOUT,
                )
                logger.info(
                    f"[skill] {plat}: {len(r)} results in "
                    f"{asyncio.get_running_loop().time() - t_start:.2f}s"
                )
                return r, plat
            except asyncio.TimeoutError:
                logger.warning(f"[skill] trending_search timed out on {plat} ({_PER_PLATFORM_TIMEOUT}s)")
                return [], plat
            except Exception as e:
                logger.error(f"[skill] trending_search failed on {plat}: {e}")
                return [], plat

        # 分组信号量：同 concurrency_group 的源共享上游配额（如 8 个 Tavily 站内搜索源）
        group_sems: dict[str, asyncio.Semaphore] = {}

        def _group_sem(plat: str) -> asyncio.Semaphore | None:
            src = source_manager.get_source(plat)
            if src is None:
                return None
            limit_per_group = getattr(src, "concurrency_limit", None)
            if not limit_per_group:
                return None
            group = getattr(src, "concurrency_group", None) or plat
            if group not in group_sems:
                group_sems[group] = asyncio.Semaphore(limit_per_group)
            return group_sems[group]

        # 并发搜索所有平台（先组内限流，再全局限流；固定顺序避免死锁）
        sem = asyncio.Semaphore(_MAX_CONCURRENT)

        async def _search_one_limited(plat: str) -> tuple[list, str]:
            gsem = _group_sem(plat)
            if gsem is None:
                async with sem:
                    return await _search_one(plat)
            async with gsem:
                async with sem:
                    return await _search_one(plat)

        tasks = [asyncio.create_task(_search_one_limited(p)) for p in platforms]
        _, pending = await asyncio.wait(tasks, timeout=_TOTAL_BUDGET)
        if pending:
            # 到整体预算还没跑完的源直接取消：宁可少几个源的结果，
            # 也不能让用户等满所有慢源的超时（慢源是尾部延迟的全部来源）。
            logger.warning(
                f"[skill] multi-platform search hit total budget {_TOTAL_BUDGET}s, "
                f"cancelled {len(pending)}/{len(tasks)} platforms: "
                f"{[p for p, t in zip(platforms, tasks) if t in pending]}"
            )
            for t in pending:
                t.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

        all_results: list = []
        searched: list[str] = []
        for item_task in tasks:
            if item_task.cancelled():
                continue
            exc = item_task.exception()
            if exc is not None:
                logger.warning(f"[skill] multi-platform gather: a platform failed: {exc}")
                continue
            r, plat = item_task.result()
            searched.append(plat)
            all_results.extend(r)

        logger.info(
            f"[skill] multi-platform search '{keyword}': "
            f"platforms={searched}, total={len(all_results)}"
        )
        return all_results, searched

    async def _fallback_to_trending(
        self,
        platform: str,
        limit: int,
        min_interactions: int,
    ) -> tuple[list[dict[str, Any]], str]:
        """热门榜兜底：关键词搜索全空时，对【用户指定平台】调 get_trending。

        红线：只 fallback 到用户指定的平台，绝不偷偷切到默认平台（小红书）。
        - platform 非空：用该平台的热门榜（用户指定 HackerNews 就用 HackerNews）
        - platform 为空（全网搜索）：用默认平台兜底（全网本就含小红书）
        返回 (final_results_dict_list, platform_name)。
        """
        source = self._get_source(platform)
        if source is None:
            return [], "none"

        try:
            raw = await source.get_trending(limit=limit * 2)
        except Exception as e:
            logger.error(f"[skill] fallback get_trending failed on {source.name}: {e}")
            return [], source.name

        items: list[dict[str, Any]] = []
        for content in raw:
            d = content.to_dict()
            d["interactions"] = d["likes"] + d["comments"] + d["shares"]
            items.append(d)

        items = [it for it in items if it["interactions"] >= min_interactions]
        items.sort(key=lambda x: x["interactions"], reverse=True)
        return items[:limit], source.name

    def _get_source(self, platform: str):
        """获取 ContentSource。"""
        from capabilities.content_search.sources.manager import source_manager

        if platform:
            return source_manager.get_source(platform)
        return source_manager.get_default()

    def _list_platforms(self) -> list[str]:
        from capabilities.content_search.sources.manager import source_manager
        return source_manager.list_platforms()

    @staticmethod
    def _summarize_result_multi(
        platforms: list[str],
        total_raw: int,
        final_count: int,
        dropped_low: int,
        min_interactions: int,
        first_title: str,
        fallback_used: bool,
        keyword: str,
    ) -> str:
        """多平台搜索结果摘要。"""
        platforms_str = "+".join(platforms) if platforms else "none"
        if final_count == 0:
            return (
                f"[{platforms_str}] 关键词 '{keyword}' 搜到 {total_raw} 条，"
                f"过滤低互动(<{min_interactions}) {dropped_low} 条后无结果"
            )
        if fallback_used:
            return (
                f"[{platforms_str}] 关键词 '{keyword}' 无结果，"
                f"已用 {platforms_str} 热门榜兜底，返回 {final_count} 条，"
                f"首条：{first_title[:40]}"
            )
        return (
            f"[{platforms_str}] 关键词 '{keyword}' 搜到 {total_raw} 条 → "
            f"过滤低互动(<{min_interactions}) {dropped_low} 条 → "
            f"保留 {final_count} 条热门内容，首条：{first_title[:40]}"
        )