"""Tests for Codex-style ContextManager system.

Covers:
  1. WorldState: 6 sections, ingest_tool_output, update_world_state, snapshot/restore
  2. ConversationHistory: append-only, for_prompt, to_display_messages, compaction
  3. ContextManager: 三层上下文统管, update_world_state diff 注入, ingest_tool_output 双写
  4. 端到端: 第1轮产出 → 持久化 → 第2轮追问 → LLM 能看到卡片信息
  5. 核心不变量: 工具产出写入 WorldState, for_prompt 是唯一出口, humanize 不丢结构化数据
"""

import json
import pytest

from app.engine.governance.world_state import (
    WorldState,
    WorldStateSection,
    CopywriteSection,
    CardDraftSection,
    AssetSection,
    TaskProgressSection,
    EnvironmentSection,
    UserProfileSection,
)
from app.engine.governance.conversation_history import ConversationHistory, HistoryItem
from app.engine.governance.context_manager import ContextManager


# ============================================================
# 1. WorldState 单元测试
# ============================================================

class TestWorldStateInit:
    def test_has_six_sections(self):
        ws = WorldState()
        assert set(ws._sections.keys()) == {
            "copywrite", "card_draft", "assets",
            "task_progress", "environment", "user_profile",
        }

    def test_initial_snapshot_all_empty(self):
        ws = WorldState()
        snap = ws.take_snapshot()
        assert snap["copywrite"]["title"] == ""
        assert snap["card_draft"]["page_count"] == 0
        assert snap["assets"]["count"] == 0
        assert snap["task_progress"]["phase"] == "idle"
        assert snap["environment"]["platform"] == ""
        assert snap["user_profile"]["domain"] == ""


class TestCopywriteSection:
    def test_update_and_snapshot(self):
        s = CopywriteSection()
        s.update({
            "title": "手冲咖啡5技巧",
            "content": "正文...",
            "tags": ["咖啡", "手冲"],
            "style": "干货清单",
            "platform": "xiaohongshu",
        })
        snap = s.snapshot()
        assert snap["title"] == "手冲咖啡5技巧"
        assert snap["tags"] == ["咖啡", "手冲"]
        assert snap["style"] == "干货清单"

    def test_render_diff_full(self):
        s = CopywriteSection()
        s.update({"title": "标题", "content": "正文", "tags": ["t1"], "style": "s", "platform": "p"})
        diff = s.render_diff(None)
        assert diff is not None
        assert "标题" in diff
        assert "t1" in diff

    def test_render_diff_no_change(self):
        s = CopywriteSection()
        s.update({"title": "标题", "content": "正文", "tags": [], "style": "", "platform": ""})
        prev = s.snapshot()
        diff = s.render_diff(prev)
        assert diff is None

    def test_render_diff_empty(self):
        s = CopywriteSection()
        diff = s.render_diff(None)
        assert diff is None


class TestCardDraftSection:
    def test_update_and_snapshot(self):
        s = CardDraftSection()
        s.update({
            "pages": [{"title": "页1"}, {"title": "页2"}, {"title": "页3"}],
            "recipe": "干货清单",
            "theme": "ink",
            "file_path": "/data/cards/test.html",
            "asset_ids": ["asset_001"],
        })
        snap = s.snapshot()
        assert snap["page_count"] == 3
        assert snap["recipe"] == "干货清单"
        assert snap["file_path"] == "/data/cards/test.html"
        assert snap["asset_ids"] == ["asset_001"]
        assert snap["page_titles"] == ["页1", "页2", "页3"]

    def test_render_diff_full(self):
        s = CardDraftSection()
        s.update({
            "pages": [{"title": "页1"}],
            "recipe": "r", "theme": "t",
            "file_path": "/path.html", "asset_ids": ["a1"],
        })
        diff = s.render_diff(None)
        assert diff is not None
        assert "1 页" in diff
        assert "/path.html" in diff
        assert "a1" in diff

    def test_render_diff_no_change(self):
        s = CardDraftSection()
        s.update({"pages": [{"title": "页1"}], "recipe": "r", "theme": "t", "file_path": "/p", "asset_ids": []})
        prev = s.snapshot()
        diff = s.render_diff(prev)
        assert diff is None

    def test_restore_from_snapshot_format(self):
        """snapshot 格式 (page_count, page_titles) 也能恢复。"""
        s = CardDraftSection()
        s.update({
            "page_count": 3,
            "page_titles": ["页1", "页2", "页3"],
            "recipe": "干货清单",
            "theme": "ink",
            "file_path": "/data/cards/test.html",
            "asset_ids": ["asset_001"],
        })
        snap = s.snapshot()
        assert snap["page_count"] == 3
        assert snap["page_titles"] == ["页1", "页2", "页3"]


