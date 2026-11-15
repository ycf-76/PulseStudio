"""跨平台只读命令的纯 Python 实现：ls/cat/head/tail/wc/echo/pwd/find/grep/glob。

设计要点：
- 不依赖系统二进制。Windows 没有 ls/find/grep/head/tail 的 exe（wc/tail 根本不存在），
  cmd 内建的 dir/type 又不认 Unix 短选项（`dir -la` 直接报"找不到文件"），
  所以只读命令一律自己实现，行为在 Windows/Linux/macOS 上保持一致。
- 输出统一解码：UTF-8 → GBK → 系统首选编码 → 替换字符，
  避免路径中文变乱码、LLM 拿到 \ufffd 当事实。
  （2026-11-13：UTF-8 mode 下 locale 也返回 utf-8，必须显式插 GBK 兜底。）
- 每个命令只返回 (exit_code, stdout, stderr)，不向上抛业务异常。
"""

from __future__ import annotations

import fnmatch
import locale
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

MAX_FIND_RESULTS = 500
MAX_GREP_LINES = 200

# 走本模块实现（不起子进程）的命令集合
NATIVE_COMMANDS: frozenset[str] = frozenset(
    {
        "ls", "dir", "cat", "type", "head", "tail", "wc", "echo", "pwd",
        "find", "grep", "rg", "glob", "which", "where",
    }
)

# 未经 shell，这些元字符不会被解释，只会变成"文件名"，提前报错更可读
SHELL_METACHARS: frozenset[str] = frozenset(
    {"|", "||", ">", ">>", "<", "<<", "&&", ";", "2>", "2>&1", "&"}
)

_LONG_FLAGS: dict[str, str] = {
    "--all": "a",
    "--long": "l",
    "--recursive": "R",
    "--ignore-case": "i",
    "--line-number": "n",
    "--insensitive": "i",
}


def _encoding_chain() -> tuple[str, ...]:
    """统一解码回退链：UTF-8 → GBK → 系统首选编码。

    2026-11-13 BUG-3：Python 开启 UTF-8 mode 时，
    locale.getpreferredencoding(False) 也返回 utf-8，原来那条
    「UTF-8 → 系统首选编码」等于没有 GBK 兜底，Windows 中文子进程的
    GBK 输出会全部变成替换字符。这里显式插入 gbk/cp936。
    """
    preferred = locale.getpreferredencoding(False) or "utf-8"
    chain = ["utf-8", "gbk"]
    if preferred.lower().replace("_", "-") not in ("utf-8", "gbk", "cp936"):
        chain.append(preferred)
    return tuple(chain)


