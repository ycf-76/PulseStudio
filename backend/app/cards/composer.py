"""Compose a constrained card deck for the ReAct conversation."""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.cards.audit import audit_deck, audit_page, deck_density
from app.cards.recipe import RecipeId, get_recipe
from app.cards.renderer import to_card_draft
from app.cards.schema import (
    BentoGrid,
    CardDeck,
    CardPage,
    Cell,
    Compare,
    CompareRow,
    Cta,
    Heading,
    ImageBlock,
    Item,
    ListBlock,
    Quote,
    Stat,
    Steps,
    Text,
    ThemeId,
    Timeline,
    TimelineItem,
)
from app.services.asset_library import list_assets

logger = logging.getLogger(__name__)


class Section(BaseModel):
    kind: Literal["list", "steps", "compare", "text", "bento", "stat", "timeline", "quote"]
    title: str = Field(min_length=1, max_length=80)
    text: str = Field(default="", max_length=480)
    items: list[Item] = Field(default_factory=list, max_length=8)
    left_title: str = Field(default="之前", max_length=30)
    right_title: str = Field(default="之后", max_length=30)
    rows: list[CompareRow] = Field(default_factory=list, max_length=6)
    # P1：矩阵 / 单数据卡共享一组格子（bento 2-6 张，stat 只看第一张）
    cells: list[Cell] = Field(default_factory=list, max_length=6)
    # P2：时间线条目（date + text + note）
    timeline_items: list[TimelineItem] = Field(default_factory=list, max_length=8)
    # P2：金句（text + author + kicker）
    quote_text: str = Field(default="", max_length=120)
    quote_author: str = Field(default="", max_length=40)
    quote_kicker: str = Field(default="", max_length=30)


class Plan(BaseModel):
    subtitle: str = Field(default="", max_length=160)
    sections: list[Section] = Field(min_length=1, max_length=5)
    closing: str = Field(default="收藏备用", max_length=60)


# 上游 LLM 可能把「分页/排版指令」混进正文（2026-11-07 实测：
# 「第1页（封面，必须有超大主标题）：…」「【P2 数据反差】…」「要求：每页输出独立HTML文档」）。
# 这些是制作指令不是内容，进规划 LLM 会被当成事实渲染进卡片，必须在入口剥离。
_PAGE_MARKER_RE = re.compile(r"第\s*\d+\s*页\s*(?:[（(][^）)]*[)）])?\s*[:：]?")
_P_TAG_MARKER_RE = re.compile(r"【\s*P\s*\d+\s*[^】]*】\s*[:：]?")
_REQUIREMENT_LINE_RE = re.compile(r"(?m)^\s*要求\s*[:：][^\n]*$")


def _strip_layout_directives(content: str) -> str:
    """剥离混进正文的分页/排版指令，只留真实内容。

    注意「第一步：磨豆」这类真实步骤不受影响——模式要求「第N页」或「【P数字」。
    """
    text = _PAGE_MARKER_RE.sub("", content)
    text = _P_TAG_MARKER_RE.sub("", text)
    text = _REQUIREMENT_LINE_RE.sub("", text)
    return text.strip()


