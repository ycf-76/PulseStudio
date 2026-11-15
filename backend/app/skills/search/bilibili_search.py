"""B站热门搜索 Skill：通过 B站公开 API 搜索视频内容。

设计：
- 调用 B站搜索 API（https://api.bilibili.com/x/web-interface/search/all）
- 返回统一的搜索结果结构（title / author / plays / likes / url / cover）
- 支持按关键词搜索，按播放量排序
- 无需登录态（公开 API）
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from pydantic import BaseModel, Field

from app.core.skill_base import Skill
from app.engine.schemas import Permission
from app.core.skill_registry import register

logger = logging.getLogger(__name__)

BILIBILI_SEARCH_API = "https://api.bilibili.com/x/web-interface/search/all"

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Referer": "https://search.bilibili.com/",
}


class BilibiliSearchInput(BaseModel):
    keyword: str = Field(..., description="搜索关键词")
    limit: int = Field(default=10, ge=1, le=50, description="返回条数上限")
    order: str = Field(
        default="totalrank",
        description="排序方式：totalrank(综合) / click(播放量) / pubdate(发布时间)",
    )


@register
class BilibiliSearchSkill(Skill):
    """B站视频搜索 Skill。

    通过 B站公开搜索 API 搜索视频，返回标题/播放量/UP主/链接等。
    无需登录态。
    """

    node_type = "search"
    name = "bilibili_search"
    display_name = "B站搜索"
    description = (
        "搜索B站视频内容，返回标题、播放量、UP主、链接等。"
        "当用户需要搜索B站/哔哩哔哩视频时调用。"
        "输入 keyword，返回搜索结果列表"
    )
    input_schema = BilibiliSearchInput
    required_permissions = [Permission.XHS_SEARCH]
    trigger_words = [
        "B站搜索", "哔哩哔哩搜索", "搜索B站", "bilibili搜索",
        "B站视频", "找B站", "bilibili",
    ]

    prompt_guidance: str = (
        "【bilibili_search 使用指引】\n"
        "- 用途：搜索B站视频，返回标题/播放量/UP主/链接等结构化数据。\n"
        "- 触发场景：用户说「搜B站」「B站搜一下」「在哔哩哔哩找」等。\n"
        "- keyword 参数：搜索关键词。\n"
        "- limit 参数：返回条数，默认10，最大50。\n"
        "- order 参数：排序方式，totalrank(综合)/click(播放量)/pubdate(发布时间)。\n"
        "- 无需登录态，使用公开 API。"
    )

    async def execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        in_ = BilibiliSearchInput.model_validate(inputs)
        keyword = in_.keyword.strip()

        if not keyword:
            return {"ok": False, "error": "关键词不能为空"}

        params = {
            "keyword": keyword,
            "page": 1,
            "pagesize": in_.limit,
            "order": in_.order,
            "search_type": "video",
        }

        try:
            from capabilities.content_search.sources.base import create_async_client

            client = await create_async_client(timeout=15.0)
            async with client:
                resp = await client.get(
                    BILIBILI_SEARCH_API,
                    params=params,
                    headers=_DEFAULT_HEADERS,
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as e:
            logger.error(f"[BilibiliSearchSkill] API request failed: {e}")
            return {"ok": False, "error": f"B站搜索请求失败: {e}"}
        except Exception as e:
            logger.error(f"[BilibiliSearchSkill] unexpected error: {e}", exc_info=True)
            return {"ok": False, "error": f"搜索异常: {e}"}

        code = data.get("code", -1)
        if code != 0:
            msg = data.get("message", "unknown")
            return {"ok": False, "error": f"B站API返回错误: code={code}, message={msg}"}

        result_data = data.get("data", {})
        video_result = result_data.get("result", [])

        if not video_result:
            return {
                "ok": True,
                "keyword": keyword,
                "count": 0,
                "results": [],
                "message": f"未找到与「{keyword}」相关的B站视频",
            }

        results = []
        for item in video_result[: in_.limit]:
            title = item.get("title", "")
            title = _strip_html_tags(title)
            results.append({
                "title": title,
                "author": item.get("author", ""),
                "plays": item.get("play", 0),
                "likes": item.get("like", 0),
                "duration": item.get("duration", ""),
                "bvid": item.get("bvid", ""),
                "url": f"https://www.bilibili.com/video/{item.get('bvid', '')}",
                "cover": item.get("pic", ""),
                "pubdate": item.get("pubdate", 0),
                "tag": item.get("tag", ""),
            })

        return {
            "ok": True,
            "keyword": keyword,
            "count": len(results),
            "results": results,
            "message": f"找到 {len(results)} 条B站视频",
        }


def _strip_html_tags(text: str) -> str:
    import re
    return re.sub(r"<[^>]+>", "", text)