def decode_output(raw: bytes) -> str:
    """UTF-8 → GBK → 系统首选编码 → 替换字符。"""
    for encoding in _encoding_chain():
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def read_text(path: Path) -> str:
    """读文本文件，编码回退链同 decode_output。"""
    for encoding in _encoding_chain():
        try:
            return path.read_text(encoding=encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return path.read_text(encoding="utf-8", errors="replace")


def _resolve(cwd: Path, token: str) -> Path:
    path = Path(token).expanduser()
    return path if path.is_absolute() else cwd / path


def _split_flags(args: list[str]) -> tuple[set[str], list[str]]:
    """拆出短选项（-la → {'l','a'}）与位置参数。"""
    flags: set[str] = set()
    positional: list[str] = []
    for token in args:
        if token in _LONG_FLAGS:
            flags.add(_LONG_FLAGS[token])
        elif token.startswith("--"):
            flags.add(token[2:])
        elif token.startswith("-") and len(token) > 1:
            flags.update(token[1:].lower())
        else:
            positional.append(token)
    return flags, positional


def _ls_line(path: Path, flags: set[str]) -> str:
    if "l" not in flags:
        return path.name
    try:
        stat = path.stat()
        size, mtime = stat.st_size, datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
    except OSError:
        size, mtime = 0, "?"
    return f"{'d' if path.is_dir() else '-'} {size:>10} {mtime} {path.name}"


def _cmd_ls(args: list[str], cwd: Path) -> tuple[int, str, str]:
    flags, targets = _split_flags(args)
    roots = [(target, _resolve(cwd, target)) for target in targets] or [(".", cwd)]
    lines: list[str] = []
    errors: list[str] = []
    for label, root in roots:
        if not root.exists():
            errors.append(f"ls: {label}: no such file or directory")
            continue
        if root.is_file():
            lines.append(_ls_line(root, flags))
            continue
        try:
            entries = [
                p for p in root.iterdir() if "a" in flags or not p.name.startswith(".")
            ]
        except OSError as e:
            errors.append(f"ls: {label}: {e}")
            continue
        entries.sort(key=lambda p: (not p.is_dir(), p.name.lower()))
        if len(roots) > 1:
            lines.append(f"{label}:")
        lines.extend(_ls_line(p, flags) for p in entries)
        if len(roots) > 1:
            lines.append("")
    out = "\n".join(lines) + ("\n" if lines else "")
    return (1 if errors else 0), out, "\n".join(errors)


def _cmd_cat(args: list[str], cwd: Path) -> tuple[int, str, str]:
    flags, files = _split_flags(args)
    if not files:
        return 1, "", "cat: missing file operand"
    chunks: list[str] = []
    errors: list[str] = []
    for name in files:
        path = _resolve(cwd, name)
        if not path.is_file():
            errors.append(f"cat: {name}: no such file")
            continue
        chunks.append(read_text(path))
    text = "".join(chunks)
    if "n" in flags and text:
        text = "\n".join(f"{i:>6}\t{line}" for i, line in enumerate(text.splitlines(), 1)) + "\n"
    return (1 if errors else 0), text, "\n".join(errors)


def _parse_count(args: list[str], default: int = 10) -> tuple[int, list[str]]:
    count = default
    files: list[str] = []
    idx = 0
    while idx < len(args):
        token = args[idx]
        if token in ("-n", "--lines") and idx + 1 < len(args):
            idx += 1
            if args[idx].isdigit():
                count = int(args[idx])
        elif re.fullmatch(r"-\d+", token):
            count = int(token[1:])
        elif token.startswith("-"):
            pass
        else:
            files.append(token)
        idx += 1
    return count, files


def _cmd_head(args: list[str], cwd: Path) -> tuple[int, str, str]:
    count, files = _parse_count(args)
    if not files:
        return 1, "", "head: missing file operand"
    path = _resolve(cwd, files[0])
    if not path.is_file():
        return 1, "", f"head: {files[0]}: no such file"
    lines = read_text(path).splitlines()[:count]
    return 0, "\n".join(lines) + ("\n" if lines else ""), ""


def _cmd_tail(args: list[str], cwd: Path) -> tuple[int, str, str]:
    count, files = _parse_count(args)
    if not files:
        return 1, "", "tail: missing file operand"
    path = _resolve(cwd, files[0])
    if not path.is_file():
        return 1, "", f"tail: {files[0]}: no such file"
    lines = read_text(path).splitlines()[-count:]
    return 0, "\n".join(lines) + ("\n" if lines else ""), ""


def _cmd_wc(args: list[str], cwd: Path) -> tuple[int, str, str]:
    flags, files = _split_flags(args)
    if not files:
        return 1, "", "wc: missing file operand"
    path = _resolve(cwd, files[0])
    if not path.is_file():
        return 1, "", f"wc: {files[0]}: no such file"
    text = read_text(path)
    lines, words, chars = len(text.splitlines()), len(text.split()), len(text)
    if not flags:
        return 0, f"{lines} {words} {chars} {files[0]}\n", ""
    parts = []
    if "l" in flags:
        parts.append(str(lines))
    if "w" in flags:
        parts.append(str(words))
    if "c" in flags or "m" in flags:
        parts.append(str(chars))
    return 0, " ".join(parts) + f" {files[0]}\n", ""


def _cmd_echo(args: list[str], cwd: Path) -> tuple[int, str, str]:
    trailing_newline = True
    if args and args[0] == "-n":
        trailing_newline, args = False, args[1:]
    return 0, " ".join(args) + ("\n" if trailing_newline else ""), ""


def _cmd_pwd(args: list[str], cwd: Path) -> tuple[int, str, str]:
    return 0, f"{cwd}\n", ""


def _cmd_which(args: list[str], cwd: Path) -> tuple[int, str, str]:
    _, names = _split_flags(args)
    if not names:
        return 1, "", "which: missing command name"
    lines, missing = [], False
    for name in names:
        found = shutil.which(name)
        missing = missing or found is None
        lines.append(found or f"{name}: not found")
    return (1 if missing else 0), "\n".join(lines) + "\n", ""


def _cmd_find(args: list[str], cwd: Path) -> tuple[int, str, str]:
    pattern, target, kind, maxdepth = "*", ".", "", None
    insensitive = False
    idx = 0
    while idx < len(args):
        token = args[idx]
        if token in ("-name", "-iname") and idx + 1 < len(args):
            insensitive = token == "-iname"
            idx += 1
            pattern = args[idx]
        elif token == "-type" and idx + 1 < len(args):
            idx += 1
            kind = args[idx].lower()
        elif token == "-maxdepth" and idx + 1 < len(args):
            idx += 1
            maxdepth = int(args[idx]) if args[idx].isdigit() else None
        elif not token.startswith("-"):
            target = token
        idx += 1

    root = _resolve(cwd, target)
    if not root.exists():
        return 1, "", f"find: {target}: no such file or directory"
    if root.is_file():
        return 0, f"{root}\n", ""

    needle = pattern.lower() if insensitive else pattern
    results: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel_depth = len(Path(dirpath).relative_to(root).parts)
        if maxdepth is not None and rel_depth >= maxdepth:
            dirnames[:] = []
        for name in sorted(dirnames) + sorted(filenames):
            candidate = name.lower() if insensitive else name
            if not fnmatch.fnmatch(candidate, needle):
                continue
            path = Path(dirpath) / name
            is_dir = path.is_dir()
            if kind and ((kind == "d" and not is_dir) or (kind == "f" and is_dir)):
                continue
            results.append(str(path))
            if len(results) >= MAX_FIND_RESULTS:
                break
        if len(results) >= MAX_FIND_RESULTS:
            break
    return 0, ("\n".join(results) + ("\n" if results else "")), ""


def _grep_files(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    return [p for p in root.rglob("*") if p.is_file()]


def _cmd_grep(args: list[str], cwd: Path) -> tuple[int, str, str]:
    flags: set[str] = set()
    pattern: str | None = None
    targets: list[str] = []
    for token in args:
        if token in _LONG_FLAGS:
            flags.add(_LONG_FLAGS[token])
        elif token.startswith("--"):
            flags.add(token[2:])
        elif token.startswith("-") and len(token) > 1:
            flags.update(token[1:].lower())
        elif pattern is None:
            pattern = token
        else:
            targets.append(token)
    if pattern is None:
        return 1, "", "grep: missing pattern"

    try:
        regex = re.compile(pattern, re.IGNORECASE if "i" in flags else 0)
    except re.error as e:
        return 1, "", f"grep: invalid regex: {e}"

    roots = [_resolve(cwd, t) for t in targets] or [cwd]
    lines: list[str] = []
    errors: list[str] = []
    matched_files = 0
    for root in roots:
        if not root.exists():
            errors.append(f"grep: {root}: no such file or directory")
            continue
        for path in _grep_files(root):
            try:
                text = read_text(path)
            except OSError as e:
                errors.append(f"grep: {path}: {e}")
                continue
            if "\x00" in text:
                continue
            hit = 0
            for line_no, line in enumerate(text.splitlines(), start=1):
                if not regex.search(line):
                    continue
                hit += 1
                if "l" in flags or "files-with-matches" in flags:
                    lines.append(str(path))
                    matched_files += 1
                    break
                prefix = f"{path}:" + (f"{line_no}:" if "n" in flags else "")
                lines.append(f"{prefix}{line}")
                if len(lines) >= MAX_GREP_LINES:
                    break
            if hit and "c" in flags:
                lines.append(f"{path}:{hit}")
            if len(lines) >= MAX_GREP_LINES:
                break
        if len(lines) >= MAX_GREP_LINES:
            break
    return (1 if errors else 0), ("\n".join(lines) + ("\n" if lines else "")), "\n".join(errors)


def _cmd_glob(args: list[str], cwd: Path) -> tuple[int, str, str]:
    _, positional = _split_flags(args)
    pattern = positional[0] if positional else "*"
    root = _resolve(cwd, positional[1] if len(positional) > 1 else ".")
    if not root.exists():
        return 1, "", f"glob: {root}: no such file or directory"
    matches = sorted(str(p) for p in root.rglob(pattern) if p.is_file())
    return 0, ("\n".join(matches) + ("\n" if matches else "")), ""


_HANDLERS: dict[str, Any] = {
    "ls": _cmd_ls,
    "dir": _cmd_ls,
    "cat": _cmd_cat,
    "type": _cmd_cat,
    "head": _cmd_head,
    "tail": _cmd_tail,
    "wc": _cmd_wc,
    "echo": _cmd_echo,
    "pwd": _cmd_pwd,
    "which": _cmd_which,
    "where": _cmd_which,
    "find": _cmd_find,
    "grep": _cmd_grep,
    "rg": _cmd_grep,
    "glob": _cmd_glob,
}


def execute_native(argv: list[str], cwd: Path) -> tuple[int, str, str]:
    """按命令名分发到纯 Python 实现，返回 (exit_code, stdout, stderr)。"""
    command = Path(argv[0]).stem.lower()
    handler = _HANDLERS.get(command)
    if handler is None:
        return 1, "", f"unsupported native command: {command}"
    try:
        return handler(argv[1:], cwd)
    except OSError as e:
        return 1, "", f"{command}: {e}"
