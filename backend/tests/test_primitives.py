"""Tests for primitives.py — Step 1 validation.

Verifies:
1. All 13 primitives are registered and have correct metadata
2. Each primitive has name, description, required_permissions
3. PRIMITIVES_BY_NAME maps correctly
4. File primitives can actually execute (using temp workspace)
5. Content check primitive works
6. No import errors
"""

import os
import tempfile
from pathlib import Path

import pytest


class TestPrimitiveRegistry:
    def test_all_primitives_count(self):
        from app.engine.reactor.primitives import ALL_PRIMITIVES

        assert len(ALL_PRIMITIVES) == 12

    def test_primitives_by_name_count(self):
        from app.engine.reactor.primitives import PRIMITIVES_BY_NAME

        assert len(PRIMITIVES_BY_NAME) == 12

    def test_all_have_name_and_description(self):
        from app.engine.reactor.primitives import ALL_PRIMITIVES

        for p in ALL_PRIMITIVES:
            assert p.name, f"{p.__class__.__name__} has no name"
            assert p.description, f"{p.__class__.__name__} has no description"
            assert len(p.description) > 10, f"{p.name} description too short"

    def test_names_are_unique(self):
        from app.engine.reactor.primitives import ALL_PRIMITIVES

        names = [p.name for p in ALL_PRIMITIVES]
        assert len(names) == len(set(names)), f"duplicate names: {names}"

    def test_by_name_mapping(self):
        from app.engine.reactor.primitives import ALL_PRIMITIVES, PRIMITIVES_BY_NAME

        for p in ALL_PRIMITIVES:
            assert PRIMITIVES_BY_NAME[p.name] is p

    def test_expected_names(self):
        from app.engine.reactor.primitives import PRIMITIVES_BY_NAME

        # 2026-10 loop 优化：图片三件套与 xhs_search 收编进技能体系（skill_call），
        # 新增 ask_user_question（三层澄清）与 skill_call（技能桥）
        expected = {
            "file_read",
            "file_write",
            "file_edit",
            "bash",
            "glob",
            "grep",
            "llm_generate",
            "viral_score",
            "content_check",
            "ask_user_question",
            "skill_call",
            "work_edit",
        }
        assert set(PRIMITIVES_BY_NAME.keys()) == expected

    def test_info_method(self):
        from app.engine.reactor.primitives import PRIMITIVES_BY_NAME

        info = PRIMITIVES_BY_NAME["file_read"].info()
        assert info["name"] == "file_read"
        assert "description" in info
        assert "required_permissions" in info


class TestPrimitivePermissions:
    def test_file_read_needs_file_read(self):
        from app.engine.reactor.primitives import FileReadPrimitive
        from app.engine.schemas import Permission

        p = FileReadPrimitive()
        assert Permission.FILE_READ in p.required_permissions

    def test_file_write_needs_file_write(self):
        from app.engine.reactor.primitives import FileWritePrimitive
        from app.engine.schemas import Permission

        p = FileWritePrimitive()
        assert Permission.FILE_WRITE in p.required_permissions

    def test_bash_needs_bash_exec(self):
        from app.engine.reactor.primitives import BashPrimitive
        from app.engine.schemas import Permission

        p = BashPrimitive()
        assert Permission.BASH_EXEC in p.required_permissions

    def test_xhs_search_needs_xhs_search(self):
        """XhsSearchPrimitive 已迁移至 skill_call——不再作为内核 Primitive。"""
        pass

    def test_llm_generate_needs_no_permission(self):
        from app.engine.reactor.primitives import LLMGeneratePrimitive

        p = LLMGeneratePrimitive()
        assert p.required_permissions == []

    def test_viral_score_needs_no_permission(self):
        from app.engine.reactor.primitives import ViralScorePrimitive

        p = ViralScorePrimitive()
        assert p.required_permissions == []

    def test_content_check_needs_no_permission(self):
        from app.engine.reactor.primitives import ContentCheckPrimitive

        p = ContentCheckPrimitive()
        assert p.required_permissions == []


