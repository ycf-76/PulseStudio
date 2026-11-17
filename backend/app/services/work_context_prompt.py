"""作品上下文 prompt 构造（唯一实现）。

为什么要单独成模块：
- 同一段拼装逻辑曾在 chat_agent.py 里存在两份（注入 DB system 消息 + 拼
  extra_system_prompt），改一处漏一处。
- 卡片页的 content.htmlContent 是整段内联 CSS + HTML（实测单页约 2 万字符，
  其中 98% 是样式表）。此前直接 json.dumps(pages)[:6000]，4 页组图 85K 只留下
  7%，截断点正好落在第 1 页 CSS 中间 —— 模型收到的是非法 JSON 残片，只能断言
  「只收到一页」。
- 这里改成逐页提取文字（剥离 script/style/标签），每页单独限额并显式标注截断，
  模型能完整看到全部页面的文案，而不是被样式表挤掉预算。
"""

from __future__ import annotations

import html as _html
import re
from typing import Any

WORK_EDITABLE_STATUS = "draft"

CONTENT_TEXT_LIMIT = 2000
PAGE_TEXT_LIMIT = 1200
PAGES_TOTAL_LIMIT = 9000
MAX_PAGES = 30
# 作品配图清单上限：每张图一行引用，太多会挤占 prompt 预算；
# 用户追问具体某张时模型按需挑引用，不需要全量铺开。
IMAGE_REF_LIMIT = 8

_IMAGE_EXT_RE = re.compile(r"\.(?:png|jpg|jpeg|webp|bmp|gif|avif)$", re.IGNORECASE)
_NON_IMAGE_EXT_RE = re.compile(
    r"\.(?:mp4|mov|avi|mkv|webm|flv|mp3|wav|m4a|aac|html?|pdf|json)$", re.IGNORECASE
)

_STYLE_SCRIPT_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1\s*>", re.IGNORECASE | re.DOTALL)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_BLOCK_TAG_RE = re.compile(
    r"</?(?:br|p|div|section|article|li|ul|ol|h[1-6]|tr|td|th|header|footer"
    r"|figure|blockquote|span|table)\b[^>]*>",
    re.IGNORECASE,
)
_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"[ \t\u00a0]+")
_BLANK_RE = re.compile(r"\n{2,}")
_MACHINE_TOKEN_RE = re.compile(r"^[a-z0-9_\-/.]{1,40}$")

_SKIP_KEYS = {
    "htmlContent",
    "html_content",
    "htmlUrl",
    "html_url",
    "pngUrls",
    "png_urls",
    "imageUrl",
    "image_url",
    "id",
    "page_id",
    "pageId",
    "layoutId",
    "layout_id",
    "availableLayouts",
    "available_layouts",
    "density",
    "component",
    "asset_id",
    "asset_ids",
    "audioUrl",
    "audio_url",
}


def strip_html_text(raw: str) -> str:
    """把一页 HTML 压成可见文字（去掉样式表、脚本、标签、注释）。"""
    if not raw:
        return ""
    text = _COMMENT_RE.sub(" ", str(raw))
    text = _STYLE_SCRIPT_RE.sub(" ", text)
    text = _BLOCK_TAG_RE.sub("\n", text)
    text = _TAG_RE.sub(" ", text)
    text = _html.unescape(text)
    text = _SPACE_RE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.splitlines())
    text = _BLANK_RE.sub("\n", text)
    return text.strip()


def _collect_texts(value: Any, *, depth: int = 0, out: list[str] | None = None) -> list[str]:
    """递归收集结构化字段里的文字，跳过布局/资源等机器标识。"""
    if out is None:
        out = []
    if depth > 5:
        return out
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return out
        if len(text) > 80 and "<" in text:
            text = strip_html_text(text)
        if not text or _MACHINE_TOKEN_RE.match(text):
            return out
        if text not in out:
            out.append(text)
    elif isinstance(value, dict):
        for key, item in value.items():
            if str(key) in _SKIP_KEYS:
                continue
            _collect_texts(item, depth=depth + 1, out=out)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _collect_texts(item, depth=depth + 1, out=out)
    return out


