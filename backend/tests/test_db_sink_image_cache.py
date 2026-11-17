"""同步入库的图片处理回归测试。

覆盖问题：我的空间里的图文在同步后依旧过期——同步只更新元数据，
图集仍是小红书 CDN 原链（签名约 2 天过期），且卡片级数据只有一张封面，
会把已有的多图详情降级成单图。

文档依据：docs/FEATURE_平台账号管理与作品数据回收.md#7.2（图片缓存对齐 my_works.py）
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from capabilities.scrapling.spiders import db_sink  # noqa: E402

# ═══════════════════════════════════════════════════════════════════
# 1. _pick_images：单张封面不得覆盖已有图集
# ═══════════════════════════════════════════════════════════════════


def test_pick_images_keeps_old_gallery_when_new_has_only_cover():
    """测试: _pick_images
    场景: 老记录有 6 张本地详情图，本次同步只带回封面一张（详情页没取到）
    预期: 保留旧图集，不降级成单图
    """
    old = [f"/uploads/topic_images/old_{i}.jpg" for i in range(6)]
    assert db_sink._pick_images(["/uploads/topic_images/cover.jpg"], old) == old


def test_pick_images_keeps_expired_gallery_so_it_can_still_be_refreshed():
    """测试: _pick_images
    场景: 老记录是 6 张已过期的小红书 CDN 外链，本次同步只带回一张本地封面
    预期: 保留旧图集——降级成单图会让这条笔记被误判"图集已本地化"，
          从此不再进入详情页刷新候选，过期图文就永远修不回来
    """
    old = [f"https://cdn/old_{i}.jpg" for i in range(6)]
    assert db_sink._pick_images(["/uploads/topic_images/cover.jpg"], old) == old


def test_pick_images_keeps_single_expired_url_until_detail_page_fills_it():
    """测试: _pick_images
    场景: 库里的真实情况——老记录只有一张过期 CDN 外链，本次只带回一张本地封面
    预期: 保留外链。否则 32 条单图外链会被"就地转成本地"，全部退出刷新候选，
          图集永远补不齐（库里当前 32/55 条就是被这条规则坑出来的）
    """
    old = ["https://sns-img.xhscdn.com/notes_pre_post/old.webp"]
    new = ["/uploads/topic_images/cover.webp"]
    assert db_sink._pick_images(new, old) == old


def test_pick_images_downgrades_to_cover_after_giving_up():
    """测试: _pick_images
    场景: 详情页已重试到上限（放弃自动修复），本次仍只带回一张本地封面
    预期: allow_single_downgrade=True 时接受单图，不再保留一堆 403 外链
    """
    old = [f"https://cdn/old_{i}.jpg" for i in range(6)]
    new = ["/uploads/topic_images/cover.jpg"]
    assert db_sink._pick_images(new, old, allow_single_downgrade=True) == new


def test_pick_images_accepts_single_image_from_detail_page():
    """测试: _pick_images
    场景: 详情页 imageList 只有一张（笔记本身就是单图），旧值是过期的单图外链
    预期: 用新值——详情页的单图是完整图集，不能和"主页卡片封面兜底"混为一谈，
          否则单图笔记的过期外链永远换不掉
    """
    old = ["https://sns-img.xhscdn.com/notes_pre_post/old.webp"]
    new = ["/uploads/topic_images/n1_0.webp"]
    assert db_sink._pick_images(new, old, allow_single_downgrade=True) == new


def test_pick_images_refreshes_single_local_cover():
    """测试: _pick_images
    场景: 老记录本来就是一张本地封面（真实单图笔记），新封面也是本地文件
    预期: 用新值——单图笔记的封面更新不该被拦截
    """
    old = ["/uploads/topic_images/old_cover.jpg"]
    new = ["/uploads/topic_images/new_cover.jpg"]
    assert db_sink._pick_images(new, old) == new


def test_pick_images_keeps_local_gallery_when_new_is_remote():
    """测试: _pick_images
    场景: 老记录是本地图集，本次同步缓存失败带回外链
    预期: 保留本地图集（本地文件永远不会 403）
    """
    old = ["/uploads/topic_images/n1_0.jpg", "/uploads/topic_images/n1_1.jpg"]
    new = ["https://cdn/new_0.jpg", "https://cdn/new_1.jpg", "https://cdn/new_2.jpg"]
    assert db_sink._pick_images(new, old) == old


def test_pick_images_replaces_when_new_gallery_is_complete():
    """测试: _pick_images
    场景: 本次同步取到完整图集（张数不少于旧图集）
    预期: 用新图集整体替换，旧 CDN 链接被本地路径覆盖
    """
    old = ["https://cdn/old_0.jpg", "https://cdn/old_1.jpg"]
    new = ["/uploads/topic_images/n1_0.jpg", "/uploads/topic_images/n1_1.jpg"]
    assert db_sink._pick_images(new, old) == new


def test_pick_images_keeps_old_when_new_is_empty():
    """测试: _pick_images
    场景: 本次同步没有带回任何图片
    预期: 保留旧值
    """
    old = ["/uploads/topic_images/n1_0.jpg"]
    assert db_sink._pick_images([], old) == old


def test_pick_images_accepts_missing_old_value():
    """测试: _pick_images
    场景: 新建记录（旧值为 None），只有一张封面
    预期: 直接用新值，不报错
    """
    assert db_sink._pick_images(["/uploads/topic_images/n1_0.jpg"], None) == [
        "/uploads/topic_images/n1_0.jpg"
    ]


# ═══════════════════════════════════════════════════════════════════
# 2. _pick_cover：外链封面不得覆盖已缓存的本地封面
# ═══════════════════════════════════════════════════════════════════


def test_pick_cover_prefers_local_value():
    """测试: _pick_cover
    场景: 旧封面是本地文件，新封面是外链（缓存失败）
    预期: 保留本地封面
    """
    old = "/uploads/topic_images/n1_cover.jpg"
    assert db_sink._pick_cover("https://cdn/cover.jpg", old) == old


def test_pick_cover_accepts_new_local_value():
    """测试: _pick_cover
    场景: 新封面已成功缓存为本地路径
    预期: 用新值，旧 CDN 链接被替换
    """
    new = "/uploads/topic_images/n1_cover_v2.jpg"
    assert db_sink._pick_cover(new, "https://cdn/old_cover.jpg") == new


def test_pick_cover_keeps_old_when_new_is_empty():
    """测试: _pick_cover
    场景: 本次同步没有带回封面
    预期: 保留旧值
    """
    assert db_sink._pick_cover("", "https://cdn/old_cover.jpg") == "https://cdn/old_cover.jpg"


# ═══════════════════════════════════════════════════════════════════
# 3. _cache_detail_images：外链本地化，本地路径原样返回
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_cache_detail_images_skips_download_for_local_paths():
    """测试: _cache_detail_images
    场景: 图集已经是 /uploads 本地路径
    预期: 原样返回，不触发网络下载
    """
    local = ["/uploads/topic_images/n1_0.jpg", "/uploads/topic_images/n1_1.jpg"]
    assert await db_sink._cache_detail_images("n1", local) == local


@pytest.mark.asyncio
async def test_cache_detail_images_returns_input_for_empty_list():
    """测试: _cache_detail_images
    场景: 图集为空
    预期: 返回空列表，不报错
    """
    assert await db_sink._cache_detail_images("n1", []) == []