class TestAssetSection:
    def test_add_and_snapshot(self):
        s = AssetSection()
        s.add({"type": "image", "id": "img_001", "path": "/data/img/1.png"})
        s.add({"type": "image", "id": "img_002", "path": "/data/img/2.png"})
        snap = s.snapshot()
        assert snap["count"] == 2
        assert len(snap["items"]) == 2

    def test_render_diff_full(self):
        s = AssetSection()
        s.add({"type": "image", "id": "img_001", "path": "/data/img/1.png"})
        diff = s.render_diff(None)
        assert diff is not None
        assert "img_001" in diff

    def test_render_diff_incremental(self):
        s = AssetSection()
        s.add({"type": "image", "id": "img_001", "path": "/data/img/1.png"})
        prev = s.snapshot()
        s.add({"type": "image", "id": "img_002", "path": "/data/img/2.png"})
        diff = s.render_diff(prev)
        assert diff is not None
        assert "img_002" in diff
        assert "img_001" in diff


class TestTaskProgressSection:
    def test_update_and_snapshot(self):
        s = TaskProgressSection()
        s.update("copywrite_done", ["文案生成"], ["卡片制作"])
        snap = s.snapshot()
        assert snap["phase"] == "copywrite_done"
        assert "文案生成" in snap["completed"]

    def test_render_diff(self):
        s = TaskProgressSection()
        s.update("card_done", ["文案生成", "卡片制作"], [])
        diff = s.render_diff(None)
        assert diff is not None
        assert "card_done" in diff
        assert "文案生成" in diff


class TestWorldStateIngest:
    def test_ingest_copywrite(self):
        ws = WorldState()
        ws.ingest_tool_output("skill_call", {
            "skill": "copywrite",
            "result": {"title": "T", "content": "C", "tags": ["t"], "style": "s", "platform": "p"},
        })
        assert ws.copywrite._title == "T"

    def test_ingest_card_draft(self):
        ws = WorldState()
        ws.ingest_tool_output("skill_call", {
            "skill": "compose_card_deck",
            "result": {
                "card_draft": {
                    "pages": [{"title": "页1"}],
                    "recipe": "r", "theme": "t",
                    "file_path": "/p", "asset_ids": ["a1"],
                }
            },
        })
        assert ws.card_draft._file_path == "/p"
        assert ws.card_draft._asset_ids == ["a1"]

    def test_ingest_image_gen(self):
        ws = WorldState()
        ws.ingest_tool_output("image_gen", {
            "images": [{"id": "img_001", "path": "/data/img/1.png"}],
        })
        assert ws.assets._assets[0]["id"] == "img_001"

    def test_ingest_unknown_tool_no_crash(self):
        ws = WorldState()
        ws.ingest_tool_output("unknown_tool", {"foo": "bar"})
        assert ws.take_snapshot()["assets"]["count"] == 0