def _page_media(page: dict) -> str:
    media = page.get("imageUrl") or page.get("image_url")
    if not media:
        png = page.get("pngUrls") or page.get("png_urls")
        if isinstance(png, list) and png:
            media = png[0]
    return str(media or "").strip()


def _page_block(index: int, page: Any) -> str:
    if not isinstance(page, dict):
        text = str(page or "").strip()
        return f"[第{index}页] {text[:200]}" if text else f"[第{index}页]（无内容）"

    meta: list[str] = []
    if page.get("type"):
        meta.append(f"类型={page['type']}")
    if page.get("role"):
        meta.append(f"角色={page['role']}")
    title = str(page.get("title") or "").strip()
    if title and title not in meta:
        meta.append(f"标题={title}")
    header = f"[第{index}页]" + (f" {' | '.join(meta)}" if meta else "")

    content = page.get("content")
    html_raw = ""
    if isinstance(content, dict):
        html_raw = str(content.get("htmlContent") or content.get("html_content") or "")
    if not html_raw:
        html_raw = str(page.get("htmlContent") or page.get("html_content") or "")

    if html_raw:
        body = strip_html_text(html_raw)
    else:
        body = "\n".join(_collect_texts(content) if isinstance(content, (dict, list)) else [])
    body = body.strip()

    if not body:
        media = _page_media(page)
        return f"{header}\n（纯图片页，没有文字内容）" + (f"：{media}" if media else "")

    if len(body) > PAGE_TEXT_LIMIT:
        body = body[:PAGE_TEXT_LIMIT] + f"…[本页文字已截断，原文共 {len(body)} 字]"
    return f"{header}\n{body}"


def summarize_card_pages(pages: list[Any]) -> list[str]:
    """逐页产出可读文本块，带总预算与显式省略标记。"""
    if not isinstance(pages, list) or not pages:
        return []

    blocks: list[str] = []
    used = 0
    for index, page in enumerate(pages[:MAX_PAGES], start=1):
        block = _page_block(index, page)
        if used + len(block) > PAGES_TOTAL_LIMIT:
            blocks.append(
                f"[后略] 共 {len(pages)} 页，从第 {index} 页起超出注入预算未展开，"
                "需要细节时请让用户提供该页或按页面标题追问。"
            )
            return blocks
        blocks.append(block)
        used += len(block)

    if len(pages) > MAX_PAGES:
        blocks.append(f"[后略] 共 {len(pages)} 页，仅展开前 {MAX_PAGES} 页。")
    return blocks


def _truncate_block(text: str, limit: int, label: str) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n…[{label}已截断，原文共 {len(text)} 字]"


def work_edit_boundary_lines(wc: dict) -> list[str]:
    """作品可写/只读边界说明。

    采集（collected）与已发布（published）作品是分析归因的数据源，只能读；
    只有草稿（draft）允许 Agent 原地改，且必须走 work_edit（覆盖层），
    不能碰数据库、不能覆盖原始采集快照。
    """
    status = str(wc.get("contentStatus") or "").strip()
    work_id = str(wc.get("workId") or "").strip()
    if status == WORK_EDITABLE_STATUS:
        return [
            "",
            "=== 可写范围（草稿）===",
            f"这是一条草稿（work_id: {work_id}），用户可以要求你直接修改它。",
            "修改必须调用 work_edit 工具：work_edit(title=..., content_text=..., tags=[...], images=[...], cover_img_url=...)",
            "只提交需要改的字段，未提交的字段保持原样；tags/images 是整体替换，不是追加。",
            "禁止用 bash / file_write / skill_call 去改数据库里的作品记录。",
        ]
    return [
        "",
        "=== 只读边界 ===",
        f"这是一条{'已发布' if status == 'published' else '采集'}作品（work_id: {work_id}），本次对话只做分析。",
        "禁止调用 work_edit 修改它，也绝对不要声称「已经帮你改好了原作品」。",
        "用户如果要落地修改，先说明「原作品只读」，再产出新的文案/卡片草稿让用户确认。",
    ]


