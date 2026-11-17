"""文件操作核心（内核层）：路径守卫 + 读/写/编辑的同步实现。

归属内核的原因：file_read/file_write/file_edit 是 primitive 表的基础工具，
实现必须与能力技能层解耦——primitives 与 skills/file_ops 都引用本模块
（依赖方向：能力 → 内核 core ✓，内核零 skills import）。

安全红线：所有操作限制在 WORKSPACE_ROOT（默认 backend/app/data/workspace/）
+ 额外白名单根（backend/outputs/，技能产物目录），绝不回退到项目根目录，
防 LLM 经 file_read 读 .env 密钥。
（2026-10-22 修正文档：旧注释写的 backend/data/workspace 是错的，实际
默认路径一直是 app/data/workspace——与 cards/projects 等运行数据同区。）
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any


def _extra_allowed_roots() -> list[Path]:
    """工作区之外额外放行的目录（绝对路径白名单）。

    backend/outputs/ 是技能产物的统一落点（pixel2motion、视频导出等）。
    智能体精修产物必须能 file_read/file_write 该目录，否则技能生成后
    无法回读修改（2026-11-03 pixel2motion 闭环实测断路）。该目录只含
    运行产物，不含密钥/源码，放行不触碰「防 LLM 读 .env」红线。
    """
    backend_dir = Path(__file__).resolve().parents[3]
    return [backend_dir / "outputs"]


def workspace_root() -> Path:
    """Return the workspace root, guarding all file operations.

    优先级：会话级 cwd（用户在侧边栏指定的文件夹）> WORKSPACE_ROOT 环境变量
    > 默认 backend/app/data/workspace/。

    安全红线：任何情况下都不回退到项目根目录，否则 LLM 可经 file_read/grep
    读到 backend/.env 里的全部密钥（FILE_READ 属于默认低危权限，任何节点自动具备）。
    """
    from app.core.context_vars import current_workspace_root

    bound = current_workspace_root.get()
    if bound:
        return Path(bound).resolve()

    root = os.environ.get("WORKSPACE_ROOT", "")
    if root:
        return Path(root).resolve()
    # 默认：app/data/workspace/（既有运行事实——原 file_ops.py 用 3 级 parent，
    # cards/projects/work 等运行数据都在此，不能改位置；.env 隔离由 .env 位于
    # backend/ 根保证，workspace 在 app/ 下同样读不到）
    app_dir = Path(__file__).resolve().parent.parent.parent
    return (app_dir / "data" / "workspace").resolve()


def resolve_and_guard(raw_path: str) -> Path:
    """Resolve path and enforce it stays within WORKSPACE_ROOT or extra roots.

    Supports both relative paths (resolved against the workspace root) and
    absolute paths (allowed only when they stay inside an allowed root) — mirrors
    Codex's <cwd> semantics where the agent may use either form.

    Raises ValueError if the resolved path escapes all allowed roots.
    """
    ws = workspace_root()
    if not ws.exists():
        ws.mkdir(parents=True, exist_ok=True)

    candidate = Path(raw_path)
    p = (candidate if candidate.is_absolute() else ws / raw_path).resolve()

    def _under(root: Path) -> bool:
        try:
            p.relative_to(root)
            return True
        except ValueError:
            return False

    if _under(ws):
        return p
    for root in _extra_allowed_roots():
        if _under(root):
            return p
    raise ValueError(
        f"path {raw_path!r} resolves to {p}, which is outside workspace {ws} "
        f"and extra allowed roots {[str(r) for r in _extra_allowed_roots()]}"
    )


def _read_text(target: Path) -> tuple[str | None, str | None]:
    """utf-8 优先，gbk 兜底。返回 (text, error)，二者恰一非 None。"""
    try:
        return target.read_text(encoding="utf-8"), None
    except UnicodeDecodeError:
        try:
            return target.read_text(encoding="gbk"), None
        except Exception as e:
            return None, f"cannot read file: {e}"
    except Exception as e:
        return None, f"cannot read file: {e}"


def _atomic_write(target: Path, content: str) -> str | None:
    """原子写入：temp file + rename。返回 None=成功，str=错误。"""
    try:
        fd, tmp = tempfile.mkstemp(
            dir=str(target.parent), prefix="." + target.name + ".", suffix=".tmp"
        )
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, str(target))
        return None
    except Exception as e:
        return f"write failed: {e}"


def read_file(path: str, offset: int = 0, limit: int = 200) -> dict[str, Any]:
    """读取工作区内文件内容，支持分段读取。"""
    try:
        target = resolve_and_guard(path)
    except ValueError as e:
        return {"ok": False, "error": str(e)}

    if not target.exists():
        return {"ok": False, "error": f"file not found: {path}"}
    if not target.is_file():
        return {"ok": False, "error": f"not a file: {path}"}

    text, err = _read_text(target)
    if err:
        return {"ok": False, "error": err}

    lines = text.splitlines()
    total_lines = len(lines)
    if offset > 0:
        lines = lines[offset:]
    if limit > 0:
        lines = lines[:limit]

    return {
        "ok": True,
        "path": str(target),
        "size": target.stat().st_size,
        "total_lines": total_lines,
        "lines": lines,
        "content": "\n".join(lines),
    }


def write_file(path: str, content: str) -> dict[str, Any]:
    """原子写入文件到工作区。"""
    try:
        target = resolve_and_guard(path)
    except ValueError as e:
        return {"ok": False, "error": str(e)}

    target.parent.mkdir(parents=True, exist_ok=True)

    err = _atomic_write(target, content)
    if err:
        return {"ok": False, "error": err}

    return {
        "ok": True,
        "path": str(target),
        "size": target.stat().st_size,
        "lines": content.count("\n") + 1,
    }


def edit_file(path: str, old_string: str, new_string: str = "") -> dict[str, Any]:
    """精确替换文件中唯一匹配的文本片段。"""
    try:
        target = resolve_and_guard(path)
    except ValueError as e:
        return {"ok": False, "error": str(e)}

    if not target.exists():
        return {"ok": False, "error": f"file not found: {path}"}
    if not target.is_file():
        return {"ok": False, "error": f"not a file: {path}"}

    original, err = _read_text(target)
    if err:
        return {"ok": False, "error": err}

    count = original.count(old_string)
    if count == 0:
        return {"ok": False, "error": f"old_string not found in {path}"}
    if count > 1:
        return {
            "ok": False,
            "error": (
                f"old_string appears {count} times in {path}. "
                "must be unique for safe replacement"
            ),
        }

    modified = original.replace(old_string, new_string, 1)
    err = _atomic_write(target, modified)
    if err:
        return {"ok": False, "error": err}

    return {"ok": True, "path": str(target), "replaced": True}