async def _plan(topic: str, content: str, llm: Any, recipe_id: RecipeId | None = None) -> Plan:
    """把内容规划成 Plan。

    `recipe_id` 是用户选的整套骨架，它在这里**引导内容如何组织**，而不是事后把内容硬套进版式
    —— 硬套必然出现密度不够或溢出。无 LLM 时退化为按行拆分，此时 recipe 不生效（只能得到清单）。
    """
    content = _strip_layout_directives(content)
    recipe = get_recipe(recipe_id)
    if llm is not None:
        prompt = (
            "你是社媒图文信息架构师。仅输出 JSON 对象，不要 HTML/CSS/Markdown。\n"
            f"【用户选择的骨架】{recipe.label}（{recipe.hint}）。{recipe.guidance}\n"
            f"【强制约束】主力 kind：{'、'.join(recipe.preferred_kinds)}（每个页面优先用主力）"
            f"{('；允许补充 kind：' + '、'.join(recipe.aux_kinds) + '（仅当内容确实需要时才用）') if recipe.aux_kinds else ''}\n"
            "禁止使用上述以外的 kind。将用户内容拆成 2-4 个有实质信息的页面。\n"
            "对象形状: {\"subtitle\":\"封面副标题\",\"sections\":[{\"kind\":\"list\","
            "\"title\":\"标题\",\"text\":\"说明\","
            "\"items\":[{\"text\":\"主行\",\"note\":\"副行补充说明\",\"tag\":\"右侧短标签\"}],"
            "\"left_title\":\"之前\",\"right_title\":\"之后\","
            "\"rows\":[[\"维度标签\",\"前\",\"后\"]],"
            "\"cells\":[{\"kicker\":\"分类\",\"big\":\"数据\",\"unit\":\"单位\","
            "\"note\":\"解读\",\"source\":\"来源\"}],"
            "\"timeline_items\":[{\"date\":\"2024 Q1\",\"text\":\"标题\",\"note\":\"说明\"}],"
            "\"quote_text\":\"金句正文\",\"quote_author\":\"作者\",\"quote_kicker\":\"日期\"}],"
            "\"closing\":\"结尾句\"}。\n"
            "items 的元素可以只写一个字符串（等价 {\"text\":...}），但**强烈建议写对象**：\n"
            "note 是主行下面的灰色小字，用来补一句「为什么 / 怎么用 / 举个例子」；\n"
            "tag 是右侧的短标签（≤6 字，如词性、年份、难度），没有就省略。\n"
            "rows 的元素可以是 [\"前\",\"后\"]，也可以是 [\"这一行比什么\",\"前\",\"后\"]。\n"
            "cells（bento / stat 共用）每个格子有 kicker/big/unit/note/source 五层：\n"
            "- bento：4-6 格矩阵，每格一个数据点；少于 4 格会被审计判 FAIL；\n"
            "- stat：1 格数据强调，巨数字必须配 note（这数字在说什么）和 source（数据来源），\n"
            "  没有这两层看起来像孤立大数字。\n"
            "- timeline：3-5 条时间锚点，每条要有 date + text + note（背景说明）；\n"
            "- quote：1 句金句（60-80 字）+ author + kicker，允许较空但必须补三锚点。\n"
            "密度要求（卡片是 3:4 竖版，内容太少会出现大片空白，务必写足）：\n"
            "- list：5-6 条，每条都要有 note，不要只写两个字的词；\n"
            "- steps：4-5 步，每步写清「做什么 + 为什么」（为什么写进 note）；\n"
            "- compare：5 组对比行，两栏都要有内容，尽量给每行加维度标签；\n"
            "- bento：4-6 格，每格写满 kicker+big+unit+note，不要只给一个数字；\n"
            "- stat：1 格，写满 kicker+big+unit+note+source 五层；\n"
            "- text：写成 80 字以上的一段，包含原因、例子或类比，不要只写一句结论；\n"
            "- timeline：3-5 条时间锚点，每条 date + text + note；\n"
            "- quote：1 句金句（60-80 字）+ author + kicker，允许较空但必须补三锚点。\n"
            "尽量少用 text，优先用 list / steps / compare / bento / stat 这类信息密度高的结构。\n"
            "原文里若混有分页、排版或渲染要求（如「第N页」「【P1】」「要求：…」），"
            "一律视为制作指令而非内容：不得据此生成页面，也不得让它们出现在任何标题或正文中。\n"
            "不要编造事实；超长文字请分段。"
            f"\n主题：{topic[:80]}\n原文：{content[:4500]}"
        )
        _complex_kinds = {"bento", "stat", "compare", "timeline"}
        _plan_max_tokens = 4096 if any(k in recipe.preferred_kinds for k in _complex_kinds) else 2800
        try:
            response = await llm.chat(messages=[
                {"role": "system", "content": "只输出满足要求的 JSON；保留用户提供的事实。"},
                {"role": "user", "content": prompt},
            ], max_tokens=_plan_max_tokens)
            raw = response.get("content", "") if isinstance(response, dict) else ""
            match = re.search(r"\{[\s\S]*\}", raw)
            if match:
                return Plan.model_validate(json.loads(match.group(0)))
        except (ValueError, TypeError, KeyError) as exc:
            logger.warning("Structured card planning invalid: %s", exc)
        except Exception as exc:
            logger.warning("Structured card planning unavailable: %s", exc)
    if not content.strip():
        raise ValueError("缺少正文且无法完成内容规划，请先提供卡片文案")
    lines = [part.strip(" \t\n-•1234567890.、") for part in re.split(r"[\n。；]+", content) if part.strip()]
    items = [line[:75] for line in lines if line][:8]
    if len(items) >= 2:
        return Plan(subtitle=content[:80], sections=[Section(kind="list", title=topic[:80], items=items)])
    return Plan(subtitle=content[:80], sections=[Section(kind="text", title=topic[:80], text=content[:480])])