def _is_image_ref(value: str) -> bool:
    """判断是不是可以交给 VL 的图片引用（URL / 本地路径）。

    data URI 直接排除：作品配图可能有十几张，base64 会把 prompt 撑爆，
    这类图只能走前端上传通道（asset_id）。
    带视频/网页等非图片扩展名的排除，避免 VL 拿到 .mp4 直接报错。
    """
    text = value.strip()
    if not text or text.startswith("data:"):
        return False
    path_part = text.split("?", 1)[0].split("#", 1)[0]
    if _NON_IMAGE_EXT_RE.search(path_part):
        return False
    if text.startswith(("http://", "https://", "/")):
        return True
    return bool(_IMAGE_EXT_RE.search(path_part))


def collect_image_refs(wc: dict) -> list[str]:
    """从作品上下文里收集配图引用，去重保序，限量。

    字段来源覆盖三种作品形态：
    - 采集作品：work_collector 写 image_urls（转成前端 images）
    - 产物/草稿：creativeArtifact 的 page_images → images，另有 coverUrl
    - 卡片作品：cardDraft.pages[].pngUrls / imageUrl
    """
    candidates: list[Any] = []
    for key in ("imageUrls", "images", "image_urls", "pageImages", "page_images"):
        value = wc.get(key)
        if isinstance(value, (list, tuple)):
            candidates.extend(value)
    for key in ("coverUrl", "cover_url", "coverImgUrl", "cover_img_url"):
        value = wc.get(key)
        if isinstance(value, str):
            candidates.append(value)
    card_draft = wc.get("cardDraft")
    if isinstance(card_draft, dict):
        pages = card_draft.get("pages")
        if isinstance(pages, list):
            for page in pages:
                if not isinstance(page, dict):
                    continue
                candidates.append(page.get("imageUrl"))
                png_urls = page.get("pngUrls")
                if isinstance(png_urls, list):
                    candidates.extend(png_urls)

    refs: list[str] = []
    for item in candidates:
        if not isinstance(item, str):
            continue
        text = item.strip()
        if not _is_image_ref(text) or text in refs:
            continue
        refs.append(text)
    return refs[:IMAGE_REF_LIMIT]


def image_analysis_lines(wc: dict) -> list[str]:
    """作品配图的可视分析通道。

    主对话模型是纯文本模型（DeepSeek），看不见像素。此前 work_context 只
    注入标题/正文/标签/指标，图片字段一个都没进 prompt —— 用户问「你能看见
    我作品上的配图吗」时，模型只能回答「图片是空的」，它连有图都不知道。
    这里把配图引用显式列出，并给出 vl_analyze 的调用形态。
    """
    refs = collect_image_refs(wc)
    if not refs:
        return [
            "",
            "=== 作品配图 ===",
            "这条作品没有可分析的图片引用（配图未采集或只有 base64）。",
            "用户问到画面/首图/排版时如实说明「拿不到这张图」，不要凭标题编造画面。",
        ]
    lines = [
        "",
        f"=== 作品配图（共 {len(refs)} 张）===",
        "你看不见像素。用户问画面内容/首图/排版/配色/字体时，必须先调用 vl_analyze 看图再回答，",
        "不要根据标题或正文猜测图上写了什么。图片引用（逐张调用，一张一次）：",
    ]
    for idx, ref in enumerate(refs, 1):
        lines.append(f"{idx}. {ref}")
    lines.extend(
        [
            "调用示例：skill_call(skill='vl_analyze', arguments={'image_url': '<上面的某个引用>'})",
            "拿到 tags/mood/composition/color_palette 后基于实际画面回答；"
            "某张图 VL 调用失败时如实说明「这张没读到」，不要用文字内容补画面。",
        ]
    )
    return lines


