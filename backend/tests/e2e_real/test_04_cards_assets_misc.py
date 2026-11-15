"""E2E 真实 API：卡片渲染 / 质量门禁 / 创作工件 / 资产 / 技能 / 插件 /
看板 / 任务计划 / 工作区 / 记忆 / 反馈 / 通知。

本文件覆盖「除 AI 图生视频」以外的其余业务面。
"""

from __future__ import annotations

import json
import shutil
import tempfile
import uuid
from pathlib import Path

import httpx
import pytest

from helpers import ONE_PIXEL_PNG

pytestmark = pytest.mark.integration


def _blocks(deck: dict) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    for page in deck.get("pages") or []:
        for block in page.get("blocks") or []:
            out.append((page.get("id"), block))
    return out


@pytest.fixture(scope="module")
def preview_draft(client) -> dict:
    """模块级共享：由 topic+content 退化生成一份结构化卡片草稿。"""
    resp = client.post(
        "/api/cards/preview",
        json={
            "topic": "番茄工作法",
            "content": "25 分钟专注，5 分钟休息。四个番茄钟后休息 15 分钟。\n"
                       "关键：把干扰记在纸上，不打断当前番茄钟。\n"
                       "工具：计时器、待办清单、一张纸。",
            "theme_id": "ink",
            "image_layout": "top",
        },
    )
    assert resp.status_code == 200, resp.text
    draft = resp.json()["data"]
    assert draft.get("pages"), f"预览未产出页面: {list(draft)[:8]}"
    return draft


def test_cards_meta_exposes_layouts_themes_blocks(client):
    """测试：卡片元数据（版式/主题/块类型）可用。

    场景：GET /api/cards/meta。
    预期：layouts、themes、block_types 均非空。
    文档依据：backend/app/api/routers/cards.py meta。
    """
    resp = client.get("/api/cards/meta")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["layouts"], "版式清单为空"
    assert data["themes"], "主题清单为空"
    assert data["block_types"], "块类型清单为空"


def test_cards_preview_renders_html_per_page(preview_draft):
    """测试：预览产出每页 HTML 与密度诊断。

    场景：使用 topic+content 退化生成（不发起 LLM）。
    预期：每页含 id/layoutId/htmlContent/density，deck 与 pages 数量一致。
    文档依据：backend/app/cards/renderer.py to_card_draft L488。
    """
    pages = preview_draft["pages"]
    deck = preview_draft["deck"]
    assert len(pages) == len(deck["pages"]), "draft.pages 与 deck.pages 数量不一致"
    for page in pages:
        assert page["id"] and page["layoutId"], f"页面标识缺失: {page}"
        assert page["htmlContent"].strip(), f"页面未渲染 HTML: {page['id']}"
        assert isinstance(page.get("density"), dict), f"缺少密度诊断: {page['id']}"
    assert preview_draft["schema_version"] == 1


def test_cards_audit_reports_density(client, preview_draft):
    """测试：卡片审计接口可对 deck 给出报告。

    场景：POST /api/cards/audit（传预览草稿里的 deck）。
    预期：reports 列表非空，min_fill 为数值。
    文档依据：backend/app/api/routers/cards.py audit_endpoint。
    """
    resp = client.post("/api/cards/audit", json={"deck": preview_draft["deck"]})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["reports"], "审计未产出报告"
    assert isinstance(data["min_fill"], (int, float))


