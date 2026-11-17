"""Reactor system prompt for the ReAct loop.

Prompt is assembled from modular template files under templates/:
  - base.md        : identity, tools, workflow, output format, rules
  - domain_xhs.md  : 小红书 domain knowledge (swap for other domains)
  - discipline.md  : clarification + hallucination discipline
  - skills.md      : skill catalog (injected only when skills exist)

Adding a new domain or rule = adding a new .md file, zero edits to existing ones.
Inspired by Codex's prompt/templates/ architecture: each concern is an
independent template file, Python side renders with variable substitution.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from pydantic_core import PydanticUndefined

from app.engine.reactor.primitives import ALL_PRIMITIVES

TEMPLATES_DIR = Path(__file__).parent / "templates"

# 技能描述注入目录时的截断长度：目录只需"做什么+何时用"，细节留给 usage_hint
_DESC_MAX_CHARS = 80
# 目录「必填」行的截断长度：必填字段是硬约束必须前置，但不能把目录撑爆
_REQUIRED_MAX_CHARS = 160


def _load_template(name: str) -> str:
    """Load a prompt template file from templates/ directory."""
    return (TEMPLATES_DIR / name).read_text(encoding="utf-8")


def build_tool_descriptions() -> str:
    """Auto-generate tool descriptions from registered primitives."""
    sections: list[str] = []
    for p in ALL_PRIMITIVES:
        perms = ""
        if p.required_permissions:
            perm_str = ", ".join(perm.value for perm in p.required_permissions)
            perms = f" [requires: {perm_str}]"
        sections.append(f"- {p.name}{perms} — {p.description}")
    return "\n".join(sections)


def _field_type_name(finfo) -> str:
    """把字段注解映射为 prompt 友好的类型名（bool 判定须先于 int：bool 是 int 子类）。"""
    ann = finfo.annotation
    if ann is None:
        return "str"
    origin = getattr(ann, "__origin__", None)
    if origin is list:
        return "list"
    if ann is bool or (isinstance(ann, type) and issubclass(ann, bool)):
        return "bool"
    if ann is int or (isinstance(ann, type) and issubclass(ann, int)):
        return "int"
    if ann is float or (isinstance(ann, type) and issubclass(ann, float)):
        return "float"
    return "str"


def _render_schema_params(schema_cls, truncate: bool = True) -> str | None:
    """渲染 input_schema 为单行参数说明（不含「参数: 」前缀）。

    返回 None = 渲染失败（保留技能条目但不列参数）；
    返回 "" = schema 无字段（技能不接收参数）。
    truncate=False 用于二层 skill_doc（完整描述，不截断）。
    """
    try:
        fields = dict(getattr(schema_cls, "model_fields", None) or {})
    except Exception:
        return None
    parts: list[str] = []
    for fname, finfo in fields.items():
        fdesc = (finfo.description or "").strip()
        is_required = finfo.default is PydanticUndefined
        part = f"{fname}:{_field_type_name(finfo)}"
        if fdesc:
            part += f" — {fdesc[:_DESC_MAX_CHARS] if truncate else fdesc}"
        if not is_required:
            part += " (可选)"
        parts.append(part)
    return ", ".join(parts)


def _render_required_params(schema_cls, max_chars: int = _REQUIRED_MAX_CHARS) -> str:
    """只渲染「必填」字段为单行（`keyword:str — 说明`），无必填返回空串。

    为什么必填要单独进目录：目录层为控体积把独立参数行整体剥到了二层
    skill_doc，但 skill_doc 只在调用之后才到——含必填字段的技能因此第一次
    调用注定参数缺失（2026-10-29 实测连续多轮空参数失败）。必填是「不写就
    100% 失败」的硬约束，必须前置到目录；可选字段仍留在二层。
    """
    try:
        fields = dict(getattr(schema_cls, "model_fields", None) or {})
    except Exception:
        return ""
    parts: list[str] = []
    for fname, finfo in fields.items():
        if getattr(finfo, "default", PydanticUndefined) is not PydanticUndefined:
            continue
        fdesc = (finfo.description or "").strip()
        part = f"{fname}:{_field_type_name(finfo)}"
        if fdesc:
            part += f" — {fdesc[:_DESC_MAX_CHARS]}"
        parts.append(part)
    line = ", ".join(parts)
    return line[:max_chars]


def build_skill_doc(skill) -> str:
    """二层渐进披露（P2-2）：单技能完整说明，随 skill_call 首次返回。

    目录（一层）只保留 name + desc + 纪律 hint + 通用参数；
    独立参数 schema 与完整字段说明在此层披露，LLM 首调出错时
    可据此立即修正（等价 Claude Code「调用前读 SKILL.md」）。
    """
    lines: list[str] = []
    hint = (getattr(skill, "usage_hint", "") or "").strip()
    if hint:
        lines.append(hint)
    schema_cls = getattr(skill, "input_schema", None)
    params = _render_schema_params(schema_cls, truncate=False) if schema_cls is not None else None
    if params:
        lines.append(f"参数: {params}")
    return "\n".join(lines)


def build_skill_catalog(skills: list | None) -> str:
    """从 session._skills 生成「可用技能」清单文本（一层目录）。

    skills 为 ReactorSession._skills（SkillRegistry 各 node_type 的实例）。
    返回空字符串表示无技能可用（不改写 prompt）。

    体积治理：
    - P0：相同参数行 ≥2 技能共享时提升为顶部「通用参数」一行
    - P2：独立参数行不再进目录（随 skill_call 首次调用以 skill_doc
      二层披露），闸门纪律 usage_hint 保留在目录——剥离会有首次
      调用跳过确认闸门的风险，收益（~1-2k 字符）不抵风险
    - P3：含必填字段的技能单独补一行「必填: ...」——必填缺失是 100%
      失败，必须前置；可选字段仍留在二层 skill_doc
    """
    if not skills:
        return ""
    entry_lines: list[str] = []
    param_rows: list[str | None] = []
    for s in skills:
        name = getattr(s, "name", "")
        if not name:
            continue
        desc = (getattr(s, "description", "") or "").strip().replace("\n", " ")
        entry_lines.append(f"- {name} — {desc[:_DESC_MAX_CHARS]}")
        # 能力侧自带用法指引（frontmatter/类属性 usage_hint），内核零业务知识
        hint = (getattr(s, "usage_hint", "") or "").strip()
        if hint:
            for hl in hint.splitlines():
                hl = hl.strip()
                if hl:
                    entry_lines.append(f"  {hl}")
        schema_cls = getattr(s, "input_schema", None)
        param_rows.append(_render_schema_params(schema_cls) if schema_cls is not None else None)
        # P3：必填字段前置。目录剥掉了独立参数行，若不补这一行，
        # 模型只能凭描述猜字段名（某搜索技能的 keyword 在目录里
        # 一次都不出现 → 首调必缺参）。
        required_line = _render_required_params(schema_cls) if schema_cls is not None else ""
        if required_line:
            entry_lines.append(f"  必填: {required_line}")

    # 通用参数去重：同一参数行出现 ≥2 次时只在顶部写一次（109 个 SKILL.md
    # 技能共用同一 schema，不去重会让同一段参数说明重复上百遍）
    counter = Counter(p for p in param_rows if p)
    out: list[str] = []
    if counter:
        common_params, count = counter.most_common(1)[0]
        if count >= 2:
            out.append(f"通用参数: {common_params}")
    out.extend(entry_lines)
    return "\n".join(out)


# 「其他可用技能」每行列出的名字数：一行 8 个左右，控制行宽
_EXTRA_NAMES_PER_LINE = 8


def _build_extra_names_block(extra_names: list[str] | None) -> str:
    """路由未选中技能的列名段（P1）：只列名字不展开描述。

    目录只展示路由命中子集时，其余技能以列名兜底——LLM 可按名直接
    skill_call 调用（session._skills 始终保持全集，执行侧不受路由影响）。
    """
    if not extra_names:
        return ""
    names = [n.strip() for n in extra_names if n and n.strip()]
    if not names:
        return ""
    lines = ["其他可用技能（未展开，可按名直接调用 skill_call）："]
    for i in range(0, len(names), _EXTRA_NAMES_PER_LINE):
        lines.append("  " + "、".join(names[i : i + _EXTRA_NAMES_PER_LINE]))
    return "\n".join(lines)


def build_skill_catalog_section(
    skills: list | None,
    extra_names: list[str] | None = None,
) -> str:
    """完整「可用技能」段（清单 + skill_call 用法）；无技能时返回空串。

    extra_names：路由未选中、仅列名的技能（P1）。None/空 = 全量目录模式。

    Rendered from templates/skills.md — adding/changing skill protocol
    rules = editing that file, zero edits to Python.
    """
    catalog = build_skill_catalog(skills)
    extra_block = _build_extra_names_block(extra_names)
    if not catalog and not extra_block:
        return ""
    return "\n" + _load_template("skills.md").format(
        skill_catalog="\n".join(x for x in (catalog, extra_block) if x)
    )


def build_design_context_block(
    user_message: str = "",
    creation_type: str | None = None,
) -> str:
    """动态设计系统块（P2-3）：不进 system prompt，由调用方以独立
    消息注入（如 _build_initial_messages 的第二条 system 消息）。

    system prompt 因此完全静态 → 多轮对话间 LLM 前缀缓存可命中
    （动态内容挪到缓存前缀之外的动态区）。
    """
    if not (user_message or creation_type):
        return ""
    try:
        from app.agents.prompts.references.design_system_injector import build_design_system_block

        return (
            build_design_system_block(
                user_message=user_message,
                creation_type=creation_type,
                mode="summary",
            )
            or ""
        )
    except Exception:
        return ""


def get_reactor_system_prompt(
    skills: list | None = None,
    user_message: str = "",
    creation_type: str | None = None,
    extra_skill_names: list[str] | None = None,
) -> str:
    """Build the full system prompt by assembling modular template files.

    Templates loaded from templates/ directory (Codex-style architecture):
      base.md        — identity, tools, workflow, output format, rules
      domain_xhs.md  — 小红书 domain knowledge
      discipline.md  — clarification + hallucination discipline
      skills.md      — skill catalog (only when skills exist)

    Adding a new domain or rule = adding a new .md file + one append line.
    Zero edits to existing template files or Python code.

    P2-3：system prompt 不再拼接 design 动态块（由 build_design_context_block
    单独构建、调用方注入动态区）；user_message/creation_type 参数保留仅为
    签名兼容，不参与组装。
    """
    tool_descriptions = build_tool_descriptions()
    parts: list[str] = [
        _load_template("base.md").format(tool_descriptions=tool_descriptions),
        _load_template("domain_xhs.md"),
        _load_template("discipline.md"),
    ]

    catalog_section = build_skill_catalog_section(skills, extra_skill_names)
    if catalog_section:
        parts.append(catalog_section)

    return "\n\n".join(parts)
