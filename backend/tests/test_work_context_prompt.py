"""work_context_prompt 多页组图完整透传契约（2026-11-05 修复回归锁定）。

背景（用户实测）：内容按钮下的作品同步给 chat 后，智能体声称「只收到 1 张卡片、
第 1 页内容在 <div 处就断了」。根因不在模型上下文窗口，而在 chat_agent.py 里
两份重复的拼装逻辑对 cardDraft.pages 做了 `json.dumps(...)[:6000]` 硬截断：
单页 HTML 实测约 2 万字符（98% 是内联样式表），4 页组图序列化 85K 只剩 7%，
截断点正好落在第 1 页 CSS 中间 → 模型收到非法 JSON 残片，后 3 页从未进入字符串。

契约：
1. 组图每页的可见文案都必须出现在注入文本里，页数必须显式声明；
2. HTML 样式表不得占用注入预算（剥离后再限长）；
3. 超长单页必须显式标注截断，不允许静默丢弃；
4. 纯图片页必须显式说明「没有文字内容」，不允许伪装成有内容；
5. 正文上限从 800 提升到 2000，避免采集类作品正文被切。

全部离线，不碰真机 LLM。
"""

from __future__ import annotations

import json

import pytest

from app.services.work_context_prompt import (
    CONTENT_TEXT_LIMIT,
    MAX_PAGES,
    PAGE_TEXT_LIMIT,
    PAGES_TOTAL_LIMIT,
    build_work_context_prompt,
    strip_html_text,
    summarize_card_pages,
)


def _page(index: int, title: str, body_html: str, *, css_pad: int = 19000) -> dict:
    """构造一页真实形态的卡片：巨量内联样式 + 少量可见文案。"""
    filler_css = ".x{color:#00f0ff;background:#0a0e14;}" * (css_pad // 38)
    html = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<style>{filler_css}</style></head>"
        f"<body><article class='card'><h1>{title}</h1>{body_html}</article></body></html>"
    )
    return {
        "id": f"page_{index:02d}",
        "type": "cover" if index == 1 else "content",
        "title": title,
        "content": {"layoutId": "cover_top_image", "title": title, "htmlContent": html},
    }


def _four_page_draft() -> dict:
    return {
        "recipe": "cyber_neon",
        "theme": "dark",
        "pages": [
            _page(1, "你还在下废话级指令", "<p>决定输出质量的不是模型，是指令。</p>"),
            _page(2, "信号02 格式", "<p>规定输出结构，三段式加表情符号。</p>"),
            _page(3, "信号03 示例", "<p>给一个范文，AI 就知道往哪写。</p>"),
            _page(4, "信号04 约束", "<p>字数上限、禁用词、平台红线。</p>"),
        ],
    }


def test_strip_html_text_drops_stylesheet_and_keeps_copy():
    """剥离样式表：可见文案保留，CSS 不占预算。"""
    html = (
        "<html><head><style>.a{color:#fff}" * 200 + "</style></head>"
        "<body><h1>标题甲</h1><p>正文乙</p><script>var a=1;</script></body></html>"
    )
    text = strip_html_text(html)
    assert "标题甲" in text
    assert "正文乙" in text
    assert "color:#fff" not in text
    assert "var a=1" not in text
    assert len(text) < 100


def test_four_page_card_draft_all_pages_reach_prompt():
    """4 页组图：4 页标题与正文全部出现在注入文本中（原来只剩第 1 页残片）。"""
    wc = {
        "title": "4 个赛博信号",
        "platform": "xiaohongshu",
        "contentStatus": "draft",
        "cardDraft": _four_page_draft(),
    }
    prompt = build_work_context_prompt(wc)

    assert "共 4 页" in prompt
    for index, expected in enumerate(
        ["你还在下废话级指令", "信号02 格式", "信号03 示例", "信号04 约束"], start=1
    ):
        assert f"[第{index}页]" in prompt, f"第 {index} 页整块缺失"
        assert expected in prompt, f"第 {index} 页文案缺失: {expected}"

    # 不允许把 HTML 原样塞进来
    assert "<style>" not in prompt
    assert "htmlContent" not in prompt


