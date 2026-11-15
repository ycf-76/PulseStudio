"""离线重放：用录制的真实页面驱动真实解析逻辑，全程零网络。

为什么需要它：
  spider 的解析层（__INITIAL_STATE__ 提取、CSS 兜底选择器、from_card /
  detail_page 两条产出路径）此前没有任何离线验证手段，每次改动都只能拿
  真实账号跑一次同步来试错——真跑一次暴露一层，改完再跑暴露下一层。
  本模块把「真实 HTML → 真实 parse()」这段搬到本地，让改版、字段变动、
  选择器失效在测试里先红，而不是线上静默出错。

  解析代码零改动：ReplayResponse 只补齐 spider 实际用到的
  url / meta / css / text，其余属性转发给 Scrapling 的 Selector。

用法（测试）：
    from capabilities.scrapling.spiders import replay
    result = await replay.run_parse(spider, replay.load_html(replay.PROFILE_PAGE))
    assert result.items

用法（脚本）：
    backend/scripts/sync_dry_run.py  ← 一条命令模拟整次同步
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from scrapling import Selector

logger = logging.getLogger(__name__)

FIXTURE_DIR = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "xhs"

PROFILE_PAGE = "profile_page.html"
DETAIL_IMAGE = "detail_image_note.html"
DETAIL_VIDEO = "detail_video_note.html"
DETAIL_SINGLE = "detail_single_image.html"

REQUIRED_FIXTURES = (PROFILE_PAGE, DETAIL_IMAGE, DETAIL_VIDEO, DETAIL_SINGLE)

HOME_URL = "https://www.xiaohongshu.com/user/profile/replay"


class FixtureMissingError(FileNotFoundError):
    """录制页面缺失。明确提示补录方式，而不是让测试静默通过。"""


def fixture_path(name: str) -> Path:
    return FIXTURE_DIR / name


def has_fixture(name: str) -> bool:
    path = fixture_path(name)
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def missing_fixtures() -> list[str]:
    """列出缺失的录制页面，供测试 skip 与脚本报告使用。"""
    return [name for name in REQUIRED_FIXTURES if not has_fixture(name)]


def load_html(name: str) -> str:
    if not has_fixture(name):
        raise FixtureMissingError(
            f"缺少页面夹具：{fixture_path(name)}\n"
            "补录方式（普通浏览器操作，与爬虫无关，平台侧无任何异常特征）：\n"
            "  1. 登录后打开对应页面\n"
            "  2. F12 → Console → copy(document.documentElement.outerHTML)\n"
            f"  3. 粘贴保存为 {fixture_path(name)}"
        )
    return fixture_path(name).read_text(encoding="utf-8", errors="replace")


def _make_selector(html: str) -> Selector:
    try:
        return Selector(html, adaptive=True)
    except TypeError:
        return Selector(html)


class ReplayResponse:
    """Scrapling Response 的离线替身：真实 HTML + 真实 CSS 选择器，零网络。"""

    def __init__(
        self,
        html: str,
        url: str = HOME_URL,
        meta: dict[str, Any] | None = None,
        status: int = 200,
    ) -> None:
        object.__setattr__(self, "_selector", _make_selector(html))
        self.url = url
        self.meta = dict(meta or {})
        self.status = status

    def css(self, selector: str, **kwargs: Any) -> list:
        return self._selector.css(selector, **kwargs)

    @property
    def text(self) -> str:
        return getattr(self._selector, "text", "") or ""

    def __getattr__(self, item: str) -> Any:
        return getattr(object.__getattribute__(self, "_selector"), item)


@dataclass
class ReplayResult:
    """一次重放的产出：产出的 item、待抓的详情页 Request、被推迟的条数。"""

    items: list[dict[str, Any]] = field(default_factory=list)
    requests: list[Any] = field(default_factory=list)
    deferred: int = 0

    @property
    def detail_note_ids(self) -> list[str]:
        return [
            str(getattr(req, "meta", {}).get("note_id", "")) for req in self.requests
        ]


async def run_parse(
    spider: Any,
    html: str,
    url: str = HOME_URL,
    meta: dict[str, Any] | None = None,
) -> ReplayResult:
    """重放主页解析：驱动真实的 spider.parse()。"""
    result = ReplayResult()
    async for out in spider.parse(ReplayResponse(html, url=url, meta=meta)):
        if isinstance(out, dict):
            result.items.append(out)
        else:
            result.requests.append(out)
    result.deferred = int(getattr(spider, "detail_deferred", 0) or 0)
    return result


async def run_parse_note(
    spider: Any,
    html: str,
    *,
    note_id: str,
    from_card: bool = True,
    likes_hint: int = 0,
    url: str = "",
) -> ReplayResult:
    """重放详情页解析：驱动真实的 spider.parse_note()。"""
    meta = {"note_id": note_id, "from_card": from_card, "likes_hint": likes_hint}
    detail_url = url or f"https://www.xiaohongshu.com/explore/{note_id}"
    result = ReplayResult()
    async for out in spider.parse_note(ReplayResponse(html, url=detail_url, meta=meta)):
        if isinstance(out, dict):
            result.items.append(out)
        else:
            result.requests.append(out)
    return result