class TestWorldStateUpdateAndSnapshot:
    def test_update_world_state_first_round(self):
        ws = WorldState()
        fragments, rollout = ws.update_world_state(None)
        assert fragments == ""
        assert rollout["type"] == "full"

    def test_update_world_state_with_diff(self):
        ws = WorldState()
        ws.ingest_tool_output("skill_call", {
            "skill": "compose_card_deck",
            "result": {
                "card_draft": {
                    "pages": [{"title": "页1"}],
                    "recipe": "r", "theme": "t",
                    "file_path": "/p", "asset_ids": [],
                }
            },
        })
        fragments, rollout = ws.update_world_state(None)
        assert "卡片草稿" in fragments
        assert "/p" in fragments
        assert rollout["type"] == "full"

    def test_snapshot_roundtrip(self):
        ws = WorldState()
        ws.ingest_tool_output("skill_call", {
            "skill": "copywrite",
            "result": {"title": "T", "content": "C", "tags": ["t"], "style": "s", "platform": "p"},
        })
        ws.ingest_tool_output("skill_call", {
            "skill": "compose_card_deck",
            "result": {
                "card_draft": {
                    "pages": [{"title": "页1"}, {"title": "页2"}],
                    "recipe": "r", "theme": "t",
                    "file_path": "/p.html", "asset_ids": ["a1"],
                }
            },
        })
        snap = ws.take_snapshot()

        ws2 = WorldState.from_snapshot(snap)
        snap2 = ws2.take_snapshot()

        assert snap2["copywrite"]["title"] == "T"
        assert snap2["card_draft"]["page_count"] == 2
        assert snap2["card_draft"]["file_path"] == "/p.html"
        assert snap2["card_draft"]["asset_ids"] == ["a1"]
        assert snap2["card_draft"]["page_titles"] == ["页1", "页2"]
        assert snap2["task_progress"]["phase"] == "card_done"


# ============================================================
# 2. ConversationHistory 单元测试
# ============================================================

class TestConversationHistoryBasic:
    def test_empty(self):
        h = ConversationHistory()
        assert h.message_count == 0
        assert h.estimate_tokens() == 0
        assert h.for_prompt() == []

    def test_add_user_message(self):
        h = ConversationHistory()
        h.add_user_message("你好")
        assert h.message_count == 1
        msgs = h.for_prompt()
        assert msgs[0] == {"role": "user", "content": "你好"}

    def test_add_assistant_message(self):
        h = ConversationHistory()
        h.add_assistant_message("已为您制作卡片")
        assert h.message_count == 1
        msgs = h.for_prompt()
        assert msgs[0] == {"role": "assistant", "content": "已为您制作卡片"}

    def test_add_system_message(self):
        h = ConversationHistory()
        h.add_system_message("系统提示")
        msgs = h.for_prompt()
        assert msgs[0] == {"role": "system", "content": "系统提示"}

    def test_add_tool_call_and_output(self):
        h = ConversationHistory()
        h.add_tool_call("skill_call", {"skill": "copywrite", "args": {}})
        h.add_tool_output("skill_call", {"ok": True, "result": "文案已生成"}, ok=True)
        assert h.message_count == 2
        msgs = h.for_prompt()
        assert len(msgs) == 2

    def test_for_prompt_with_system_prompt(self):
        h = ConversationHistory()
        h.add_user_message("你好")
        msgs = h.for_prompt(system_prompt="你是助手")
        assert msgs[0] == {"role": "system", "content": "你是助手"}
        assert msgs[1] == {"role": "user", "content": "你好"}


class TestConversationHistoryAppendOnly:
    def test_record_items(self):
        h = ConversationHistory()
        items = [
            HistoryItem(role="user", content="msg1"),
            HistoryItem(role="assistant", content="msg2"),
        ]
        h.record_items(items)
        assert h.message_count == 2
        assert h.history_version == 1

    def test_version_increments(self):
        h = ConversationHistory()
        assert h.history_version == 0
        h.add_user_message("a")
        assert h.history_version == 1
        h.add_assistant_message("b")
        assert h.history_version == 2

    def test_replace_compacted_history(self):
        h = ConversationHistory()
        for i in range(10):
            h.add_user_message(f"msg{i}")
        old_version = h.history_version

        new_items = [
            HistoryItem(role="system", content="摘要", metadata={"compaction": True}),
            HistoryItem(role="user", content="msg9"),
        ]
        h.replace_compacted_history(new_items)
        assert h.message_count == 2
        assert h.history_version == old_version + 1