def _trim_item(item: Item) -> Item:
    """模型可能超长，截断后仍要保住副行——副行是密度的关键，不能因为超长就丢掉。"""
    return Item(text=item.text[:90], note=item.note[:120], tag=item.tag[:16])


def _trim_row(row: CompareRow) -> CompareRow:
    return CompareRow(label=row.label[:30], left=row.left[:85], right=row.right[:85])


def _main_texts(items) -> list[str]:
    """取条目的主行文本——副行只在账本版式里出现，退化成纯文字时只留主行。"""
    return [item.text for item in items if item.text]


def _trim_cell(cell: Cell) -> Cell:
    """截断单元字段时也保留 kicker/note——这些是密度与可信度的关键。"""
    return Cell(
        kicker=cell.kicker[:20], big=cell.big[:24], unit=cell.unit[:16],
        note=cell.note[:80], source=cell.source[:40],
    )


def _split_to_items(text: str, limit: int = 6) -> list[Item]:
    """把一段说明拆成清单条目——清单天然吃高度，是文字页欠填时的首选修正。

    只在能拆出足够多实质条目时才拆；拆不动就维持原样，绝不为了填满而灌水。
    """
    # 逗号也参与拆分：小红书清单本来就是短语，长句拆开后信息量不变、密度更高。
    parts = [part.strip(" \t\n-•，,、") for part in re.split(r"[\n；;。，,]+", text) if part.strip()]
    return [Item(text=part[:90]) for part in parts if len(part) >= 4][:limit]


def _recap_points(pages: list[CardPage], limit: int = 5) -> list[str]:
    """收集可做导读/回顾的真实信息点：先页标题，不够再取清单条目主行。

    只取标题时短牌组（封面 + 1 页正文 + 收尾）凑不出条数，下面的欠填修正阶梯
    会被 `>=3` 守卫整段跳过 —— 退化路径（llm=None）实测收尾页最大空白带 60%+。
    清单条目同样是牌组里的真实内容，用它兜底。
    """
    points = [b.text.strip() for p in pages for b in p.blocks
              if isinstance(b, Heading) and b.text.strip()]
    if len(points) < limit:
        for p in pages:
            for b in p.blocks:
                if not isinstance(b, ListBlock):
                    continue
                for it in b.items:
                    text = it.text.strip()
                    if text and text not in points:
                        points.append(text)
                        if len(points) >= limit:
                            return points[:limit]
    return points[:limit]


def _fill_thin_pages(deck: CardDeck) -> CardDeck:
    """渲染前的欠填修正阶梯：不改文案语义，只换更能承载当前内容量的骨架。

    两条确定性修正：
    1. 文字页太空 → 拆成清单项，换 list_ledger（清单比整段文字吃高度）。
    2. 收尾页只有一句号召 → 补上前面各页的要点回顾。
    """
    for page in deck.pages:
        if page.layout_id != "content_text":
            continue
        text = next((b for b in page.blocks if isinstance(b, Text)), None)
        if text is None or audit_page(deck, page)["pass"]:
            continue
        items = _split_to_items(text.text)
        if len(items) < 3:
            continue
        page.blocks = [b for b in page.blocks if not isinstance(b, Text)]
        page.blocks.append(ListBlock(id=f"list_{page.id}", items=items))
        page.layout_id = "list_ledger"

    ending = next((p for p in deck.pages if p.layout_id == "end_cta"), None)
    if ending is not None and not audit_page(deck, ending)["pass"]:
        if not any(isinstance(b, ListBlock) for b in ending.blocks):
            review = _recap_points(deck.pages[:-1], limit=5)
            if len(review) >= 3:
                ending.blocks.insert(1, ListBlock(id=f"list_{ending.id}", items=review))

    # 封面挂「本期导读」：把后续各页标题做成预告。
    # 这是带信息量的承载，不是用装饰图形把下半画幅糊满。
    cover = deck.pages[0]
    if cover.layout_id == "cover_display" and not audit_page(deck, cover)["pass"]:
        if not any(isinstance(b, ListBlock) for b in cover.blocks):
            preview = _recap_points(deck.pages[1:], limit=4)
            if len(preview) >= 3:
                cover.blocks.append(ListBlock(id=f"list_{cover.id}", items=preview))
    return deck


