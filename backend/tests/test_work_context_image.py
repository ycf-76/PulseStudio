"""作品配图进入 work_context prompt 的契约测试。

背景（用户实测）：在作品详情页问「你能看见我作品上的配图吗」，Agent 回答
「图片是空的」。根因不在聊天上传通道，而是 work_context 从头到尾没有图片
字段 —— 前端 buildWorkContext 不收集、后端 build_work_context_prompt 不渲染，
模型连「有图」都不知道，自然不会去调 vl_analyze。

契约：
1. 作品有配图时，prompt 必须列出图片引用，并明确要求调 vl_analyze；
2. 作品没配图时必须显式说明「拿不到图」，不许让模型凭标题编画面；
3. data URI 不进 prompt（会撑爆预算），只收 URL / 本地路径；
4. 引用去重、限量，避免十几张图挤占正文预算。
"""

from __future__ import annotations

from app.services.work_context_prompt import (
    IMAGE_REF_LIMIT,
    build_work_context_prompt,
    collect_image_refs,
    image_analysis_lines,
)

# ── 1. 引用收集 ──────────────────────────────────────────────


def test_collects_images_and_cover():
    """场景: 作品同时有 images 和 coverUrl
    预期: 都收集，cover 不重复
    """
    refs = collect_image_refs(
        {
            "images": ["https://cdn/a.jpg", "/outputs/b.png"],
            "coverUrl": "https://cdn/a.jpg",
        }
    )
    assert refs == ["https://cdn/a.jpg", "/outputs/b.png"]


def test_dedupes_and_limits():
    """场景: 配图超过上限且含重复
    预期: 去重保序并截到 IMAGE_REF_LIMIT
    """
    urls = [f"https://cdn/{i}.jpg" for i in range(IMAGE_REF_LIMIT + 4)] + ["https://cdn/0.jpg"]
    refs = collect_image_refs({"images": urls})
    assert len(refs) == IMAGE_REF_LIMIT
    assert refs[0] == "https://cdn/0.jpg"


def test_skips_data_uri():
    """场景: images 里混了 base64 data URI
    预期: 跳过（进 prompt 会撑爆预算），只留可用引用
    """
    refs = collect_image_refs({"images": ["data:image/png;base64,AAAA", "https://cdn/a.jpg"]})
    assert refs == ["https://cdn/a.jpg"]


def test_collects_card_page_png_urls():
    """场景: 卡片作品的配图挂在 cardDraft.pages[].pngUrls
    预期: 能收集到（卡片页文案提取时这些字段是被跳过的）
    """
    refs = collect_image_refs(
        {
            "cardDraft": {
                "pages": [
                    {"pngUrls": ["/outputs/p1.png", "/outputs/p2.png"]},
                    {"imageUrl": "/outputs/p3.png"},
                ]
            }
        }
    )
    assert refs == ["/outputs/p1.png", "/outputs/p2.png", "/outputs/p3.png"]


def test_ignores_non_image_refs():
    """场景: images 里混了视频/网页链接
    预期: 只留下真正的图片引用（否则 VL 拿到 .mp4 直接报错）
    """
    refs = collect_image_refs(
        {"images": ["https://cdn/a.mp4", "https://cdn/b.html", "https://cdn/c.webp"]}
    )
    assert refs == ["https://cdn/c.webp"]


def test_keeps_signed_cdn_url_without_ext():
    """场景: CDN 链接带签名参数、路径无扩展名
    预期: 保留（小红书图片常见形态）
    """
    refs = collect_image_refs({"images": ["https://cdn.example.com/img?sign=abc&w=1080"]})
    assert refs == ["https://cdn.example.com/img?sign=abc&w=1080"]


# ── 2. prompt 渲染 ───────────────────────────────────────────


def test_prompt_lists_refs_and_requires_vl():
    """场景: 作品有 2 张配图
    预期: 列出两张引用 + 明确要求先调 vl_analyze 再看图
    """
    prompt = build_work_context_prompt(
        {
            "title": "contest 词族",
            "images": ["https://cdn/a.jpg", "/outputs/b.png"],
        }
    )
    assert "作品配图（共 2 张）" in prompt
    assert "https://cdn/a.jpg" in prompt
    assert "/outputs/b.png" in prompt
    assert "vl_analyze" in prompt
    assert "image_url" in prompt
    assert "看不见像素" in prompt


def test_prompt_without_images_says_unavailable():
    """场景: 作品没配图
    预期: 显式说明拿不到图，禁止凭标题编画面
    """
    prompt = build_work_context_prompt({"title": "只有文字的作品"})
    assert "没有可分析的图片引用" in prompt
    assert "不要凭标题编造画面" in prompt
    assert "vl_analyze" not in prompt


def test_image_lines_empty_when_only_data_uri():
    """场景: 配图只有 base64 data URI
    预期: 视同没有可用引用
    """
    lines = image_analysis_lines({"images": ["data:image/png;base64,AAAA"]})
    assert any("没有可分析的图片引用" in line for line in lines)