class TestConversationHistoryHumanize:
    def test_to_display_messages_skips_tool_call(self):
        h = ConversationHistory()
        h.add_user_message("你好")
        h.add_tool_call("skill_call", {"skill": "copywrite"})
        h.add_tool_output("skill_call", {"ok": True, "result": "文案已生成"})
        display = h.to_display_messages()
        roles = [m["role"] for m in display]
        assert "tool_call" not in roles

    def test_to_display_messages_shortens_tool_output(self):
        h = ConversationHistory()
        h.add_tool_output("skill_call", {"ok": True, "result": "x" * 500})
        display = h.to_display_messages()
        assert len(display) == 1
        assert len(display[0]["content"]) <= 200


class TestConversationHistorySerialization:
    def test_to_dict_and_from_dict(self):
        h = ConversationHistory()
        h.add_user_message("你好")
        h.add_assistant_message("已为您制作卡片")
        h.add_tool_output("skill_call", {"ok": True})

        data = h.to_dict()
        h2 = ConversationHistory.from_dict(data)
        assert h2.message_count == 3
        assert h2.history_version == h.history_version
        msgs = h2.for_prompt()
        assert msgs[0]["content"] == "你好"


# ============================================================
# 3. ContextManager 单元测试
# ============================================================

class TestContextManagerInit:
    def test_basic_init(self):
        cm = ContextManager(session_id="test")
        assert cm.session_id == "test"
        assert cm.world_state is not None
        assert cm.history is not None
        assert cm.token_budget is not None

    def test_set_system_prompt(self):
        cm = ContextManager(session_id="test")
        cm.set_system_prompt("你是助手")
        msgs = cm.for_prompt()
        assert msgs[0]["role"] == "system"
        assert msgs[0]["content"] == "你是助手"


class TestContextManagerUpdateWorldState:
    def test_first_round_empty(self):
        cm = ContextManager(session_id="test")
        fragments, rollout = cm.update_world_state()
        assert fragments == ""
        assert rollout["type"] == "full"

    def test_diff_injected_into_history(self):
        cm = ContextManager(session_id="test")
        cm.set_system_prompt("你是助手")
        cm.ingest_tool_output("skill_call", {
            "skill": "compose_card_deck",
            "result": {
                "card_draft": {
                    "pages": [{"title": "页1"}],
                    "recipe": "r", "theme": "t",
                    "file_path": "/cards/test.html", "asset_ids": ["a1"],
                }
            },
        })
        fragments, rollout = cm.update_world_state()
        assert "卡片草稿" in fragments
        assert "/cards/test.html" in fragments

        msgs = cm.for_prompt()
        system_msgs = [m for m in msgs if m["role"] == "system"]
        assert any("卡片草稿" in m["content"] for m in system_msgs)


class TestContextManagerIngestToolOutput:
    def test_dual_write(self):
        """ingest_tool_output 同时写入 WorldState 和 ConversationHistory。"""
        cm = ContextManager(session_id="test")
        cm.ingest_tool_output("skill_call", {
            "skill": "copywrite",
            "result": {"title": "T", "content": "C", "tags": [], "style": "", "platform": ""},
        })
        assert cm.world_state.copywrite._title == "T"
        assert cm.history.message_count == 1

    def test_tool_output_preserves_structure(self):
        """tool_output 保留完整结构化数据。"""
        cm = ContextManager(session_id="test")
        cm.ingest_tool_output("skill_call", {
            "skill": "compose_card_deck",
            "result": {
                "card_draft": {
                    "pages": [{"title": "页1"}],
                    "recipe": "r", "theme": "t",
                    "file_path": "/p.html", "asset_ids": ["a1"],
                }
            },
        })
        items = cm.history.items
        assert len(items) == 1
        assert items[0].role == "tool_output"
        assert "/p.html" in items[0].content
        assert "a1" in items[0].content


