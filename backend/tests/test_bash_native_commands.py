"""bash 工具跨平台行为测试（native 只读命令 + 白名单 + 注入拦截）。

背景：Windows 上 ls/find/grep/head/tail 没有可执行文件，cmd 内建的 dir/type
又不认 Unix 短选项，旧实现"只换程序名不换参数语义"导致 `ls -la` 报
"找不到文件"、`echo hello` 报 binary not found；子进程输出写死 UTF-8 解码
让中文路径变成乱码。本测试锁定修复后的行为。
"""

import os
from pathlib import Path

import pytest

from app.tools.common import shell_fs


@pytest.fixture(autouse=True)
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    (tmp_path / "note.md").write_text("# 中文标题\n第一行\n第二行\n第三行\n", encoding="utf-8")
    (tmp_path / "data.txt").write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    (tmp_path / ".hidden").write_text("secret\n", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "deep.md").write_text("嵌套文档\n", encoding="utf-8")
    (tmp_path / "sub" / "other.py").write_text("print('hi')\n", encoding="utf-8")
    return tmp_path


async def run(command: str, **kwargs) -> dict:
    from app.tools.dev_tools import BashSkill

    return await BashSkill().execute({"command": command, **kwargs})


class TestNativeDispatch:
    def test_native_commands_cover_unix_readonly(self):
        """ls/cat/head/tail/wc/find/grep/glob 必须走 Python 实现，不起子进程。"""
        expected = {"ls", "cat", "head", "tail", "wc", "find", "grep", "glob", "pwd", "echo"}
        assert expected <= shell_fs.NATIVE_COMMANDS

    @pytest.mark.asyncio
    async def test_echo_works(self):
        """测试: echo 是 cmd 内建命令，旧实现走子进程必然 FileNotFound。"""
        result = await run("echo hello")
        assert result["ok"] is True
        assert "hello" in result["stdout"]

    @pytest.mark.asyncio
    async def test_pwd_returns_cwd(self):
        result = await run("pwd", cwd=".")
        assert result["ok"] is True
        assert str(workspace_path()).lower() in result["stdout"].lower()


def workspace_path() -> Path:
    return Path(os.environ["WORKSPACE_ROOT"]).resolve()


class TestLs:
    @pytest.mark.asyncio
    async def test_ls_la_accepts_unix_flags(self):
        """测试: `ls -la` 在 Windows 上曾被翻成 `dir -la` 直接报错。"""
        result = await run("ls -la", cwd=".")
        assert result["ok"] is True
        assert result["stderr"] == ""
        assert "note.md" in result["stdout"]
        assert ".hidden" in result["stdout"]
        assert "sub" in result["stdout"]

    @pytest.mark.asyncio
    async def test_ls_hides_dotfiles_without_a(self):
        result = await run("ls", cwd=".")
        assert result["ok"] is True
        assert ".hidden" not in result["stdout"]

    @pytest.mark.asyncio
    async def test_ls_missing_path_reports_error(self):
        result = await run("ls nope", cwd=".")
        assert result["ok"] is False
        assert "no such file" in result["stderr"]

    @pytest.mark.asyncio
    async def test_dir_and_type_aliases(self):
        """测试: cmd 内建名 dir/type 与 ls/cat 等价，避免 LLM 用 Windows 习惯失败。"""
        ls_result = await run("dir", cwd=".")
        cat_result = await run("type data.txt", cwd=".")
        assert ls_result["ok"] is True
        assert "note.md" in ls_result["stdout"]
        assert cat_result["ok"] is True
        assert "alpha" in cat_result["stdout"]


class TestCatAndPaging:
    @pytest.mark.asyncio
    async def test_cat_chinese_content_not_mangled(self):
        """测试: 输出/文件解码回退链必须保住中文，不能出现替换字符。"""
        result = await run("cat note.md", cwd=".")
        assert result["ok"] is True
        assert "中文标题" in result["stdout"]
        assert "\ufffd" not in result["stdout"]

    @pytest.mark.asyncio
    async def test_cat_with_line_numbers(self):
        result = await run("cat -n data.txt", cwd=".")
        assert result["ok"] is True
        assert "1\talpha" in result["stdout"]

    @pytest.mark.asyncio
    async def test_head_and_tail_line_counts(self):
        head = await run("head -n 2 data.txt", cwd=".")
        tail = await run("tail -n 1 data.txt", cwd=".")
        assert head["stdout"].splitlines() == ["alpha", "beta"]
        assert tail["stdout"].splitlines() == ["gamma"]

    @pytest.mark.asyncio
    async def test_wc_counts_lines(self):
        result = await run("wc -l data.txt", cwd=".")
        assert result["ok"] is True
        assert result["stdout"].startswith("3")


class TestFindAndGrep:
    @pytest.mark.asyncio
    async def test_find_name_filters(self):
        """测试: 旧实现把 -name 当普通参数，把所有文件都列了出来。"""
        result = await run('find . -name "*.md"', cwd=".")
        assert result["ok"] is True
        lines = result["stdout"].splitlines()
        assert any(line.endswith("note.md") for line in lines)
        assert any(line.endswith("deep.md") for line in lines)
        assert not any(line.endswith(".py") for line in lines)

    @pytest.mark.asyncio
    async def test_grep_recursive_with_line_numbers(self):
        result = await run('grep -rn "alpha" .', cwd=".")
        assert result["ok"] is True
        assert "data.txt" in result["stdout"]
        assert ":1:" in result["stdout"]

    @pytest.mark.asyncio
    async def test_grep_ignore_case(self):
        result = await run('grep -i "ALPHA" data.txt', cwd=".")
        assert result["ok"] is True
        assert "alpha" in result["stdout"]

    @pytest.mark.asyncio
    async def test_glob_lists_markdown(self):
        result = await run('glob "*.md"', cwd=".")
        assert result["ok"] is True
        assert "note.md" in result["stdout"]


class TestGuardrails:
    @pytest.mark.asyncio
    async def test_shell_metacharacters_rejected(self):
        """测试: 不经 shell，管道不该被当文件名静默吞掉，要给出明确错误。"""
        result = await run("ls | grep md", cwd=".")
        assert result["ok"] is False
        assert "metacharacters" in result["error"]

    @pytest.mark.asyncio
    async def test_python_not_in_whitelist(self):
        """测试: ROADMAP P0-1 决策——python/node/npm 不在白名单。"""
        result = await run("python --version")
        assert result["ok"] is False
        assert "not in whitelist" in result["error"]

    @pytest.mark.asyncio
    async def test_exec_flag_blocked(self):
        result = await run('find . -exec echo {} ";"', cwd=".")
        assert result["ok"] is False
        assert "denied" in result["error"]

    @pytest.mark.asyncio
    async def test_which_reports_location(self):
        result = await run("which python")
        assert result["ok"] is True
        assert "python" in result["stdout"].lower()


@pytest.mark.skipif(os.name != "nt", reason="GBK 回退只在中文 Windows 生效")
def test_decode_output_gbk_fallback():
    """测试: cmd 子进程在中文 Windows 输出 GBK，解码必须回退而不是变乱码。"""
    assert shell_fs.decode_output("中文路径".encode("gbk")) == "中文路径"