def _volume(section: Section) -> int:
    """一节内容的体量（字符数），用来判断"补内容"到底补没补。"""
    return (len(section.text)
            + sum(len(item.text) + len(item.note) + len(item.tag) for item in section.items)
            + sum(len(row.label) + len(row.left) + len(row.right) for row in section.rows)
            + sum(len(c.kicker) + len(c.big) + len(c.unit) + len(c.note) + len(c.source)
                 for c in section.cells)
            + sum(len(t.date) + len(t.text) + len(t.note) for t in section.timeline_items)
            + len(section.quote_text) + len(section.quote_author) + len(section.quote_kicker))


async def _repair_plan(plan: Plan, deck: CardDeck, topic: str, content: str, llm: Any) -> Plan | None:
    """把密度审计的具体缺口回灌给模型，让它把欠填的章节补饱满。

    这是欠填修正阶梯的最后一阶：前两阶（换骨架、挂真实目录/回顾）都是确定性的、
    不新增内容；只有当它们仍撑不满时，才把「第几节、缺多少 px、缺在哪」交给模型扩写。

    三条约束，缺一不可：
    1. 只扩写欠填的章节，其余原样返回；
    2. 补完必须**真的变多**，否则视为模型只是把原话换个说法，沿用原稿；
    3. 明确禁止编造事实——密度不够的解法是把原文里已有的信息写足，不是注水。
    """
    if llm is None:
        return None
    failing = [r for r in audit_deck(deck) if not r["pass"]]
    if not failing:
        return None

    # 用页面标题把审计结果映射回 section 下标：正文页的 Heading 就是 section.title。
    by_id = {page.id: page for page in deck.pages}
    targets: list[tuple[int, dict]] = []
    for report in failing:
        page = by_id.get(report["page_id"])
        if page is None:
            continue
        heading = next((b.text for b in page.blocks if isinstance(b, Heading)), None)
        index = next((i for i, s in enumerate(plan.sections) if s.title == heading), None)
        if index is not None:
            targets.append((index, report))
    if not targets:
        return None

    brief = "\n".join(
        f'- 第 {index + 1} 节「{plan.sections[index].title}」（kind={plan.sections[index].kind}）：'
        f'当前填充率 {report["fill"]:.0%}。{report["hint"] or "内容不足以填满 3:4 画幅"}'
        for index, report in targets
    )
    prompt = (
        "下面是社媒图文卡片的内容规划。卡片是 3:4 竖版，有几节内容太少，"
        "渲染后会出现大片空白，看起来很廉价。\n"
        "请**只扩写**下列章节，其余章节一字不改地原样保留，整体仍返回一个完整 JSON 对象。\n"
        "扩写铁律：\n"
        "- 保留原有事实与观点，**严禁编造**原文里没有的信息；\n"
        "- 缺多少补多少：清单补足到 5-6 条、对比补足到 5 组、说明写到 80 字以上；\n"
        "- 宁可多写一条真实要点，也不要把原来的话换个说法凑数。\n"
        f"需要补内容的章节：\n{brief}\n\n"
        f"当前规划 JSON：\n{plan.model_dump_json()}\n\n"
        f"原文（从中提取更多真实信息来扩写；分页/排版等制作指令不是内容，禁止提取）：\n{_strip_layout_directives(content)[:3000]}"
    )
    try:
        response = await llm.chat(messages=[
            {"role": "system", "content": "只输出满足要求的 JSON；保留用户提供的事实。"},
            {"role": "user", "content": prompt},
        ], max_tokens=2600)
        raw = response.get("content", "") if isinstance(response, dict) else ""
        match = re.search(r"\{[\s\S]*\}", raw)
        if not match:
            return None
        candidate = Plan.model_validate(json.loads(match.group(0)))
    except (ValueError, TypeError, KeyError) as exc:
        logger.warning("卡片密度补内容返回非法: %s", exc)
        return None
    except Exception as exc:
        logger.warning("卡片密度补内容不可用: %s", exc)
        return None

    if len(candidate.sections) != len(plan.sections):
        return None
    # 必须真的变多，否则说明模型只是在复述，沿用原稿比"看起来改过"更诚实。
    for index, _ in targets:
        if _volume(candidate.sections[index]) <= _volume(plan.sections[index]):
            return None
    return candidate