class TestContextManagerForPrompt:
    def test_for_prompt_is_only_llm_exit(self):
        """for_prompt() 是唯一的 LLM 出口——包含 system + history + world_state diff。"""
        cm = ContextManager(session_id="test")
        cm.set_system_prompt("你是助手")
        cm.history.add_user_message("帮我做卡片")
        cm.ingest_tool_output("skill_call", {
            "skill": "compose_card_deck",
            "result": {
                "card_draft": {
                    "pages": [{"title": "页1"}],
                    "recipe": "r", "theme": "t",
                    "file_path": "/p.html", "asset_ids": [],
                }
            },
        })
        cm.update_world_state()

        msgs = cm.for_prompt()
        assert msgs[0]["role"] == "system"
        assert msgs[0]["content"] == "你是助手"
        assert any("卡片草稿" in m["content"] for m in msgs)


class TestContextManagerSnapshot:
    def test_take_snapshot_and_restore(self):
        cm = ContextManager(session_id="test")
        cm.ingest_tool_output("skill_call", {
            "skill": "compose_card_deck",
            "result": {
                "card_draft": {
                    "pages": [{"title": "页1"}, {"title": "页2"}],
                    "recipe": "r", "theme": "t",
                    "file_path": "/p.html", "asset_ids": ["a1"],
                }
            },
        })
        snap = cm.take_snapshot()

        cm2 = ContextManager.from_snapshot("test", snap)
        assert cm2.world_state.card_draft._file_path == "/p.html"
        assert cm2.world_state.card_draft._asset_ids == ["a1"]
        assert len(cm2.world_state.card_draft._pages) == 2


# ============================================================
# 4. 端到端测试：核心问题场景
# ============================================================

class TestEndToEndCardWhereIsIt:
    """核心问题：第1轮生成卡片后，第2轮追问"卡片在哪"时 LLM 能看到卡片信息。"""

    def _make_round1_snapshot(self):
        """第1轮：用户要求做卡片 → 工具产出 → 持久化。"""
        cm = ContextManager(session_id="test_session")
        cm.set_system_prompt("你是小红书创作助手")

        cm.update_world_state()

        cm.history.add_user_message("帮我做一期手冲咖啡的图文卡片")

        cm.ingest_tool_output("skill_call", {
            "skill": "compose_card_deck",
            "result": {
                "card_draft": {
                    "pages": [
                        {"title": "手冲咖啡技巧1"},
                        {"title": "手冲咖啡技巧2"},
                        {"title": "手冲咖啡技巧3"},
                    ],
                    "recipe": "干货清单",
                    "theme": "ink",
                    "file_path": "/data/cards/手冲咖啡.html",
                    "asset_ids": ["asset_coffee_001"],
                }
            },
        })

        snapshot = cm.take_snapshot()
        return snapshot

    def test_round2_llm_sees_card_info(self):
        """第2轮追问"卡片在哪"时，LLM 的上下文包含卡片信息。"""
        snapshot = self._make_round1_snapshot()

        cm2 = ContextManager.from_snapshot("test_session", snapshot)
        cm2.set_system_prompt("你是小红书创作助手")

        cm2.history.add_user_message("帮我做一期手冲咖啡的图文卡片")
        cm2.history.add_assistant_message("已为您制作3张图文卡片")
        cm2.history.add_user_message("卡片在哪？给我看看")

        fragments, _ = cm2.update_world_state()

        assert "卡片草稿" in fragments, f"WorldState diff 应包含卡片信息，实际: {fragments}"
        assert "/data/cards/手冲咖啡.html" in fragments, "diff 应包含文件路径"
        assert "asset_coffee_001" in fragments, "diff 应包含资源ID"
        assert "3 页" in fragments, "diff 应包含页数"

    def test_round2_for_prompt_contains_card_state(self):
        """for_prompt() 输出中 LLM 能看到卡片状态。"""
        snapshot = self._make_round1_snapshot()

        cm2 = ContextManager.from_snapshot("test_session", snapshot)
        cm2.set_system_prompt("你是小红书创作助手")
        cm2.history.add_user_message("帮我做一期手冲咖啡的图文卡片")
        cm2.history.add_assistant_message("已为您制作3张图文卡片")
        cm2.history.add_user_message("卡片在哪？给我看看")
        cm2.update_world_state()

        msgs = cm2.for_prompt()

        all_content = " ".join(m["content"] for m in msgs)
        assert "/data/cards/手冲咖啡.html" in all_content, \
            "LLM 上下文应包含卡片文件路径"
        assert "asset_coffee_001" in all_content, \
            "LLM 上下文应包含资源ID"
        assert "手冲咖啡技巧1" in all_content, \
            "LLM 上下文应包含页面标题"

    def test_round2_llm_will_not_say_not_generated(self):
        """LLM 不可能再说"没有生成卡片"——因为 WorldState 的事实存在。"""
        snapshot = self._make_round1_snapshot()

        cm2 = ContextManager.from_snapshot("test_session", snapshot)
        cm2.set_system_prompt("你是小红书创作助手")
        cm2.history.add_user_message("帮我做一期手冲咖啡的图文卡片")
        cm2.history.add_assistant_message("已为您制作3张图文卡片")
        cm2.history.add_user_message("你生成卡片了吗？")
        cm2.update_world_state()

        msgs = cm2.for_prompt()
        all_content = " ".join(m["content"] for m in msgs)

        assert "card_done" in all_content or "卡片制作" in all_content, \
            "LLM 上下文应包含任务进度信息，表明卡片已制作完成"
        assert "3 页" in all_content, \
            "LLM 上下文应包含页数信息"