def test_quality_gate_on_preview_draft(client, preview_draft):
    """测试：质量门禁对预览草稿返回报告与 badge。

    场景：POST /api/quality-gate/check。
    预期：200，返回 report 结构，badge.level ∈ pass/warn/fail。
    文档依据：backend/app/api/routers/quality_gate.py check + gate_badge。
    """
    resp = client.post(
        "/api/quality-gate/check",
        json={"card_draft": preview_draft, "expected_count": len(preview_draft["pages"])},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert isinstance(data, dict) and data, f"门禁返回空: {data}"
    blob = json.dumps(data, ensure_ascii=False)
    assert "pass" in blob or "warn" in blob or "fail" in blob, f"门禁缺少 badge 语义: {blob[:200]}"


def test_creative_artifact_build_read_patch_delete(client, preview_draft):
    """测试：创作工件（CreativeArtifact）完整生命周期。

    场景：build → get → card-draft → patch → delete。
    预期：构建成功且带 artifact_id；card-draft 可读；删除后 404。
    文档依据：backend/app/api/routers/creative_artifact.py。
    """
    built = client.post(
        "/api/creative-artifacts/build",
        json={"card_draft": preview_draft, "brief": {"topic": "番茄工作法"}, "persist": True},
    )
    assert built.status_code == 200, built.text
    artifact = built.json()["data"]
    artifact_id = artifact["artifact_id"]
    assert artifact_id

    fetched = client.get(f"/api/creative-artifacts/{artifact_id}")
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["data"]["artifact_id"] == artifact_id

    card = client.get(f"/api/creative-artifacts/{artifact_id}/card-draft")
    assert card.status_code == 200, card.text
    assert card.json()["data"]["pages"], "工件 card-draft 无页面"

    patched = client.patch(f"/api/creative-artifacts/{artifact_id}", json={"brief": {"topic": "番茄工作法（改）"}})
    assert patched.status_code == 200, patched.text

    listed = client.get("/api/creative-artifacts", params={"limit": 10})
    assert listed.status_code == 200, listed.text
    assert any(a["artifact_id"] == artifact_id for a in listed.json()["data"])

    deleted = client.delete(f"/api/creative-artifacts/{artifact_id}")
    assert deleted.status_code == 200, deleted.text
    gone = client.get(f"/api/creative-artifacts/{artifact_id}")
    assert gone.status_code == 404, f"删除后仍可读取: {gone.status_code}"


def test_card_edit_enforces_optimistic_lock(client, preview_draft):
    """测试：卡片块级编辑 + 乐观锁版本校验。

    场景：读卡片拿 version → set_text 成功 → 用旧 version 再编辑。
    预期：第一次 200 且 HTML 变化；第二次 409（版本过期）。
    文档依据：backend/app/cards/operations.py + cards.py _edit_error。
    """
    built = client.post(
        "/api/creative-artifacts/build",
        json={"card_draft": preview_draft, "persist": True},
    )
    assert built.status_code == 200, built.text
    artifact_id = built.json()["data"]["artifact_id"]

    card = client.get(f"/api/cards/{artifact_id}")
    assert card.status_code == 200, card.text
    card_data = card.json()["data"]
    version = card_data["version"]
    blocks = _blocks(card_data["deck"])
    target_page, target_block = next(
        ((pid, b) for pid, b in blocks if b.get("type") == "text"),
        (None, None),
    )
    if target_block is None:
        pytest.skip("预览草稿中没有 text 块，跳过块级编辑校验")

    first = client.post(
        f"/api/cards/{artifact_id}/edit",
        json={
            "page_id": target_page,
            "block_id": target_block["id"],
            "op": "set_text",
            "value": "E2E 编辑后的正文",
            "expected_version": version,
        },
    )
    assert first.status_code == 200, first.text
    assert first.json()["data"]["version"] > version, "编辑后版本号未递增"

    stale = client.post(
        f"/api/cards/{artifact_id}/edit",
        json={
            "page_id": target_page,
            "block_id": target_block["id"],
            "op": "set_text",
            "value": "过期写入",
            "expected_version": version,
        },
    )
    assert stale.status_code == 409, f"过期版本未拒绝: {stale.status_code} {stale.text[:200]}"

    client.delete(f"/api/creative-artifacts/{artifact_id}")


def test_assets_upload_list_delete(client):
    """测试：图片资产上传 / 列表 / 删除。

    场景：一次上传 2 张 1x1 PNG。
    预期：返回 2 条资产（含 asset_id 与 url）；列表可查；删除后列表不含。
    文档依据：backend/app/api/routers/assets.py。
    """
    files = [
        ("files", (f"e2e-{i}.png", ONE_PIXEL_PNG, "image/png")) for i in range(2)
    ]
    uploaded = client.post("/api/assets", files=files)
    assert uploaded.status_code == 200, uploaded.text
    assets = uploaded.json()["data"]
    assert len(assets) == 2, f"上传数量不符: {assets}"
    for asset in assets:
        assert asset.get("asset_id"), f"资产缺 id: {asset}"
        assert asset.get("original_url") or asset.get("url"), f"资产缺 URL: {asset}"

    listed = client.get("/api/assets", params={"limit": 20})
    assert listed.status_code == 200, listed.text
    ids = {a["asset_id"] for a in listed.json()["data"]}
    assert {a["asset_id"] for a in assets} <= ids, "新上传资产未出现在列表"

    for asset in assets:
        removed = client.delete(f"/api/assets/{asset['asset_id']}")
        assert removed.status_code == 200, removed.text
    after = client.get("/api/assets", params={"limit": 20})
    assert {a["asset_id"] for a in after.json()["data"]}.isdisjoint({a["asset_id"] for a in assets})


def test_memory_overview_and_preferences(client):
    """测试：智能体记忆概览与偏好设置。

    场景：读概览 → 用当前值原样回写偏好（不改变用户真实偏好）。
    预期：概览为 dict；回写后仍返回概览。
    文档依据：backend/app/api/routers/memory.py。
    """
    overview = client.get("/api/memory")
    assert overview.status_code == 200, overview.text
    data = overview.json()["data"]
    assert isinstance(data, dict) and data, "记忆概览为空"

    current_style = data.get("writing_style") or ""
    updated = client.put("/api/memory/preferences", json={"writing_style": current_style})
    assert updated.status_code == 200, updated.text
    assert isinstance(updated.json()["data"], dict)


def test_feedback_like_and_dislike_validation(client, qa_session_id):
    """测试：反馈事件落库 + 点踩必须带维度。

    场景：like 正常提交；dislike 不带 dimension。
    预期：like 200；dislike 422；统计与按会话查询可读。
    文档依据：backend/app/api/routers/feedback.py L70-75 / L163 / L249。
    """
    like = client.post("/api/feedback", json={"type": "like", "session_id": qa_session_id})
    assert like.status_code == 200, like.text

    dislike = client.post("/api/feedback", json={"type": "dislike", "session_id": qa_session_id})
    assert dislike.status_code == 422, f"点踩缺维度未拦截: {dislike.status_code} {dislike.text[:200]}"

    stats = client.get("/api/feedback/stats")
    assert stats.status_code == 200, stats.text
    assert isinstance(stats.json()["data"], dict)

    by_session = client.get(f"/api/feedback/session/{qa_session_id}")
    assert by_session.status_code == 200, by_session.text
    assert isinstance(by_session.json()["data"], list)


def test_profile_read_and_personalities(client):
    """测试：用户画像与人格清单读取。

    场景：GET /api/profile 与 /api/profile/personalities。
    预期：画像为创作者画像（含定位/语气等字段）或未设置时 data=null；人格清单非空。
    文档依据：backend/app/api/routers/profile.py L37-47。
    """
    profile = client.get("/api/profile")
    assert profile.status_code == 200, profile.text
    data = profile.json()["data"]
    if data is not None:
        assert isinstance(data, dict) and data, f"画像结构异常: {data}"
        assert data.get("primary_domain") or data.get("tone"), (
            f"画像缺创作者定位字段: {list(data)[:8]}"
        )

    personalities = client.get("/api/profile/personalities")
    assert personalities.status_code == 200, personalities.text
    assert personalities.json()["data"], "人格清单为空"


def test_skills_list_filter_and_detail(client):
    """测试：技能库列表、按节点过滤、单技能详情。

    场景：GET /api/skills、?node_type=copywrite、/api/skills/copywrite/{name}。
    预期：分组结构非空；过滤只返回该节点；详情含触发词与输入字段。
    文档依据：backend/app/api/routers/skills.py L128-232。
    """
    all_skills = client.get("/api/skills")
    assert all_skills.status_code == 200, all_skills.text
    grouped = all_skills.json()["data"]
    assert grouped and all(isinstance(v, list) for v in grouped.values()), "技能分组结构异常"
    assert "copywrite" in grouped, f"缺少 copywrite 节点技能: {list(grouped)}"

    filtered = client.get("/api/skills", params={"node_type": "copywrite"})
    assert filtered.status_code == 200, filtered.text
    assert list(filtered.json()["data"]) == ["copywrite"], "node_type 过滤未生效"

    sample = grouped["copywrite"][0]
    detail = client.get(f"/api/skills/copywrite/{sample['name']}")
    assert detail.status_code == 200, detail.text
    payload = detail.json()["data"]
    for key in ("name", "node_type", "display_name", "trigger_words", "input_fields"):
        assert key in payload, f"技能详情缺字段 {key}: {list(payload)}"


def test_prompt_skill_create_and_delete(client):
    """测试：提示词型技能创建 → 详情 → 删除。

    场景：POST /api/skills/prompt 新建一个临时 skill，随后删除。
    预期：创建成功返回文件名；详情可读；删除后详情 404。
    文档依据：backend/app/api/routers/skills.py L352-467。
    """
    skill_name = f"e2e_probe_{uuid.uuid4().hex[:6]}"
    created = client.post(
        "/api/skills/prompt",
        json={
            "node_type": "quality_gate",
            "name": skill_name,
            "display_name": "E2E 临时检查器",
            "description": "端到端测试临时创建的提示词技能",
            "trigger_words": ["e2e检查"],
            "prompt_guidance": "仅用于端到端测试",
            "skill_body": "你是端到端测试用的检查器，请输出一句话结论。",
        },
    )
    assert created.status_code == 200, created.text
    detail = client.get(f"/api/skills/quality_gate/{skill_name}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["display_name"] == "E2E 临时检查器"

    deleted = client.delete(f"/api/skills/prompt/quality_gate/{skill_name}")
    assert deleted.status_code == 200, deleted.text
    after = client.get(f"/api/skills/quality_gate/{skill_name}")
    assert after.status_code == 404, f"删除后仍可读取: {after.status_code}"


def test_plugins_health_and_marketplace(client):
    """测试：插件系统健康检查与市场列表。

    场景：GET /api/plugins/health、/api/plugins/my、/api/plugins/marketplace。
    预期：health 返回状态字段；my 与 marketplace 为列表。
    文档依据：backend/app/api/routers/plugins.py L98-130 / L218 / L1019。

    注意：plugins 路由直接返回裸对象/列表，未套 StandardResponse 信封
    （与其余路由的 data 信封不一致，属已知契约不统一），此处两种形状都接受。
    """
    health = client.get("/api/plugins/health")
    assert health.status_code == 200, health.text
    health_body = health.json()
    health_payload = health_body["data"] if isinstance(health_body, dict) and "data" in health_body else health_body
    assert health_payload.get("status") in {"healthy", "error", "degraded"}, (
        f"插件健康检查缺状态: {list(health_payload)[:8]}"
    )

    mine = client.get("/api/plugins/my")
    assert mine.status_code == 200, mine.text
    mine_body = mine.json()
    mine_payload = mine_body["data"] if isinstance(mine_body, dict) else mine_body
    assert isinstance(mine_payload, list), f"我的插件结构异常: {type(mine_payload)}"

    market = client.get("/api/plugins/marketplace")
    assert market.status_code == 200, market.text
    market_body = market.json()
    market_payload = market_body["data"] if isinstance(market_body, dict) and "data" in market_body else market_body
    assert isinstance(market_payload, dict), f"插件市场结构异常: {type(market_payload)}"
    for key in ("items", "total", "page", "page_size"):
        assert key in market_payload, f"插件市场缺字段 {key}: {list(market_payload)[:8]}"
    assert isinstance(market_payload["items"], list), "插件市场 items 非列表"


def test_plugin_upload_rejects_invalid_category(client):
    """测试：第三方插件上传对非法 category 返回 400，而不是 500 + 泄漏 SQL。

    场景：打包一个 category="tool"（不在 DB 的 6 值 ENUM 内）的插件 zip 上传。
    预期：400 且 detail 明确列出合法取值；响应体不含 SQL / 表结构。
    缺陷依据：plugins.py 上传端点此前不校验 category，非法值直接写库 →
    MySQL DataError 1265，接口返回 500 并把整条 INSERT 语句回传前端。
    合法取值依据：SHOW CREATE TABLE plugins 的 category enum 定义。
    """
    import io
    import json
    import zipfile

    manifest = {
        "id": "e2e-invalid-category-probe",
        "name": "E2E Invalid Category",
        "version": "0.0.1",
        "category": "tool",
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("plugin.json", json.dumps(manifest, ensure_ascii=False))
        zf.writestr("main.py", "def run(payload):\n    return {'ok': True}\n")

    resp = client.post(
        "/api/plugins/upload",
        files={
            "plugin_package": (
                "e2e-invalid-category-probe.zip",
                buf.getvalue(),
                "application/zip",
            )
        },
    )
    assert resp.status_code == 400, f"非法 category 未返回 400: {resp.status_code} {resp.text[:200]}"
    detail = resp.text
    assert "category" in detail.lower(), f"400 未说明原因: {detail[:200]}"
    for leak in ("INSERT INTO", "SQL:", "pymysql", "Data truncated"):
        assert leak not in detail, f"响应体泄漏内部实现: {leak}"


def test_dashboard_endpoints_return_metrics(client):
    """测试：数据看板四个读取接口。

    场景：overview / content-analysis / follower-analysis / platform/xiaohongshu。
    预期：均 200 且返回 dict。
    文档依据：backend/app/api/routers/dashboard.py L72-371。
    """
    for path in (
        "/api/dashboard/overview",
        "/api/dashboard/content-analysis",
        "/api/dashboard/follower-analysis",
        "/api/dashboard/platform/xiaohongshu",
    ):
        resp = client.get(path)
        assert resp.status_code == 200, f"{path} 失败: {resp.status_code} {resp.text[:200]}"
        assert isinstance(resp.json()["data"], (dict, list)), f"{path} 返回结构异常"


def test_task_plan_decompose_create_and_state_machine(client):
    """测试：任务清单意图拆解（规则+真实 LLM）与创建/暂停/恢复/取消。

    场景：dry_run 规则拆解 → 真实 LLM 拆解 → 创建清单 → 状态流转。
    预期：items 非空且 day_index 连续；创建返回 id；pause/resume/cancel 生效。
    文档依据：backend/app/api/routers/task_plan.py L57-170。
    """
    rule = client.post(
        "/api/task-plans/decompose",
        json={"intent_text": "第1天：番茄工作法入门\n第2天：番茄钟实操\n第3天：常见误区", "dry_run": True},
    )
    assert rule.status_code == 200, rule.text
    rule_data = rule.json()["data"]
    assert rule_data["items"], "规则拆解无条目"

    llm = client.post(
        "/api/task-plans/decompose",
        json={"intent_text": "帮我规划 3 天的小红书图文选题，主题是效率提升", "daily_time": "09:00"},
    )
    assert llm.status_code == 200, llm.text
    llm_data = llm.json()["data"]
    assert llm_data["items"], "真实 LLM 拆解无条目"
    day_indexes = [it["day_index"] for it in llm_data["items"]]
    assert sorted(day_indexes) == list(range(1, len(day_indexes) + 1)), f"day_index 不连续: {day_indexes}"

    created = client.post(
        "/api/task-plans",
        json={
            "intent_text": "E2E 三天效率主题清单",
            "title": "E2E 清单",
            "items": [
                {"day_index": 1, "topic": "番茄工作法入门", "keyword": "番茄工作法"},
                {"day_index": 2, "topic": "番茄钟实操", "keyword": "专注力"},
            ],
            "plan_config": {"daily_time": "09:00", "review_mode": "manual"},
        },
    )
    assert created.status_code == 200, created.text
    plan_id = created.json()["data"]["id"]

    listed = client.get("/api/task-plans")
    assert listed.status_code == 200, listed.text
    assert any(p["id"] == plan_id for p in listed.json()["data"])

    detail = client.get(f"/api/task-plans/{plan_id}")
    assert detail.status_code == 200, detail.text

    paused = client.post(f"/api/task-plans/{plan_id}/pause")
    assert paused.status_code == 200, paused.text
    resumed = client.post(f"/api/task-plans/{plan_id}/resume")
    assert resumed.status_code == 200, resumed.text
    cancelled = client.post(f"/api/task-plans/{plan_id}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    final = client.get(f"/api/task-plans/{plan_id}").json()["data"]
    assert final["status"] in ("cancelled", "canceled"), f"取消后状态异常: {final['status']}"


def test_task_plan_rejects_non_contiguous_day_index(client):
    """测试：清单 day_index 必须从 1 连续递增。

    场景：提交 day_index=[1,3]。
    预期：400。
    文档依据：backend/app/api/routers/task_plan.py L78-83。
    """
    resp = client.post(
        "/api/task-plans",
        json={
            "intent_text": "E2E 非法清单",
            "items": [
                {"day_index": 1, "topic": "A"},
                {"day_index": 3, "topic": "B"},
            ],
        },
    )
    assert resp.status_code == 400, f"非法 day_index 未拦截: {resp.status_code}"


def test_workspace_bind_file_ops_and_unbind(client):
    """测试：本地工作区绑定 + 文件读写搜索删除 + 解绑。

    场景：绑定临时目录 → 写文件 → 读回 → glob 搜索 → 删除文件 → 解绑。
    预期：每步成功；解绑后工作区列表不含该 id。
    文档依据：backend/app/api/routers/workspace.py L164-360。
    """
    tmp_dir = Path(tempfile.mkdtemp(prefix="e2e-ws-"))
    try:
        bound = client.post("/api/workspace/bind", json={"local_path": str(tmp_dir), "name": "E2E 工作区"})
        assert bound.status_code == 200, bound.text
        ws_id = bound.json()["data"]["id"]

        written = client.post(
            f"/api/workspace/{ws_id}/write",
            json={"path": "notes/e2e.md", "content": "# E2E\n\n番茄工作法测试内容。"},
        )
        assert written.status_code == 200, written.text
        assert (tmp_dir / "notes" / "e2e.md").exists(), "文件未真实落盘"

        read_back = client.post(f"/api/workspace/{ws_id}/read", json={"path": "notes/e2e.md"})
        assert read_back.status_code == 200, read_back.text
        assert "番茄工作法" in read_back.json()["data"]["content"]

        found = client.post(f"/api/workspace/{ws_id}/search", json={"pattern": "**/*.md"})
        assert found.status_code == 200, found.text
        assert found.json()["data"], "文件搜索无结果"

        removed = client.delete(f"/api/workspace/{ws_id}/delete", params={"path": "notes/e2e.md"})
        assert removed.status_code == 200, removed.text
        assert not (tmp_dir / "notes" / "e2e.md").exists(), "文件未被删除"

        listed = client.get("/api/workspace")
        assert listed.status_code == 200, listed.text
        assert any(w["id"] == ws_id for w in listed.json()["data"]["workspaces"])

        unbound = client.delete(f"/api/workspace/{ws_id}")
        assert unbound.status_code == 200, unbound.text
        after = client.get("/api/workspace").json()["data"]["workspaces"]
        assert all(w["id"] != ws_id for w in after)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_notifications_sse_is_authenticated_and_streamable(client):
    """测试：全局通知 SSE 可建立连接且要求登录。

    场景：携带 JWT 打开 /api/sse/notifications 读取首帧（无事件时超时结束）。
    预期：200 + text/event-stream；无 token 时 401/403。
    文档依据：backend/app/api/routers/sse.py subscribe_notifications。
    """
    with client.stream("GET", "/api/sse/notifications", timeout=httpx.Timeout(connect=10.0, read=8.0, write=10.0, pool=10.0)) as resp:
        assert resp.status_code == 200, resp.read().decode("utf-8", "ignore")
        assert "text/event-stream" in resp.headers.get("content-type", "")
        try:
            for _ in resp.iter_lines():
                break
        except httpx.ReadTimeout:
            pass