def _asset_block(asset: dict, block_id: str) -> ImageBlock:
    return ImageBlock(
        id=block_id,
        media_id=asset["asset_id"],
        source="ai" if asset.get("source") == "ai" else "upload",
        resolved_url=asset["original_url"],
        alt=asset.get("filename", "配图")[:80],
    )


def _build_deck(
    plan: Plan, topic: str, images: list[dict], image_layout: str, theme_id: str, deck_id: str,
) -> CardDeck:
    """把语义规划落成 Deck。抽出来是因为「补内容」后要用同一套规则重建一次。

    `images` 会被按版式消费（pop），剩下的由调用方作为未使用素材上报。
    """
    pages: list[CardPage] = []
    cover_blocks: list = [Heading(id="heading_1", text=topic)]
    if plan.subtitle:
        cover_blocks.append(Text(id="text_1", text=plan.subtitle))
    cover_layout = "cover_display"
    if images and image_layout == "top":
        cover_layout = "cover_top_image"
        cover_blocks.append(_asset_block(images.pop(0), "image_1"))
    pages.append(CardPage(id="page_01", role="cover", layout_id=cover_layout, blocks=cover_blocks))

    for i, section in enumerate(plan.sections[:5], 2):
        blocks: list = [Heading(id=f"heading_{i}", text=section.title)]
        if section.kind == "list" and section.items:
            layout = "list_ledger"
            blocks.append(ListBlock(id=f"list_{i}", items=[_trim_item(it) for it in section.items]))
            if images and image_layout == "side":
                layout = "image_right_ledger"
                blocks.append(_asset_block(images.pop(0), f"image_{i}"))
        elif section.kind == "steps" and len(section.items) >= 2:
            layout = "steps_flow"
            blocks.append(Steps(id=f"steps_{i}", items=[_trim_item(it) for it in section.items[:7]]))
        elif section.kind == "compare" and len(section.rows) >= 2:
            layout = "compare_split"
            blocks.append(Compare(id=f"compare_{i}", left_title=section.left_title,
                                  right_title=section.right_title,
                                  rows=[_trim_row(row) for row in section.rows]))
        elif section.kind == "bento" and len(section.cells) >= 2:
            # 矩阵：2-6 格自适应，≥4 格才过最小密度线。
            layout = "bento_grid"
            blocks.append(BentoGrid(id=f"bento_{i}",
                                    cells=[_trim_cell(c) for c in section.cells[:6]]))
        elif section.kind == "stat" and section.cells:
            layout = "stat_card"
            blocks.append(Stat(id=f"stat_{i}", cell=_trim_cell(section.cells[0])))
        elif section.kind == "timeline" and len(section.timeline_items) >= 3:
            layout = "timeline_flow"
            blocks.append(Timeline(id=f"timeline_{i}", items=section.timeline_items[:8]))
        elif section.kind == "quote" and section.quote_text.strip():
            layout = "quote_callout"
            # 金句页的 LAYOUTS 只含 quote、不含 heading（kicker 由块内承载），
            # 必须把前面 prepended 的 Heading 移除，否则 validate_layout 报"含有不允许的组件"。
            blocks = [b for b in blocks if not isinstance(b, Heading)]
            blocks.append(Quote(
                id=f"quote_{i}", text=section.quote_text.strip(),
                author=section.quote_author.strip(), kicker=section.quote_kicker.strip(),
            ))
        else:
            layout = "content_text"
            blocks.append(Text(id=f"text_{i}", text=(section.text or "；".join(_main_texts(section.items)) or section.title)[:480]))
        if images and image_layout == "overlay":
            layout = "image_bg_overlay"
            blocks = [Heading(id=f"heading_{i}", text=section.title)]
            summary = section.text or "；".join(_main_texts(section.items[:2])) or section.title
            blocks.append(Text(id=f"text_{i}", text=summary[:160]))
            blocks.append(_asset_block(images.pop(0), f"image_{i}"))
        elif images and image_layout == "top" and i == 2:
            # Additional uploaded images should be used, not silently discarded.
            layout = "image_bg_overlay"
            summary = section.text or "；".join(_main_texts(section.items[:2])) or section.title
            blocks = [Heading(id=f"heading_{i}", text=section.title),
                      Text(id=f"text_{i}", text=summary[:160]),
                      _asset_block(images.pop(0), f"image_{i}")]
        pages.append(CardPage(id=f"page_{i:02}", role="body", layout_id=layout, blocks=blocks))

    pages.append(CardPage(id=f"page_{len(pages) + 1:02}", role="ending", layout_id="end_cta", blocks=[
        Heading(id="heading_end", text="回顾一下"),
        Cta(id="cta_end", text=plan.closing or "收藏备用"),
    ]))
    return CardDeck(deck_id=deck_id, title=topic, theme_id=theme_id, pages=pages)