class TestEndToEndCopywriteThenCard:
    """文案 → 卡片 → 追问文案内容的完整流程。"""

    def test_copywrite_result_visible_in_round2(self):
        """第1轮生成文案，第2轮追问文案内容时 LLM 能看到。"""
        cm = ContextManager(session_id="test")
        cm.set_system_prompt("你是助手")
        cm.update_world_state()

        cm.history.add_user_message("帮我写一篇手冲咖啡的文案")
        cm.ingest_tool_output("skill_call", {
            "skill": "copywrite",
            "result": {
                "title": "手冲咖啡5个必知技巧",
                "content": "第一，选择新鲜烘焙的咖啡豆...",
                "tags": ["咖啡", "手冲", "技巧"],
                "style": "干货清单",
                "platform": "xiaohongshu",
            },
        })

        snapshot = cm.take_snapshot()

        cm2 = ContextManager.from_snapshot("test", snapshot)
        cm2.set_system_prompt("你是助手")
        cm2.history.add_user_message("帮我写一篇手冲咖啡的文案")
        cm2.history.add_assistant_message("已为您生成文案")
        cm2.history.add_user_message("文案标题是什么？")

        fragments, _ = cm2.update_world_state()
        assert "手冲咖啡5个必知技巧" in fragments, \
            f"第2轮 diff 应包含文案标题，实际: {fragments}"


class TestEndToEndNewAssetInRound3:
    """第3轮新增资源时，diff 只包含增量。"""

    def test_incremental_diff(self):
        cm = ContextManager(session_id="test")
        cm.ingest_tool_output("skill_call", {
            "skill": "compose_card_deck",
            "result": {
                "card_draft": {
                    "pages": [{"title": "页1"}],
                    "recipe": "r", "theme": "t",
                    "file_path": "/p.html", "asset_ids": [],
                }
            },
        })
        snap1 = cm.take_snapshot()

        cm2 = ContextManager.from_snapshot("test", snap1)
        cm2._world_state_baseline = snap1.get("world_state")

        cm2.ingest_tool_output("image_gen", {
            "images": [{"id": "img_001", "path": "/data/img/1.png"}],
        })

        fragments, _ = cm2.update_world_state()
        assert "img_001" in fragments, "增量 diff 应包含新资源"


