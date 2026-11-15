"""方法论装载层 — 打法（method/skill）= 纯文本规则输入。

定位（平台红线）：装载层只做两件事——
  1. 找到文本：内置包内打法 / 外部路径（用户 skill 可插入口）/ 原文透传；
  2. 拷进工作区：install_into_workspace 把文本原样落到 ws["inputs"]/rules/method.md，
     供创作 agent 分阶段 read_file。
绝不解释、不执行、不翻译规则；风格只存在于文本本身，本模块零颜色/字体/动效常量。

ref 三选一：
  {"builtin": "general_talk"}     → 读包内 studio_methods/<name>/method.md（utf-8）
  {"path": "<目录或 .md 路径>"}   → 目录读其下 method.md；.md 文件直接读
  {"text": "<规则原文>"}          → 直接透传
"""

from __future__ import annotations

from pathlib import Path

_METHOD_FILE = "method.md"
_REF_KEYS = ("builtin", "path", "text")
_BUILTIN_ROOT = Path(__file__).resolve().parent


def _fail(msg: str) -> None:
    raise RuntimeError(msg)


def _builtin_names() -> list[str]:
    """枚举包内可用打法名：含 method.md 的子目录（新增内置打法无需改代码）。"""
    return sorted(
        p.name for p in _BUILTIN_ROOT.iterdir()
        if p.is_dir() and (p / _METHOD_FILE).is_file()
    )


def _read_builtin(name: object) -> str:
    if not isinstance(name, str):
        _fail(f"builtin 应为字符串（打法名），实际为 {type(name).__name__}（值={name!r}）")
    p = _BUILTIN_ROOT / name / _METHOD_FILE
    if not p.is_file():
        _fail(f"未知内置打法：builtin={name!r}（当前可用：{', '.join(_builtin_names())}）")
    return p.read_text(encoding="utf-8")


def _read_path(value: object) -> str:
    if not isinstance(value, str):
        _fail(f"path 应为字符串（目录或 .md 文件路径），实际为 {type(value).__name__}（值={value!r}）")
    p = Path(value)
    if p.is_dir():
        target = p / _METHOD_FILE
        if not target.is_file():
            _fail(f"打法目录下未找到 {_METHOD_FILE}：{value}")
        return target.read_text(encoding="utf-8")
    if not p.is_file():
        _fail(f"打法路径不存在：{value}")
    return p.read_text(encoding="utf-8")


def load_method(ref: dict) -> dict:
    """按 ref 装载打法文本，返回 {"name", "text", "source"}。

    name：builtin 取打法名；path 取路径 stem；text 固定 "inline"。
    ref 非法（非对象/零键/多键/未知键/类型错）、未知名、路径不存在 → RuntimeError（中文，含实际值）。
    只读取与透传，不改写、不解释文本内容。
    """
    if not isinstance(ref, dict):
        _fail(f"method ref 应为对象(dict)，实际为 {type(ref).__name__}（值={ref!r}）")
    provided = [k for k in _REF_KEYS if k in ref]
    unknown = [k for k in ref if k not in _REF_KEYS]
    if unknown or len(provided) != 1:
        if not provided and not unknown:
            _fail("method ref 不能为空：需 builtin/path/text 三选一")
        _fail(f"method ref 只能三选一（builtin/path/text），实际键={list(ref.keys())}")
    key = provided[0]
    if key == "builtin":
        name = ref["builtin"]
        text = _read_builtin(name)
        return {"name": name, "text": text, "source": "builtin"}
    if key == "path":
        value = ref["path"]
        text = _read_path(value)
        return {"name": Path(value).stem, "text": text, "source": "path"}
    value = ref["text"]
    if not isinstance(value, str):
        _fail(f"text 应为字符串（规则原文），实际为 {type(value).__name__}（值={value!r}）")
    return {"name": "inline", "text": value, "source": "text"}


def install_into_workspace(ws: dict, method: dict) -> Path:
    """把打法文本原样拷入工作区 ws["inputs"]/rules/method.md（utf-8），返回落盘路径。

    只拷贝，不解释：不校验内容、不改写格式、不提取结构。
    父目录不存在自动创建；重复调用覆盖为相同内容（幂等）。
    """
    rules_dir = Path(ws["inputs"]) / "rules"
    rules_dir.mkdir(parents=True, exist_ok=True)
    p = rules_dir / _METHOD_FILE
    p.write_text(method["text"], encoding="utf-8")
    return p