_DANGEROUS_ATTR_RE = re.compile(r"""(?:^|[\s"'])on\w+""", re.I)
_ALLOWED_TAG_RE = re.compile(
    r"<\s*(/?)\s*(script|iframe|object|embed|form|input|button|link|meta|base)\b[^>]*>",
    re.I,
)


def _sanitize_html(html: str) -> str:
    """安全校验：移除危险标签和事件属性，锁定画布尺寸。"""
    html = _ALLOWED_TAG_RE.sub("", html)
    html = _DANGEROUS_ATTR_RE.sub("", html)
    html = re.sub(r"<script[\s\S]*?</script>", "", html, flags=re.I)
    if "<html" not in html.lower():
        html = (
            '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=1080,initial-scale=1">'
            f"<style>html,body{{margin:0;width:1080px;height:1440px;overflow:hidden}}</style>"
            f"</head><body>{html}</body></html>"
        )
    else:
        html = re.sub(
            r"<html[^>]*>",
            '<html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=1080,initial-scale=1">'
            "<style>html,body{margin:0;width:1080px;height:1440px;overflow:hidden}</style></head>",
            html,
            count=1,
            flags=re.I,
        )
    return html


_FREE_FORM_DENSITY_FIX = (
    "<style>"
    "html,body{margin:0;height:1440px;overflow:hidden}"
    "body>*:first-child{height:1440px;display:flex;flex-direction:column;"
    "justify-content:space-between}"
    "</style>"
)


def _fix_free_form_whitespace(html: str) -> str:
    """自由风格页死空白修复。

    LLM 写页脚时习惯用 margin-top:auto 把页脚推到底，这会把所有剩余空间挤成
    中段一条死带，底部 25% 只剩一条页脚细线。设计系统禁止该写法，结构化链路
    靠 space-around 解决；这里对自由风格 HTML 做确定性等价修复：去掉
    margin-top:auto，改由根容器把剩余空间均摊进各段间距。
    """
    html = re.sub(r"margin-top\s*:\s*auto", "margin-top:0", html, flags=re.I)
    idx = html.lower().rfind("</head>")
    if idx > 0:
        return html[:idx] + _FREE_FORM_DENSITY_FIX + html[idx:]
    return _FREE_FORM_DENSITY_FIX + html


def _extract_complete_html_pages(raw: str) -> list[str]:
    """提取完整页面 HTML：带闭合 fence 的块，且块内必须有 </html> 才算完整页。

    截断的尾巴（无闭合 fence 或无 </html>）不算页 —— 静默收下就会把残缺 HTML 渲染给用户。
    """
    blocks = re.findall(r"```html\s*\n([\s\S]*?)```", raw)
    if not blocks:
        blocks = re.findall(r"```\s*\n([\s\S]*?)```", raw)
    if not blocks:
        blocks = re.findall(r"<!doctype\s+html[\s\S]*?(?=<!doctype\s+html|\Z)", raw, re.I)
    return [b.strip() for b in blocks if "</html" in b.lower()]


def _has_unterminated_fence(raw: str) -> bool:
    """检测输出是否带着没写完的尾巴（奇数个 fence = 最后一个块没有闭合）。"""
    return raw.count("```") % 2 == 1