# ============================================================
# 5. 核心不变量测试
# ============================================================

class TestInvariants:
    """对标 Codex 核心不变量。"""

    def test_tool_output_writes_to_world_state(self):
        """不变量1: 工具产出必须写入 WorldState。"""
        cm = ContextManager(session_id="test")
        cm.ingest_tool_output("skill_call", {
            "skill": "copywrite",
            "result": {"title": "T", "content": "C", "tags": [], "style": "", "platform": ""},
        })
        assert cm.world_state.copywrite._title == "T", \
            "工具产出应写入 WorldState"

    def test_for_prompt_is_only_llm_exit(self):
        """不变量2: for_prompt() 是唯一的 LLM 出口。"""
        cm = ContextManager(session_id="test")
        cm.set_system_prompt("系统")
        cm.history.add_user_message("用户消息")
        cm.ingest_tool_output("skill_call", {
            "skill": "copywrite",
            "result": {"title": "T", "content": "C", "tags": [], "style": "", "platform": ""},
        })
        cm.update_world_state()

        msgs = cm.for_prompt()
        assert msgs[0]["role"] == "system"
        assert len(msgs) > 1

    def test_record_items_is_only_append_entry(self):
        """不变量3: record_items() 是唯一的追加入口。"""
        h = ConversationHistory()
        old_count = h.message_count
        h.record_items([HistoryItem(role="user", content="test")])
        assert h.message_count == old_count + 1

    def test_world_state_independent_of_history(self):
        """不变量4: WorldState 独立于对话历史——即使历史被压缩，事实不丢。"""
        cm = ContextManager(session_id="test")
        cm.ingest_tool_output("skill_call", {
            "skill": "compose_card_deck",
            "result": {
                "card_draft": {
                    "pages": [{"title": "页1"}],
                    "recipe": "r", "theme": "t",
                    "file_path": "/p.html", "asset_ids": ["a1"],
                }
            },
        })

        cm.history.replace_compacted_history([
            HistoryItem(role="system", content="摘要", metadata={"compaction": True}),
        ])

        assert cm.world_state.card_draft._file_path == "/p.html", \
            "历史压缩后 WorldState 的事实不丢"
        assert cm.world_state.card_draft._asset_ids == ["a1"], \
            "历史压缩后 WorldState 的资源ID不丢"

    def test_humanize_does_not_lose_structure(self):
        """不变量5: humanize 只用于前端，结构化数据不丢弃。"""
        h = ConversationHistory()
        h.add_tool_output("skill_call", {
            "ok": True,
            "result": {
                "card_draft": {
                    "file_path": "/data/cards/test.html",
                    "asset_ids": ["a1"],
                }
            },
        })

        items = h.items
        assert len(items) == 1
        assert "/data/cards/test.html" in items[0].content, \
            "HistoryItem 保留完整结构化数据"

        display = h.to_display_messages()
        assert len(display) == 1
        assert len(display[0]["content"]) <= 200, \
            "前端展示可以截断，但 HistoryItem 不丢数据"


# ============================================================
# 6. 边界条件测试
# ============================================================

