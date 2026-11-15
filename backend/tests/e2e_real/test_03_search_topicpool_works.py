"""E2E 真实 API：全网搜索 / 选题池 / 我的作品（含真实采集）。

搜索走真实第三方源（HackerNews / Tavily / builtin），不 mock。
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration

REQUIRED_SEARCH_KEYS = ("platform", "content_id", "title", "summary", "author", "url")


def _pool_items(payload) -> list[dict]:
    if isinstance(payload, list):
        return payload
    for key in ("items", "list", "records", "data"):
        if isinstance(payload.get(key), list):
            return payload[key]
    return []


def test_search_hackernews_returns_real_results(client):
    """测试：HackerNews 搜索返回真实结果。

    场景：GET /api/search?keyword=AI&platform=hackernews&limit=5。
    预期：count>0 且结果字段齐全、URL 可点击。
    文档依据：backend/app/api/routers/search.py search。
    """
    resp = client.get("/api/search", params={"keyword": "AI", "platform": "hackernews", "limit": 5})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["count"] > 0, f"HackerNews 无结果: {data}"
    assert data["results"], "count 与 results 不一致"
    for item in data["results"]:
        for key in REQUIRED_SEARCH_KEYS:
            assert key in item, f"搜索结果缺字段 {key}: {item}"
        assert str(item["url"]).startswith("http"), f"URL 非法: {item['url']}"


def test_search_builtin_platform_returns_results(client):
    """测试：内置平台搜索可用。

    场景：platform=builtin。
    预期：返回结果且 platform 字段标记来源。
    文档依据：capabilities/content_search 内置源。
    """
    resp = client.get("/api/search", params={"keyword": "效率工具", "platform": "builtin", "limit": 5})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["count"] > 0, f"内置源无结果: {data}"


def test_search_tavily_shape_and_fallback_flags(client):
    """测试：Tavily 搜索返回结构完整（允许 0 结果，但必须标注平台与 fallback）。

    场景：platform=tavily。
    预期：200 + searched_platforms 含 tavily + fallback_used 布尔值。
    文档依据：backend/app/api/routers/search.py SearchResponse。
    """
    resp = client.get("/api/search", params={"keyword": "notion ai", "platform": "tavily", "limit": 5})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert "tavily" in (data.get("searched_platforms") or []), f"未标注实际搜索平台: {data}"
    assert isinstance(data.get("fallback_used"), bool)


def test_search_xiaohongshu_degrades_gracefully(client):
    """测试：小红书搜索在扩展离线时优雅降级（不 500）。

    场景：platform=xiaohongshu。
    预期：200 有结果，或 503 且提示浏览器扩展未连接。
    文档依据：backend/app/api/routers/search.py L77-82。
    """
    resp = client.get("/api/search", params={"keyword": "番茄工作法", "platform": "xiaohongshu", "limit": 5})
    assert resp.status_code in (200, 503), f"小红书搜索异常: {resp.status_code} {resp.text[:200]}"
    if resp.status_code == 503:
        assert "扩展" in resp.json().get("detail", ""), f"降级提示不明确: {resp.text[:200]}"


def test_search_platforms_list(client):
    """测试：平台选择器数据源完整。

    场景：GET /api/search/platforms。
    预期：非空列表，每项含平台标识与展示名。
    文档依据：backend/app/api/routers/search.py list_platforms。
    """
    resp = client.get("/api/search/platforms")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert isinstance(data, list) and data, "平台列表为空"
    for item in data:
        assert item.get("name") or item.get("platform"), f"平台项缺标识: {item}"


def test_search_requires_keyword(client):
    """测试：缺少 keyword 时参数校验生效。

    场景：GET /api/search 不带 keyword。
    预期：422。
    文档依据：search.py keyword: Query(...) 必填。
    """
    resp = client.get("/api/search")
    assert resp.status_code == 422, f"缺少必填参数未拦截: {resp.status_code}"


def test_topic_pool_list_stats_recommended(client):
    """测试：选题池列表 / 统计 / 推荐三类读取接口。

    场景：依次读取三个只读接口。
    预期：列表分页结构、统计含总数、推荐为列表。
    文档依据：backend/app/api/routers/topic_pool.py L28-140。
    """
    listed = client.get("/api/topic-pool", params={"page": 1, "size": 10})
    assert listed.status_code == 200, listed.text
    payload = listed.json()["data"]
    assert _pool_items(payload), f"选题池分页结构异常: {list(payload)[:5]}"

    stats = client.get("/api/topic-pool/stats")
    assert stats.status_code == 200, stats.text
    assert isinstance(stats.json()["data"], dict) and stats.json()["data"], "统计为空"

    rec = client.get("/api/topic-pool/recommended")
    assert rec.status_code == 200, rec.text
    assert isinstance(rec.json()["data"], (list, dict)), f"推荐结构异常: {type(rec.json()['data'])}"


def test_topic_pool_fetch_hackernews_then_detail_and_summary(client):
    """测试：主动抓取入库 → 详情读取 → AI 摘要 → 收藏切换。

    场景：POST /api/topic-pool/fetch（hackernews，真实抓取）后对新条目操作。
    预期：抓取有新增；详情可读；AI 摘要非空；收藏状态可翻转且可复原。
    文档依据：backend/app/api/routers/topic_pool.py L149-280。
    """
    fetched = client.post(
        "/api/topic-pool/fetch",
        json={"keyword": "AI agent", "platform": "hackernews", "limit": 5},
    )
    assert fetched.status_code == 200, fetched.text
    data = fetched.json()["data"]
    assert data.get("success", True) is True, f"抓取失败: {data}"

    listed = client.get("/api/topic-pool", params={"platform": "hackernews", "size": 5})
    items = _pool_items(listed.json()["data"])
    assert items, "抓取后选题池仍为空"
    item_id = items[0]["id"]

    detail = client.get(f"/api/topic-pool/{item_id}")
    assert detail.status_code == 200, detail.text
    detail_payload = detail.json()["data"]
    assert "detail" in detail_payload and "related" in detail_payload, (
        f"详情响应结构异常: {list(detail_payload)[:6]}"
    )
    assert detail_payload["detail"].get("title"), "详情缺少标题"
    assert isinstance(detail_payload["related"], list), "关联推荐不是列表"

    summary = client.post(f"/api/topic-pool/{item_id}/ai-summary")
    assert summary.status_code == 200, summary.text
    summary_payload = summary.json()["data"]
    assert summary_payload.get("success") is True, f"AI 摘要生成失败: {summary_payload}"
    assert str(summary_payload.get("ai_summary") or "").strip(), f"AI 摘要为空: {summary_payload}"

    original = bool(items[0].get("favorited") or items[0].get("is_favorited"))
    toggled = client.post(f"/api/topic-pool/{item_id}/favorite")
    assert toggled.status_code == 200, toggled.text
    restored = client.post(f"/api/topic-pool/{item_id}/favorite")
    assert restored.status_code == 200, restored.text
    final = client.get(f"/api/topic-pool/{item_id}").json()["data"]["detail"]
    final_flag = bool(final.get("favorited") or final.get("is_favorited"))
    assert final_flag == original, f"收藏两次未复原: {original} -> {final_flag}"


def test_topic_pool_delete_missing_item_is_not_500(client):
    """测试：删除不存在的选题不会 500。

    场景：DELETE /api/topic-pool/not-exist-id。
    预期：404 或 200（带 deleted=False），但绝不是 500。
    文档依据：topic_pool.py L279。
    """
    resp = client.delete("/api/topic-pool/not-exist-id")
    assert resp.status_code in (200, 404), f"删除不存在条目返回 {resp.status_code}: {resp.text[:200]}"


def test_my_works_list_facets_and_analysis(client):
    """测试：我的作品列表 / 筛选项 / 分析报告 / 归因读取。

    场景：四个只读接口。
    预期：列表分页结构完整；facets 含平台维度；分析报告结构可解析。
    文档依据：backend/app/api/routers/my_works.py L218-345 / L519。
    """
    listed = client.get("/api/my-works", params={"page": 1, "page_size": 10, "origin": "all"})
    assert listed.status_code == 200, listed.text
    payload = listed.json()["data"]
    assert _pool_items(payload), f"作品列表结构异常: {list(payload)[:5]}"

    facets = client.get("/api/my-works/facets")
    assert facets.status_code == 200, facets.text
    assert isinstance(facets.json()["data"], dict)

    report = client.get("/api/my-works/analysis-report")
    assert report.status_code == 200, report.text
    assert isinstance(report.json()["data"], dict) and report.json()["data"], "分析报告为空"

    attribution = client.get("/api/my-works/attribution")
    assert attribution.status_code == 200, attribution.text
    assert isinstance(attribution.json()["data"], (dict, list))


def test_my_works_draft_lifecycle(client):
    """测试：草稿创建 → 内容编辑 → 状态流转 → 详情 → 删除。

    场景：POST /api/my-works/draft 起步，逐步写入并回读。
    预期：每步 200；编辑后字段生效；删除后详情 404。
    文档依据：backend/app/api/routers/my_works.py L742-836。
    """
    created = client.post("/api/my-works/draft", json={"title": "E2E 草稿", "topic": "效率"})
    assert created.status_code == 200, created.text
    record_id = created.json()["data"]["id"]

    edited = client.patch(
        f"/api/my-works/{record_id}/content",
        json={
            "title": "E2E 草稿（已编辑）",
            "content_text": "番茄工作法实测：25 分钟专注 + 5 分钟休息。",
            "tags": ["效率", "番茄工作法"],
        },
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["data"]["title"] == "E2E 草稿（已编辑）"

    status = client.patch(f"/api/my-works/{record_id}/status", json={"content_status": "published"})
    assert status.status_code == 200, status.text
    assert status.json()["data"]["content_status"] == "published"

    detail = client.get(f"/api/my-works/{record_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["content_status"] == "published"

    bad_status = client.patch(f"/api/my-works/{record_id}/status", json={"content_status": "unknown"})
    assert bad_status.status_code == 422, f"非法状态未拦截: {bad_status.status_code}"

    deleted = client.delete(f"/api/my-works/{record_id}")
    assert deleted.status_code == 200, deleted.text
    after = client.get(f"/api/my-works/{record_id}")
    assert after.status_code == 404, f"删除后仍可读取: {after.status_code}"


def test_my_works_csv_import(client):
    """测试：CSV 批量导入真实落库。

    场景：导入 2 条创作者中心数据。
    预期：imported=2、errors=0。
    文档依据：backend/app/api/routers/my_works.py csv_import。
    """
    resp = client.post(
        "/api/my-works/csv-import",
        json={
            "platform": "xiaohongshu",
            "records": [
                {"note_id": "e2e-csv-1", "title": "E2E CSV 1", "likes": 12, "collects": 3, "comments": 2, "shares": 1},
                {"note_id": "e2e-csv-2", "title": "E2E CSV 2", "likes": 7, "collects": 1, "comments": 0, "shares": 0},
            ],
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["imported"] == 2, f"导入数量不符: {data}"
    assert data["errors"] == 0, f"导入出现错误行: {data}"


def test_collect_real_bilibili_url(client):
    """测试：真实链接采集（B 站）落库并返回作品条目。

    场景：POST /api/my-works/collect 采集一个真实 B 站视频链接。
    预期：200 且 success=True，返回记录含 id/标题。
    文档依据：backend/app/api/routers/my_works.py collect L576。
    """
    resp = client.post(
        "/api/my-works/collect",
        json={"note_url": "https://www.bilibili.com/video/BV1wAYS6cEWu", "platform": "bilibili"},
    )
    assert resp.status_code == 200, f"采集失败: {resp.status_code} {resp.text[:300]}"
    body = resp.json()
    assert body.get("success") is True, f"采集返回失败: {body}"
    record = body["data"].get("record") or body["data"]
    assert record.get("id"), f"采集未返回作品 id: {body['data']}"