def test_card_prompt_is_far_smaller_than_raw_html():
    """注入体积必须远小于原始 HTML（原来 85K 只留 6000 的残片）。"""
    draft = _four_page_draft()
    raw_size = len(json.dumps(draft["pages"], ensure_ascii=False))
    prompt = build_work_context_prompt({"cardDraft": draft, "title": "t"})

    assert raw_size > 60000, "测试数据应模拟真实大 HTML"
    assert len(prompt) < raw_size * 0.2, "注入文本应剥离样式后显著变小"


def test_long_page_is_explicitly_marked_truncated():
    """单页文案超限必须显式标注，不允许静默丢弃。"""
    long_body = "<p>" + "很长的正文内容。" * 400 + "</p>"
    blocks = summarize_card_pages([_page(1, "长页", long_body, css_pad=0)])
    assert len(blocks) == 1
    assert "本页文字已截断" in blocks[0]
    assert len(blocks[0]) <= PAGE_TEXT_LIMIT + 60


def test_image_only_page_is_reported_as_no_text():
    """纯图片页（采集/出图产物没有文案）必须如实说明，不能伪装成有内容。"""
    page = {
        "type": "cover",
        "title": "",
        "imageUrl": "/uploads/assets/a.png",
        "pngUrls": ["/uploads/assets/a.png"],
    }
    blocks = summarize_card_pages([page])
    assert "纯图片页" in blocks[0]
    assert "/uploads/assets/a.png" in blocks[0]


def test_more_than_max_pages_is_announced():
    """页数超过展开上限时必须声明，避免模型误判总页数。"""
    pages = [_page(i, f"页{i}", f"<p>内容{i}</p>", css_pad=0) for i in range(1, MAX_PAGES + 3)]
    blocks = summarize_card_pages(pages)
    assert f"仅展开前 {MAX_PAGES} 页" in blocks[-1]


def test_total_budget_guard_announces_remaining_pages():
    """总预算耗尽时必须标注从第几页起未展开。"""
    pages = [_page(i, f"页{i}", "<p>" + "文案" * 900 + "</p>", css_pad=0) for i in range(1, 9)]
    blocks = summarize_card_pages(pages)
    joined = "\n".join(blocks)
    assert "[后略]" in joined
    assert f"共 {len(pages)} 页" in joined
    assert sum(len(b) for b in blocks) <= PAGES_TOTAL_LIMIT + 200


def test_content_text_limit_raised_to_2000():
    """正文上限从 800 提升到 2000，采集类作品正文不再被切在半句。"""
    assert CONTENT_TEXT_LIMIT == 2000
    longer = "长正文。" * 400  # 1600 字，旧实现 [:800] 会截掉一半
    prompt = build_work_context_prompt({"contentText": longer, "title": "t"})
    assert longer in prompt


def test_script_text_included():
    """口播稿同样进入上下文，且标注清楚。"""
    prompt = build_work_context_prompt({"scriptText": "开场先抛钩子，再讲三点。"})
    assert "口播稿" in prompt
    assert "开场先抛钩子" in prompt


def test_video_without_local_cache_tells_agent_not_to_transcribe():
    """只有远端地址的视频：明确告知不能转写，避免空转。"""
    prompt = build_work_context_prompt({"videoUrl": "https://cdn/x.mp4"})
    assert "视频口播内容" in prompt
    assert "无法转写口播" in prompt


def test_video_with_local_cache_gives_transcribe_path():
    """有本地缓存：给出 video_transcribe 调用方式。"""
    prompt = build_work_context_prompt({"localVideoUrl": "/data/v.mp4"})
    assert "video_transcribe" in prompt


def test_draft_work_gets_write_boundary():
    """草稿允许 work_edit，已发布只读。"""
    draft_prompt = build_work_context_prompt({"contentStatus": "draft", "workId": "w1"})
    assert "可写范围（草稿）" in draft_prompt
    assert "work_edit" in draft_prompt

    read_only = build_work_context_prompt({"contentStatus": "published", "workId": "w2"})
    assert "只读边界" in read_only
    assert "禁止调用 work_edit" in read_only


@pytest.mark.parametrize(
    "wc", [{}, {"title": "只有标题"}, {"cardDraft": {}}, {"cardDraft": {"pages": []}}]
)
def test_degenerate_work_context_does_not_crash(wc):
    """空/残缺 work_context 不抛异常，仍返回可注入文本。"""
    prompt = build_work_context_prompt(wc)
    assert isinstance(prompt, str)
    assert "当前用户正在查看以下作品" in prompt