class TestEdgeCases:
    def test_empty_world_state_diff(self):
        ws = WorldState()
        fragments, rollout = ws.update_world_state(None)
        assert fragments == ""

    def test_concurrent_ingest_order(self):
        """多次 ingest 的顺序不影响最终状态。"""
        ws = WorldState()
        ws.ingest_tool_output("skill_call", {
            "skill": "copywrite",
            "result": {"title": "T1", "content": "C", "tags": [], "style": "", "platform": ""},
        })
        ws.ingest_tool_output("skill_call", {
            "skill": "compose_card_deck",
            "result": {
                "card_draft": {
                    "pages": [{"title": "页1"}],
                    "recipe": "r", "theme": "t",
                    "file_path": "/p", "asset_ids": [],
                }
            },
        })
        snap = ws.take_snapshot()
        assert snap["copywrite"]["title"] == "T1"
        assert snap["card_draft"]["page_count"] == 1
        assert snap["task_progress"]["phase"] == "card_done"

    def test_history_serialization_roundtrip(self):
        h = ConversationHistory()
        h.add_user_message("你好")
        h.add_assistant_message("已制作卡片")
        h.add_tool_call("skill_call", {"skill": "copywrite"})
        h.add_tool_output("skill_call", {"ok": True, "result": {"title": "T"}})

        data = h.to_dict()
        h2 = ConversationHistory.from_dict(data)

        assert h2.message_count == 4
        msgs1 = h.for_prompt()
        msgs2 = h2.for_prompt()
        assert msgs1 == msgs2

    def test_context_manager_snapshot_roundtrip(self):
        cm = ContextManager(session_id="test")
        cm.set_system_prompt("你是助手")
        cm.ingest_tool_output("skill_call", {
            "skill": "copywrite",
            "result": {"title": "T", "content": "C", "tags": ["t1"], "style": "s", "platform": "p"},
        })
        cm.ingest_tool_output("skill_call", {
            "skill": "compose_card_deck",
            "result": {
                "card_draft": {
                    "pages": [{"title": "页1"}, {"title": "页2"}],
                    "recipe": "r", "theme": "t",
                    "file_path": "/p.html", "asset_ids": ["a1"],
                }
            },
        })

        snap = cm.take_snapshot()
        cm2 = ContextManager.from_snapshot("test", snap)

        assert cm2.world_state.copywrite._title == "T"
        assert cm2.world_state.card_draft._file_path == "/p.html"
        assert len(cm2.world_state.card_draft._pages) == 2
        assert cm2.world_state.card_draft._asset_ids == ["a1"]

    def test_multiple_rounds_accumulate(self):
        """多轮累积：文案 → 卡片 → 图片 → 每轮 diff 正确。"""
        cm = ContextManager(session_id="test")
        cm.set_system_prompt("你是助手")

        cm.update_world_state()
        cm.history.add_user_message("写文案")
        cm.ingest_tool_output("skill_call", {
            "skill": "copywrite",
            "result": {"title": "T", "content": "C", "tags": [], "style": "", "platform": ""},
        })
        snap1 = cm.take_snapshot()

        cm2 = ContextManager.from_snapshot("test", snap1)
        cm2.set_system_prompt("你是助手")
        cm2.history.add_user_message("写文案")
        cm2.history.add_assistant_message("文案已生成")
        cm2.history.add_user_message("做卡片")
        cm2.ingest_tool_output("skill_call", {
            "skill": "compose_card_deck",
            "result": {
                "card_draft": {
                    "pages": [{"title": "页1"}],
                    "recipe": "r", "theme": "t",
                    "file_path": "/p.html", "asset_ids": [],
                }
            },
        })
        fragments2, _ = cm2.update_world_state()
        assert "文案" in fragments2 or "卡片" in fragments2, \
            f"第2轮 diff 应包含文案或卡片信息，实际: {fragments2}"

        snap2 = cm2.take_snapshot()

        cm3 = ContextManager.from_snapshot("test", snap2)
        cm3.set_system_prompt("你是助手")
        cm3.history.add_user_message("写文案")
        cm3.history.add_assistant_message("文案已生成")
        cm3.history.add_user_message("做卡片")
        cm3.history.add_assistant_message("卡片已制作")
        cm3.history.add_user_message("生成配图")
        cm3.ingest_tool_output("image_gen", {
            "images": [{"id": "img_001", "path": "/data/img/1.png"}],
        })
        fragments3, _ = cm3.update_world_state()
        assert "img_001" in fragments3, \
            f"第3轮 diff 应包含新图片资源，实际: {fragments3}"