async def _render_free_form(
    plan: Plan, topic: str, style_hint: str, llm: Any,
    images: list[dict] | None = None,
) -> dict:
    """LLM 直出 HTML：用户描述的视觉风格不在标准 THEMES 中时自动触发。

    把内容规划 + 用户风格描述喂给 LLM，让它直接输出每页的 htmlContent。
    不经过 renderer.py，不需要预定义任何版式。

    images：用户勾选的素材（已按 owner 校验）。必须显式喂给 LLM ——
    否则它手里没有任何真实图片地址，只能凭空编 URL，渲染出来必然 404。
    """
    if llm is None:
        raise ValueError("自由风格渲染需要 LLM，当前不可用")
    plan_summary = "\n".join(
        f"- 第{i+1}节「{s.title}」（{s.kind}）：{s.text or '见items'[:60]}"
        for i, s in enumerate(plan.sections)
    )
    usable_images = [img for img in (images or []) if img.get("original_url")]
    if usable_images:
        image_hint = (
            "可用配图（只能用这些 URL，禁止编造或改写）：\n"
            + "\n".join(
                f"- {img['original_url']}（{img.get('filename', '配图')[:40]}）"
                for img in usable_images
            )
            + "\n\n"
        )
    else:
        image_hint = ""
    prompt = (
        f"你是社媒图文视觉设计师。用户要求「{style_hint}」风格的小红书卡片。\n"
        "根据下面的内容规划，为每一页输出完整 HTML（含内联 CSS）。\n\n"
        "硬约束：\n"
        "- 画布固定 1080×1440px，overflow:hidden，所有内容必须在这个画布内\n"
        "- 字体只用系统字体：'Noto Sans SC','Microsoft YaHei',sans-serif 或 serif\n"
        "- 禁止 <script>、<iframe>、onclick 等可执行内容\n"
        "- 图片只用 <img src=\"...\"> 且 src 必须是 /uploads/ 或 https:// 开头\n"
        "- 所有文字必须来自下方规划，禁止编造\n"
        "- 每页 HTML 必须是完整的独立文档（<!doctype html>...）\n\n"
        "版面硬规则（违反任意一条即判不合格，会被退回重做）：\n"
        "- 根容器 height:1440px、display:flex、flex-direction:column，内容必须纵向铺满\n"
        "  ≥75% 画高；任何一段无理由空白带 >15% 画高 = 失败\n"
        "- 禁用 margin-top:auto 把页脚推到底（这正是底部大片空白的元凶）；页脚用 padding-top 固定间距\n"
        "- 禁用 <div style=\"flex:1\"></div> 空盒子造间距，间距用 padding/margin/gap\n"
        "- 内容不够填满时：扩写描述、增加条目/子项，不要留白；宁可减少页数让内容集中\n"
        "- 大标题字重 w200-500（字越大越细），只有小字才用粗体；正文不用纯黑，用深灰/深墨\n"
        "- 禁止蓝紫科技渐变、渐变文字、玻璃拟态、emoji 当图标\n\n"
        f"{image_hint}"
        f"内容规划：\n{plan_summary}\n"
        f"完整规划 JSON：\n{plan.model_dump_json()}\n\n"
        "输出格式：每个页面用一个 ```html 代码块包裹，页面之间用空行分隔。\n"
        "在代码块前用一行写页面类型，如：封面 / 正文 / 结尾。\n"
        "示例：\n"
        "封面\n"
        "```html\n"
        "<!doctype html>...\n"
        "```\n\n"
        "正文\n"
        "```html\n"
        "<!doctype html>...\n"
        "```\n"
    )
    try:
        response = await llm.chat(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=8192,
        )
        raw = response.get("content", "") if isinstance(response, dict) else ""
        if not raw and isinstance(response, dict):
            raw = response.get("reasoning_content", "")
    except Exception as exc:
        logger.warning("自由风格渲染 LLM 调用失败: %s", exc)
        raise ValueError(f"自由风格渲染失败: {exc}") from exc

    html_blocks = _extract_complete_html_pages(raw)
    raw2 = ""
    if len(html_blocks) < len(plan.sections):
        # 2026-11-07 实测：max_tokens=8192 打满截断（completion=8192），缺页被静默丢弃，
        # 末页可能是不完整 HTML。此处必须补救而不是悄悄少页。
        missing = len(plan.sections) - len(html_blocks)
        logger.warning(
            "自由风格渲染疑似截断：规划 %d 页仅完整产出 %d 页，发起续写补 %d 页",
            len(plan.sections), len(html_blocks), missing,
        )
        missing_titles = "、".join(
            f"第{i + 1}页「{plan.sections[i].title}」" for i in range(len(html_blocks), len(plan.sections))
        )
        continuation_prompt = (
            f"你上一次的输出被截断了。内容规划共 {len(plan.sections)} 页，目前只收到 "
            f"{len(html_blocks)} 页完整 HTML，还缺：{missing_titles}。\n"
            "只输出缺失的页面：每页一个完整 ```html 代码块（<!doctype html> 到 </html>），"
            "格式与之前一致。规划 JSON：\n" + plan.model_dump_json()
        )
        try:
            response2 = await llm.chat(
                messages=[{"role": "user", "content": continuation_prompt}],
                max_tokens=8192,
            )
            raw2 = response2.get("content", "") if isinstance(response2, dict) else ""
        except Exception as exc:
            logger.warning("自由风格渲染续写调用失败: %s", exc)
            raw2 = ""
        html_blocks += _extract_complete_html_pages(raw2)

    if len(html_blocks) < len(plan.sections) and (
        _has_unterminated_fence(raw) or _has_unterminated_fence(raw2)
    ):
        raise ValueError(
            f"自由风格渲染输出被截断：规划 {len(plan.sections)} 页，"
            f"仅产出 {len(html_blocks)} 页完整 HTML，续写后仍不完整"
        )
    if len(html_blocks) < len(plan.sections):
        # 无截断痕迹（LLM 合并了过薄的页）→ 保留现有页，只告警
        logger.warning(
            "自由风格渲染产出 %d 页（规划 %d 页），无截断痕迹，按 LLM 合并页处理",
            len(html_blocks), len(plan.sections),
        )

    pages = []
    for i, html in enumerate(html_blocks):
        html = html.strip()
        if not html or len(html) < 50:
            continue
        pages.append({
            "id": f"page_{i:02}",
            "type": "cover" if i == 0 else ("ending" if i == len(html_blocks) - 1 else "content"),
            "title": "",
            "htmlContent": _fix_free_form_whitespace(_sanitize_html(html)),
        })
    if not pages:
        raise ValueError("自由风格渲染未产出任何有效页面")
    return {
        "schema_version": 1,
        "title": topic,
        "template": "free_form",
        "style_hint": style_hint,
        "pages": pages,
        "pngUrls": [],
        "htmlUrls": [],
    }


