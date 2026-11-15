"""Trending topics skill — 多平台实时热搜（Python 直抓，不依赖内层 LLM 工具）。

2026-11-03 重构：原 trending_topics 是 prompt skill（PromptDrivenSkill 单次裸
LLM 调用、无内嵌工具执行器）。SKILL.md 指示内层 LLM「用 trending_search /
web_fetch 抓 60s API」，但技能内层根本没有工具可执行——deepseek-v3 吐出原生
<｜｜DSML｜｜ invoke> token 无人解析执行，碎片原样落进 report 返回给主 Agent，
导致「热点类技能全部失效」（2026-11-03 Pulse 会话实录）。

现在改为 Python 技能：
- 直接并发抓 60s/xxapi 公益 API（复用 HotboardSource：主备源降级 + 5min 缓存）
- 返回结构化热搜数据（platform/title/hot/url/rank/relevance）
- 赛道过滤与二创分析交给主 Agent（它本来就是 LLM，拿到真数据自己会分析）
- 不依赖 source_manager 的注册状态（免疫 BUG-002「无内容源注册」），
  直接从 hotboard_sources.json 构建临时 HotboardSource 实例
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from app.core.skill_base import Skill
from app.engine.schemas import Permission
from app.core.skill_registry import register

if TYPE_CHECKING:
    from app.adapters.llm_base import LLMProtocol

logger = logging.getLogger(__name__)

# hotboard key → 展示用短平台名
_PLATFORM_KEY_LABEL: dict[str, str] = {
    "hb-weibo": "微博", "hb-douyin": "抖音", "hb-zhihu": "知乎",
    "hb-toutiao": "头条", "hb-baidu": "百度", "hb-bilibili": "B站",
    "hb-rednote": "小红书",
}

_PER_PLATFORM_TIMEOUT = 25.0  # 公益 API 并发时不稳（2026-11-03 真机：知乎/头条超时），放宽
_RETRY_PER_PLATFORM = 1        # 单平台失败自动重试次数（瞬时不稳定常见）

# 平台别名 → hotboard_sources.json 的 key（大小写不敏感，中文别名直配）
_PLATFORM_ALIAS: dict[str, str] = {
    "weibo": "hb-weibo", "微博": "hb-weibo",
    "douyin": "hb-douyin", "抖音": "hb-douyin",
    "zhihu": "hb-zhihu", "知乎": "hb-zhihu",
    "toutiao": "hb-toutiao", "头条": "hb-toutiao", "今日头条": "hb-toutiao",
    "baidu": "hb-baidu", "百度": "hb-baidu",
    "bilibili": "hb-bilibili", "bili": "hb-bilibili", "b站": "hb-bilibili", "哔哩哔哩": "hb-bilibili",
    "rednote": "hb-rednote", "小红书": "hb-rednote", "xhs": "hb-rednote",
}


class TrendingTopicsInput(BaseModel):
    """Input for trending topics skill."""

    platforms: str = Field(
        default="",
        description=(
            "平台列表，逗号分隔（weibo/微博, douyin/抖音, zhihu/知乎, "
            "toutiao/头条, baidu/百度, bilibili/B站, rednote/小红书）。"
            "空 = 抓全部已启用热榜平台"
        ),
    )
    limit_per_platform: int = Field(
        default=10,
        ge=1,
        le=50,
        description="每个平台返回条数上限",
    )
    track: str = Field(
        default="",
        description="赛道/领域关键词（可选，如'科技数码'/'美妆'），用于标注相关度",
    )


@register
class TrendingTopicsSkill(Skill):
    """多平台实时热搜 skill（Python 直抓版）。

    复用 HotboardSource（60s 主源 + xxapi 备源 + 缓存 + 解析容错），
    并发抓取多平台热搜榜，返回结构化数据。全部平台失败时如实返回
    ok=False，让上层 ReAct 循环观察失败并降级（如请用户粘贴热搜内容），
    绝不伪造数据。
    """

    node_type = "discover"
    name = "trending_topics"
    display_name = "热点发现"
    description = (
        "抓取微博/抖音/知乎/头条/百度/B站/小红书实时热搜榜，返回结构化热搜数据"
        "（平台/标题/热度/链接/排名/相关度）。当用户说'今天有什么热搜'、'热点'、"
        "'追热点'、'热搜榜'、'最近大家在聊什么'、'二创选题'时使用。"
        "赛道过滤与二创建议由你基于返回数据分析，无需再抓任何网页。"
    )
    input_schema = TrendingTopicsInput
    required_permissions = [Permission.XHS_SEARCH]  # 复用搜索权限（与 trending_search 一致）

    # ===== 目录/skill_doc 展示（替代原 trending-topics.md frontmatter） =====
    trigger_words = [
        "热搜", "热点", "追热点", "蹭热点", "二创选题", "热搜榜",
        "trending", "热点雷达", "最近在聊什么", "今天有什么热搜",
    ]
    prompt_guidance = (
        "返回结构化热搜数据（results: platform/title/hot/url/rank/relevance + report: "
        "热点速报 markdown 表）。优先展示全平台 Top 10；用户给了赛道或 Profile 时按 "
        "relevance 筛选并给 3-5 个二创选题（角度/格式/时效性）；跨平台重复话题优先"
        "（大事件信号）。全部数据源失败时如实告知并请用户粘贴热搜内容，绝不编造数据。"
    )
    usage_hint = (
        "调用示例：skill_call(skill='trending_topics', arguments={"
        "'platforms': '微博,抖音', 'limit_per_platform': 10, 'track': '科技数码'})。"
        "无参数也可直接调用（默认抓全部平台）。"
        "本技能直接返回真实热搜数据——拿到 results 后自己做赛道过滤与二创分析，"
        "不要再调 web_fetch 抓平台官网或 tophub（反爬返回垃圾数据）。"
    )
    skill_category = "analyze"
    skill_priority = "hot"
    # 沿用原 prompt skill 的模型档位声明（test_skill_model_tier 契约）
    model_tier = "standard"
    task_type = "analyze"
    temperature_override = 0.5

    def __init__(self, llm: LLMProtocol | None = None) -> None:
        super().__init__()
        self.llm = llm

    async def execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        in_ = TrendingTopicsInput.model_validate(inputs)

        targets = self._resolve_platforms(in_.platforms)
        if not targets:
            return {
                "ok": False,
                "error": (
                    f"未找到可用的热榜平台（请求: '{in_.platforms}'）。"
                    f"支持: 微博/抖音/知乎/头条/百度/B站/小红书。"
                    f"可检查 capabilities/content_search/sources/hotboard_sources.json"
                ),
            }

        # 直接从 JSON 配置构建临时 HotboardSource —— 不走 source_manager，
        # 免疫「无内容源注册」（BUG-002）等启动期注册失败问题
        from capabilities.content_search.sources.hotboard_source import (
            HotboardSource,
            load_hotboard_config,
        )

        cfg_all = load_hotboard_config()
        fetched: dict[str, list[dict[str, Any]]] = {}
        failed: list[str] = []

        async def _fetch_one(key: str, cfg: dict[str, Any]) -> None:
            # 公益 API 瞬时不稳定常见（2026-11-03 真机：知乎/头条首抓失败），
            # 每平台独立实例 + 失败自动重试一次，主备源降级在 HotboardSource 内部
            for attempt in range(1 + _RETRY_PER_PLATFORM):
                src = HotboardSource(
                    platform_key=key,
                    label=cfg.get("label", key),
                    primary_url=cfg["primary"],
                    backup_url=cfg.get("backup", ""),
                )
                try:
                    items = await asyncio.wait_for(
                        src.get_trending(limit=in_.limit_per_platform),
                        timeout=_PER_PLATFORM_TIMEOUT,
                    )
                    if items:
                        fetched[key] = [
                            {
                                "title": it.title,
                                "hot": it.likes,
                                "url": it.url,
                                "rank": (it.raw or {}).get("rank", 0),
                            }
                            for it in items
                        ]
                        return
                    if attempt < _RETRY_PER_PLATFORM:
                        logger.warning(
                            f"[skill] trending_topics {key}: empty result, retry {attempt + 1}"
                        )
                        continue
                    failed.append(key)
                except asyncio.TimeoutError:
                    if attempt < _RETRY_PER_PLATFORM:
                        logger.warning(
                            f"[skill] trending_topics {key}: timeout, retry {attempt + 1}"
                        )
                        continue
                    logger.warning(f"[skill] trending_topics timed out on {key}")
                    failed.append(key)
                except Exception as e:
                    if attempt < _RETRY_PER_PLATFORM:
                        logger.warning(
                            f"[skill] trending_topics {key}: {e}, retry {attempt + 1}"
                        )
                        continue
                    logger.error(f"[skill] trending_topics failed on {key}: {e}")
                    failed.append(key)
                finally:
                    await src.close()

        started = time.monotonic()
        await asyncio.gather(*(_fetch_one(k, cfg_all[k]) for k in targets))
        elapsed = round(time.monotonic() - started, 1)

        if not fetched:
            return {
                "ok": False,
                "error": (
                    "所有热榜数据源抓取失败（公益 API 可能暂时不可用）。"
                    "请如实告知用户，建议用户粘贴热搜截图或文字，不要编造热搜数据。"
                ),
                "failed_platforms": failed,
            }

        flat = self._merge_and_rank(fetched, in_.track)
        report = self._build_report(flat, fetched, failed, in_.track, elapsed)

        summary = (
            f"抓取 {len(targets)} 个平台：成功 {len(fetched)}，失败 {len(failed)}；"
            f"共 {len(flat)} 条热搜"
            + (f"，耗时 {elapsed}s" if elapsed else "")
        )
        logger.info(f"[skill] trending_topics: {summary}, failed={failed}")

        return {
            "ok": True,
            "results": flat,
            "count": len(flat),
            "report": report,
            "summary": summary,
            "by_platform": {k: len(v) for k, v in fetched.items()},
            "failed_platforms": failed,
            "track": in_.track,
            "note": (
                "赛道过滤与二创建议请基于本 results 数据自行分析输出，"
                "不需要再调用其他抓取工具"
            ),
        }

    @staticmethod
    def _resolve_platforms(platforms_str: str) -> list[str]:
        """解析平台参数 → hotboard key 列表。空 = 全部已启用平台。"""
        from capabilities.content_search.sources.hotboard_source import (
            load_hotboard_config,
        )

        enabled_keys = list(load_hotboard_config().keys())
        if not platforms_str.strip():
            return enabled_keys

        resolved: list[str] = []
        for token in platforms_str.replace("，", ",").split(","):
            token = token.strip().lower()
            if not token:
                continue
            key = _PLATFORM_ALIAS.get(token)
            if key and key in enabled_keys and key not in resolved:
                resolved.append(key)
            elif not key:
                logger.warning(f"[skill] trending_topics: unknown platform '{token}'")
        return resolved

    @staticmethod
    def _short_label(key: str) -> str:
        """hb-weibo/微博热搜 → 微博。"""
        return _PLATFORM_KEY_LABEL.get(key, key.removeprefix("hb-"))

    @staticmethod
    def _merge_and_rank(
        fetched: dict[str, list[dict[str, Any]]], track: str
    ) -> list[dict[str, Any]]:
        """合并各平台热搜 → 统一降序列表，标注跨平台重合与赛道相关度。"""
        # 跨平台重合统计（标题精确匹配 = 大事件信号）
        title_counter: dict[str, int] = {}
        for items in fetched.values():
            for it in items:
                t = it["title"].strip()
                title_counter[t] = title_counter.get(t, 0) + 1

        track_lower = track.strip().lower() if track else ""
        flat: list[dict[str, Any]] = []
        for key, items in fetched.items():
            platform = TrendingTopicsSkill._short_label(key)
            for it in items:
                title = it["title"].strip()
                # 相关度：赛道词命中 = 高；跨平台重合 = 中；其余 = 低
                if track_lower and track_lower in title.lower():
                    relevance = "高"
                elif title_counter.get(title, 0) > 1:
                    relevance = "中"
                else:
                    relevance = "低"
                flat.append({
                    "platform": platform,
                    "title": title,
                    "hot": it["hot"],
                    "url": it["url"],
                    "rank": it["rank"],
                    "cross_platform": title_counter.get(title, 0) > 1,
                    "relevance": relevance,
                })

        # 排序：热度降序（无热度值的按 rank）
        flat.sort(key=lambda x: (x["hot"], -x["rank"]), reverse=True)
        return flat

    @staticmethod
    def _build_report(
        flat: list[dict[str, Any]],
        fetched: dict[str, list[dict[str, Any]]],
        failed: list[str],
        track: str,
        elapsed: float,
    ) -> str:
        """生成热点速报 markdown（替代原 SKILL.md 输出模板的数据部分）。"""
        import datetime

        lines = [
            "# 热点速报",
            f"日期: {datetime.date.today().isoformat()}",
            f"平台: {', '.join(TrendingTopicsSkill._short_label(k) for k in fetched)}"
            + (f"（失败: {', '.join(TrendingTopicsSkill._short_label(k) for k in failed)}）" if failed else ""),
            "",
            "## 🔥 全平台热搜 Top 15",
            "| # | 平台 | 话题 | 热度 | 相关度 |",
            "|---|------|------|------|--------|",
        ]
        top = flat[:15]
        for i, it in enumerate(top, 1):
            mark = " 🔁" if it["cross_platform"] else ""
            lines.append(
                f"| {i} | {it['platform']} | {it['title']}{mark} | {it['hot']} | {it['relevance']} |"
            )
        cross = [it for it in flat if it["cross_platform"]][:5]
        if cross:
            lines += ["", "## 📊 跨平台重合话题（大事件信号）"]
            lines += [f"- {' / '.join(it['platform'] for it in flat if it['title'] == c['title'])}: {c['title']}" for c in cross]
        if track:
            related = [it for it in flat if it["relevance"] == "高"]
            if related:
                lines += ["", f"## 🎯 与赛道「{track}」相关"]
                lines += [f"- [{it['platform']}] {it['title']}（热度 {it['hot']}）" for it in related[:10]]
            else:
                lines += ["", f"## 🎯 与赛道「{track}」相关", "（本轮热搜中未发现直接相关话题，可从全量 Top 中挑选泛相关角度）"]
        lines += ["", f"_{len(flat)} 条 · 抓取耗时 {elapsed}s · 数据源: 60s/xxapi 公益 API_"]
        return "\n".join(lines)