class TestFilePrimitivesExecution:
    """Test file primitives with a real temp workspace."""

    @pytest.fixture(autouse=True)
    def setup_workspace(self, tmp_path, monkeypatch):
        self.workspace = tmp_path
        monkeypatch.setenv("WORKSPACE_ROOT", str(self.workspace))

    @pytest.mark.asyncio
    async def test_file_write_and_read(self):
        from app.engine.reactor.primitives import FileReadPrimitive, FileWritePrimitive

        writer = FileWritePrimitive()
        reader = FileReadPrimitive()

        write_result = await writer.execute(
            path="test_file.txt",
            content="hello world\nline 2\nline 3",
        )
        assert write_result["ok"] is True
        assert write_result["lines"] == 3

        read_result = await reader.execute(path="test_file.txt")
        assert read_result["ok"] is True
        assert "hello world" in read_result["content"]

    @pytest.mark.asyncio
    async def test_file_read_with_offset_limit(self):
        from app.engine.reactor.primitives import FileReadPrimitive, FileWritePrimitive

        writer = FileWritePrimitive()
        reader = FileReadPrimitive()

        await writer.execute(
            path="multi_line.txt",
            content="\n".join(f"line {i}" for i in range(20)),
        )

        result = await reader.execute(path="multi_line.txt", offset=5, limit=3)
        assert result["ok"] is True
        assert result["total_lines"] == 20

    @pytest.mark.asyncio
    async def test_file_edit(self):
        from app.engine.reactor.primitives import (
            FileEditPrimitive,
            FileReadPrimitive,
            FileWritePrimitive,
        )

        writer = FileWritePrimitive()
        editor = FileEditPrimitive()
        reader = FileReadPrimitive()

        await writer.execute(path="edit_test.txt", content="foo bar baz")
        edit_result = await editor.execute(
            path="edit_test.txt",
            old_string="bar",
            new_string="REPLACED",
        )
        assert edit_result["ok"] is True

        read_result = await reader.execute(path="edit_test.txt")
        assert "REPLACED" in read_result["content"]
        assert "bar" not in read_result["content"]

    @pytest.mark.asyncio
    async def test_file_read_not_found(self):
        from app.engine.reactor.primitives import FileReadPrimitive

        reader = FileReadPrimitive()
        result = await reader.execute(path="nonexistent.txt")
        assert result["ok"] is False
        assert "not found" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_glob(self):
        from app.engine.reactor.primitives import FileWritePrimitive, GlobPrimitive

        writer = FileWritePrimitive()
        await writer.execute(path="sub/a.txt", content="a")
        await writer.execute(path="sub/b.txt", content="b")
        await writer.execute(path="sub/c.py", content="c")

        globber = GlobPrimitive()
        result = await globber.execute(pattern="**/*.txt", path=str(self.workspace))
        assert result["ok"] is True
        assert result["count"] >= 2

    @pytest.mark.asyncio
    async def test_grep(self):
        from app.engine.reactor.primitives import FileWritePrimitive, GrepPrimitive

        writer = FileWritePrimitive()
        await writer.execute(path="search_me.txt", content="hello world\nfoo bar\nhello again")

        grepper = GrepPrimitive()
        result = await grepper.execute(
            pattern="hello",
            path=str(self.workspace / "search_me.txt"),
        )
        assert result["ok"] is True
        assert result["count"] == 2


class TestContentCheckPrimitive:
    @pytest.mark.asyncio
    async def test_clean_text_passes(self):
        from app.engine.reactor.primitives import ContentCheckPrimitive

        p = ContentCheckPrimitive()
        result = await p.execute(text="今天分享3个手冲咖啡的小技巧")
        assert result["ok"] is True
        assert result["passed"] is True
        assert result["issues"] == []

    @pytest.mark.asyncio
    async def test_sensitive_text_fails(self):
        from app.engine.reactor.primitives import ContentCheckPrimitive

        p = ContentCheckPrimitive()
        result = await p.execute(text="这个特效药包治百病，投资回报率超高")
        assert result["ok"] is True
        assert result["passed"] is False
        assert len(result["issues"]) >= 2

    @pytest.mark.asyncio
    async def test_empty_text_passes(self):
        from app.engine.reactor.primitives import ContentCheckPrimitive

        p = ContentCheckPrimitive()
        result = await p.execute(text="")
        assert result["ok"] is True
        assert result["passed"] is True


class TestViralScorePrimitive:
    @pytest.mark.asyncio
    async def test_empty_results_error(self):
        from app.engine.reactor.primitives import ViralScorePrimitive

        p = ViralScorePrimitive()
        result = await p.execute(results=[])
        assert result["ok"] is False

    @pytest.mark.asyncio
    async def test_with_mock_results(self):
        from app.engine.reactor.primitives import ViralScorePrimitive

        p = ViralScorePrimitive()
        mock_results = [
            {"title": "test1", "likes": 1000, "comments": 50, "author_fans": 5000},
            {"title": "test2", "likes": 500, "comments": 20, "author_fans": 2000},
        ]
        result = await p.execute(results=mock_results)
        assert result["ok"] is True


class TestImageReadPrimitive:
    """ImageReadPrimitive 已迁移至 skill_call——不再作为内核 Primitive。"""

    pass


class TestImageAnalyzePrimitive:
    """ImageAnalyzePrimitive 已迁移至 skill_call——不再作为内核 Primitive。"""

    pass