async def compose_deck(
    *, topic: str, content: str, user_id: str, llm: Any = None,
    asset_ids: list[str] | None = None,
    theme_id: ThemeId = "inkwash",
    image_layout: Literal["top", "side", "overlay"] = "top",
    recipe_id: RecipeId | None = None,
    style_hint: str = "",
) -> dict:
    """Validate owner-scoped assets, plan semantics, then deterministically build HTML from a deck.

    `recipe_id` 是用户拍板的整套骨架，作用于内容规划阶段（见 `_plan`）。
    `style_hint` 是用户自定义视觉风格描述（如"报纸""赛博朋克""手账"）。
    当 style_hint 非空且 theme_id 不在标准 THEMES 中时，自动走 LLM 直出 HTML 路径。
    """
    topic = topic.strip()[:80]
    if not topic:
        raise ValueError("卡片主题不能为空")
    selected_ids = asset_ids or []
    if len(selected_ids) > 3 or len(set(selected_ids)) != len(selected_ids):
        raise ValueError("一次最多使用 3 张不重复的上传图片")
    available = {asset["asset_id"]: asset for asset in await list_assets(user_id)} if selected_ids else {}
    if any(asset_id not in available for asset_id in selected_ids):
        raise ValueError("图片不存在或不属于当前用户，请先通过素材库上传")

    # 自动路由：style_hint 非空 → 用户明确要求自定义风格 → LLM 直出 HTML
    if style_hint:
        plan = await _plan(topic, content, llm, recipe_id)
        # 素材必须一并交给 LLM：自由风格路径不走 _build_deck，
        # 不传 images 就等于把用户勾选的图整体丢弃（且它只能编 URL）。
        selected_images = [available[asset_id] for asset_id in selected_ids]
        return await _render_free_form(plan, topic, style_hint, llm, selected_images)

    images = [available[asset_id] for asset_id in selected_ids]
    plan = await _plan(topic, content, llm, recipe_id)
    deck_id = "deck_" + uuid.uuid4().hex[:12]

    pending = list(images)
    deck = _fill_thin_pages(_build_deck(plan, topic, pending, image_layout, theme_id, deck_id))

    # 欠填修正阶梯的最后一阶：确定性手段都用尽后仍有页撑不满，
    # 就把审计缺口回灌给模型补一轮内容，补完更密才采用。
    repaired = await _repair_plan(plan, deck, topic, content, llm)
    if repaired is not None:
        pending_repaired = list(images)
        candidate = _fill_thin_pages(
            _build_deck(repaired, topic, pending_repaired, image_layout, theme_id, deck_id))
        if deck_density(candidate)["min_fill"] > deck_density(deck)["min_fill"]:
            deck, pending = candidate, pending_repaired

    draft = to_card_draft(deck)
    if pending:
        draft["unused_asset_ids"] = [asset["asset_id"] for asset in pending]
    return draft