def video_analysis_lines(wc: dict) -> list[str]:
    """视频作品的口播内容获取方式。

    视频作品没有正文可读（contentText 为空），Agent 只能看到标题/标签/指标，
    拆解口播结构时属于凭空编造。这里明确给出转写路径：调用 video_transcribe，
    video_path 由钩子从作品本地缓存视频自动注入，LLM 不需要（也拿不到）路径。
    没有本地缓存（只有远端地址）时不能转写，必须明确告知而不是空转。
    """
    if not (wc.get("videoUrl") or wc.get("localVideoUrl")):
        return []
    if wc.get("scriptText"):
        return []
    if not wc.get("localVideoUrl"):
        return [
            "",
            "=== 视频口播内容 ===",
            "这条作品是视频，正文为空，且只有远端地址、没有本地缓存，无法转写口播。",
            "不要调用 video_transcribe（没有本地文件会直接失败）。",
            "如实说明「拿不到口播原文」，可建议用户在作品详情页点「下载视频」缓存后再来拆解；"
            "当前先基于标题/标签/指标分析，不要虚构台词。",
        ]
    return [
        "",
        "=== 视频口播内容 ===",
        "这条作品是视频，正文为空，口播内容没有随记录一起采集。",
        '需要拆解口播/文案结构时，先转写再分析：skill_call(skill="video_transcribe", arguments={})',
        "video_path 会由系统从作品的本地缓存视频自动注入，不要自己编造路径，也不要让用户手动上传。",
        "转写失败（返回空片段）时如实说明「无法获取口播内容」，基于标题/标签/指标分析，不要虚构台词。",
    ]


def build_work_context_prompt(wc: dict) -> str:
    """把 work_context 渲染成注入 LLM 的完整文本。"""
    parts: list[str] = [
        "【重要】当前用户正在查看以下作品，你必须基于此作品的实际内容来回答问题：",
        "",
        "=== 作品信息 ===",
    ]
    if wc.get("title"):
        parts.append(f"📝 标题: {wc['title']}")
    if wc.get("platform"):
        parts.append(f"📱 平台: {wc['platform']}")
    if wc.get("contentType"):
        parts.append(f"📄 内容类型: {wc['contentType']}")
    if wc.get("tags"):
        parts.append(f"🏷️ 标签: {wc['tags']}")
    if wc.get("metrics") or wc.get("performanceTier"):
        parts.append(
            f"⭐ 数据表现: {wc.get('performanceTier') or ''} {wc.get('metrics') or ''}".strip()
        )

    content_text = str(wc.get("contentText") or "").strip()
    if content_text:
        parts.append("\n📖 正文内容（必须以此为准）:")
        parts.append(_truncate_block(content_text, CONTENT_TEXT_LIMIT, "正文"))

    script_text = str(wc.get("scriptText") or "").strip()
    if script_text:
        parts.append("\n🎙️ 口播稿:")
        parts.append(_truncate_block(script_text, CONTENT_TEXT_LIMIT, "口播稿"))

    card_draft = wc.get("cardDraft")
    if isinstance(card_draft, dict):
        pages = card_draft.get("pages")
        if isinstance(pages, list) and pages:
            blocks = summarize_card_pages(pages)
            parts.append("\n🖼️ 当前视觉组图卡片（重新编排时必须基于这些内容，不得凭空编造）：")
            parts.append(f"共 {len(pages)} 页，逐页文字如下（已剥离 HTML 样式，只保留可见文案）：")
            parts.extend(blocks)
            recipe = str(card_draft.get("recipe") or "").strip()
            theme = str(card_draft.get("theme") or "").strip()
            if recipe or theme:
                parts.append(f"（配方: {recipe or '-'} / 主题: {theme or '-'}）")

    parts.extend(image_analysis_lines(wc))
    parts.extend(video_analysis_lines(wc))
    parts.extend(work_edit_boundary_lines(wc))
    return "\n".join(parts)
