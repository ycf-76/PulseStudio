"""Guardian: tool-call safety gate.

Ref: Codex exec.rs — every tool call goes through an approval chain:
  1. Policy check (is this tool allowed in this context?)
  2. Confirmation gate (does this high-risk action need user approval?)
  3. Argument validation (are the arguments safe and well-formed?)

Our adaptation:
  - Guardian wraps _dispatch_tool in ReactorSession
  - Three-stage pipeline: policy -> confirm -> validate
  - Each stage can ALLOW, DENY, or NEEDS_CONFIRM
  - DENY returns error as observation (doesn't crash the loop)
  - NEEDS_CONFIRM pauses the loop and asks the user
  - All decisions are logged for audit

Usage:
    guardian = Guardian()
    decision = await guardian.check(tool_name, arguments, context, skill)
    if decision.action == Action.ALLOW:
        result = await skill.execute(inputs)
    elif decision.action == Action.DENY:
        observation = decision.to_observation()
    elif decision.action == Action.NEEDS_CONFIRM:
        # pause loop, ask user
"""

from __future__ import annotations

import logging
import os
import re
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

if TYPE_CHECKING:
    from app.core.skill_base import Skill
    from app.engine.schemas import Permission, WorkflowContext

logger = logging.getLogger(__name__)


class GuardianAction(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    NEEDS_CONFIRM = "needs_confirm"


class GuardianDecision(BaseModel):
    action: GuardianAction
    reason: str = ""
    confirmation_prompt: str = ""
    risk_level: str = "low"
    hint: str = ""
    """给 LLM 的下一步指引（如权限被拒时告知如何向用户求助）。回喂 LLM 时不脱敏。"""
    user_hint: str = ""
    """面向用户的安全文案（不含内部路径/架构细节）。observation 中只传此字段。"""

    def to_observation(self) -> str:
        import json

        payload = {
            "error": "guardian_denied",
            "reason": self.reason,
            "risk_level": self.risk_level,
        }
        if self.user_hint:
            payload["hint"] = self.user_hint
        elif self.hint:
            payload["hint"] = self.hint
        return json.dumps(payload, ensure_ascii=False)


class GuardianPolicy(BaseModel):
    """Per-tool policy rule.

    Ref: Codex exec.rs — each Command has an associated policy:
      - Shell commands: sandboxed, needs confirmation
      - File reads: auto-allowed
      - File writes: needs confirmation
      - MCP calls: depends on the tool's risk level
    """

    tool_name: str
    auto_allow: bool = False
    needs_confirmation: bool = False
    max_argument_length: int = 10000
    denied_patterns: list[str] = []
    risk_level: str = "low"
    needs_clarification: bool = False


_DEFAULT_POLICIES: dict[str, GuardianPolicy] = {
    "spawn_agent": GuardianPolicy(
        tool_name="spawn_agent",
        auto_allow=True,
        risk_level="medium",
    ),
    "wait_agent": GuardianPolicy(
        tool_name="wait_agent",
        auto_allow=True,
        risk_level="low",
    ),
    "list_agents": GuardianPolicy(
        tool_name="list_agents",
        auto_allow=True,
        risk_level="low",
    ),
    "send_message": GuardianPolicy(
        tool_name="send_message",
        auto_allow=True,
        risk_level="low",
    ),
    "interrupt_agent": GuardianPolicy(
        tool_name="interrupt_agent",
        auto_allow=True,
        risk_level="low",
    ),
    "file_write": GuardianPolicy(
        tool_name="file_write",
        needs_confirmation=True,
        risk_level="high",
        denied_patterns=[r"\.\./", r"/etc/", r"/proc/", r"C:\\Windows"],
    ),
    "bash_exec": GuardianPolicy(
        tool_name="bash_exec",
        needs_confirmation=True,
        risk_level="high",
        denied_patterns=[r"rm\s+-rf", r"sudo\s+", r"chmod\s+"],
    ),
    "bash": GuardianPolicy(
        tool_name="bash",
        needs_confirmation=True,
        risk_level="high",
        denied_patterns=[r"rm\s+-rf", r"sudo\s+", r"chmod\s+"],
    ),
    # ── Primitives: file ops (read-only) ──────────────────────────
    "file_read": GuardianPolicy(
        tool_name="file_read",
        auto_allow=True,
        risk_level="low",
    ),
    "file_edit": GuardianPolicy(
        tool_name="file_edit",
        auto_allow=True,
        risk_level="medium",
    ),
    "glob": GuardianPolicy(
        tool_name="glob",
        auto_allow=True,
        risk_level="low",
    ),
    "grep": GuardianPolicy(
        tool_name="grep",
        auto_allow=True,
        risk_level="low",
    ),
    # ── Primitives: image ──────────────────────────────────────────
    # ── Primitives: LLM & scoring ─────────────────────────────────
    "llm_generate": GuardianPolicy(
        tool_name="llm_generate",
        auto_allow=True,
        risk_level="low",
    ),
    "viral_score": GuardianPolicy(
        tool_name="viral_score",
        auto_allow=True,
        risk_level="low",
    ),
    "content_check": GuardianPolicy(
        tool_name="content_check",
        auto_allow=True,
        risk_level="low",
    ),
    # ── Skills: search ─────────────────────────────────────────────
    # ── Skills: video production ───────────────────────────────────
    # skill_call：ReAct 循环调用 SkillRegistry 技能的通用入口（P0-1）。
    # 2026-10 重构：skill_call 不再 auto_allow——LLM 决定调什么技能，
    # Guardian 决定能不能执行。有用户可见产出的创作类技能必须经用户确认，
    # 否则 LLM 可在闲聊时越权生成内容；只读类技能由会话层放行。
    # （具体技能名属能力层，内核不感知，见 tests/contract 纯净度契约。）
    "skill_call": GuardianPolicy(
        tool_name="skill_call",
        needs_confirmation=True,
        risk_level="medium",
    ),
    "ask_user_question": GuardianPolicy(
        tool_name="ask_user_question",
        auto_allow=True,
        risk_level="low",
    ),
    # work_edit：写用户当前查看的作品内容覆盖层（仅草稿放行，采集/已发布
    # 作品由服务层状态闸门拒绝）。auto_approve 会话下直接放行；
    # 需人工确认的会话（auto_approve=False）走确认闸门。
    "work_edit": GuardianPolicy(
        tool_name="work_edit",
        needs_confirmation=True,
        risk_level="medium",
    ),
    # ── Skills: TTS ────────────────────────────────────────────────
    # ── Skills: image & card generation ────────────────────────────
    # ── 文案风格类策略（历史遗留段名，条目已清空） ──
    # ── Skills: analysis ───────────────────────────────────────────
    # ── Skills: external messaging (needs confirmation) ────────────
    # ── Skills: feishu (medium — read/write docs) ──────────────────
    # ── Prompt-skill node_types ────────────────────────────────────
}

_ARGUMENT_INJECTION_PATTERNS = [
    r";\s*rm\s",
    r";\s*sudo\s",
    r"\$\(",
    r"`",
    r"&&\s*rm\s",
    r"\|\s*sh\b",
    r"\b(eval|exec|system|subprocess|os\.system)\s*\(",
]

# ── bash 只读命令白名单（2026-10-22）─────────────────────────────
# 此前 bash 一刀切 needs_confirmation=True/high：LLM 探查环境的
# `ls -la`、`python -c "import playwright"` 都要用户手动确认，
# 任务干一半就卡死在「此操作需要您的确认才能继续」（10:45 实锤：
# 图文报告 13 步全被一条 ls -la 摁停）。
# 现改为：首词命中只读白名单 → 自动放行（denied/injection 模式仍照查）；
# 其余命令维持高危确认。rm -rf / sudo / chmod 由 denied_patterns 硬拒。
_BASH_READONLY_COMMANDS: frozenset[str] = frozenset(
    {
        "ls",
        "pwd",
        "cat",
        "head",
        "tail",
        "echo",
        "printf",
        "which",
        "where",
        "whoami",
        "date",
        "dir",
        "find",
        "grep",
        "rg",
        "wc",
        "sort",
        "uniq",
        "file",
        "stat",
        "du",
        "df",
        "tree",
        "env",
        "printenv",
        "type",
        # 版本/清单类探查
        "python",
        "python3",
        "node",
        "npm",
        "pip",
        "pip3",
        # git 只读子命令单独判断
    }
)

_BASH_READONLY_SUBCOMMANDS: dict[str, frozenset[str]] = {
    "git": frozenset({"status", "log", "diff", "show", "branch", "remote", "rev-parse"}),
    "npm": frozenset({"list", "ls", "view", "config"}),
    "pip": frozenset({"list", "show", "freeze"}),
    "pip3": frozenset({"list", "show", "freeze"}),
}


def _is_bash_readonly(command: str) -> bool:
    """判断 bash 命令是否为只读探查（首词/子命令白名单）。

    注意：这是"放行判断"不是"安全判断"——危险构造（rm/sudo/eval/$(
    反引号等）由 denied_patterns 与 _ARGUMENT_INJECTION_PATTERNS 拦截，
    且优先级更高（先查后放）。
    """
    import shlex

    try:
        parts = shlex.split(command or "")
    except ValueError:
        return False
    if not parts:
        return False
    first = parts[0].rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if first in _BASH_READONLY_SUBCOMMANDS:
        # git status / npm list 等只读子命令
        sub = next((p for p in parts[1:] if not p.startswith("-")), "")
        return sub in _BASH_READONLY_SUBCOMMANDS[first]
    if first in _BASH_READONLY_COMMANDS:
        # python/node 裸调用必须带 -c/-m/--version/-V 等非交互参数，
        # 进交互 REPL 或跑脚本文件（python x.py）不算只读
        if first in ("python", "python3", "node"):
            return any(
                p in ("-c", "--version", "-V", "-v") or p.startswith("-c=") for p in parts[1:]
            )
        return True
    return False


# ── bash 媒体处理白名单（2026-11-02）─────────────────────────────
# 视频链路主卡点：执行层白名单（dev_tools BashSkill.ALLOWED_BINARIES）
# 含 ffmpeg/ffprobe，但确认层（本文件）不含——确认层先杀，执行层白名单
# 永远轮不到生效。剪辑任务每一步（探测/静音检测/切割/拼接/导出）都是
# ffmpeg/ffprobe，自动执行场景无人点确认即 GUARDIAN_PAUSED
# （2026-11-02 诊断实锤：视频任务必被摁停）。
# 规则：
#   - ffprobe：纯只读（读元数据不写盘），无条件放行；
#   - ffmpeg：取最后一个非选项 token 作输出路径（ffmpeg 约定 output 在末位）
#     启发式判定——相对路径（子进程 cwd 即 workspace 内）或位于
#     workspace root 内 → 放行；workspace 外绝对路径 → 维持高危确认。
# denied/injection 模式在 Stage 2 已查（先于本判断），rm/sudo/eval 等
# 危险构造照样硬拒——与只读白名单同一安全顺序。


def _workspace_root_for_guardian() -> Path | None:
    """确认层用的 workspace root。与 dev_tools._workspace_root 同源
    （contextvar 优先，其次 WORKSPACE_ROOT 环境变量），但不 import tools
    层——governance 属内核，纯净度契约禁止能力层反向依赖。"""
    from app.core.context_vars import current_workspace_root

    bound = current_workspace_root.get()
    if bound:
        return Path(bound).resolve()
    root = os.environ.get("WORKSPACE_ROOT", "")
    if root:
        return Path(root).resolve()
    return None


def _is_bash_media_safe(command: str) -> bool:
    """判断 bash 命令是否为安全的媒体处理（ffprobe 任意 / ffmpeg 输出限 workspace）。"""
    import shlex

    try:
        parts = shlex.split(command or "")
    except ValueError:
        return False
    if len(parts) < 2:
        return False
    first = parts[0].rsplit("/", 1)[-1].rsplit("\\", 1)[-1].lower()
    if first == "ffprobe":
        return True
    if first != "ffmpeg":
        return False
    # 输出路径启发式：从末位向前找第一个非选项 token（ffmpeg 约定 output
    # 在末位；选项本身以 - 开头，+flag 类如 +faststart 也视为选项）
    out = ""
    for token in reversed(parts[1:]):
        if not token.startswith("-") and not token.startswith("+") and token != "-":
            out = token
            break
    if not out or len(out) < 2:
        return False
    out_path = Path(out)
    if not out_path.is_absolute():
        # 相对路径：子进程 cwd 恒为 workspace（BashSkill 工作区红线）
        return True
    root = _workspace_root_for_guardian()
    if root is None:
        return False
    try:
        # normcase：Windows 下大小写不敏感比较
        norm = os.path.normcase
        pr, rr = norm(str(out_path.resolve())), norm(str(root))
        return pr == rr or pr.startswith(rr + os.sep)
    except OSError:
        return False


# ── 确认弹窗文案辅助（2026-11-04）────────────────────────────────
# 引擎注入的内部元数据字段（_user_id 等）以 _ 开头，属会话层内部信息，
# 不应出现在用户可见的确认弹窗里（旧版弹窗把 _user_id 拦腰截断甩给用户）。
_INTERNAL_ARG_PREFIX = "_"


def _summarize_visible_args(args: Any, cap: int = 300) -> str:
    """用户可见的参数摘要：过滤 _ 前缀内部字段，紧凑 JSON，超长截断。"""
    if not isinstance(args, dict):
        text = str(args)
    else:
        visible = {k: v for k, v in args.items() if not str(k).startswith(_INTERNAL_ARG_PREFIX)}
        if not visible:
            return "（无业务参数）"
        import json

        try:
            text = json.dumps(visible, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(visible)
    return text if len(text) <= cap else text[:cap] + "…"


def _lookup_skill_metadata(skill_name: str) -> tuple[str, str]:
    """查技能注册表拿 display_name / description（数据驱动，查不到静默降级）。

    governance 层 import app.core 与既有依赖（permissions/context_vars）同层，
    且只读元数据、不持技能名清单——纯净度契约不破。
    """
    if not skill_name:
        return "", ""
    try:
        from app.core.skill_registry import get_skill_class_by_name

        cls_ = get_skill_class_by_name(skill_name)
        if cls_ is None:
            return "", ""
        display_name = str(getattr(cls_, "display_name", "") or "")
        description = str(getattr(cls_, "description", "") or "").strip()
        return display_name, description
    except Exception:
        logger.debug("[guardian] skill metadata lookup failed", exc_info=True)
        return "", ""


def _build_skill_call_confirmation(arguments: dict[str, Any], risk_label: str) -> str:
    """skill_call 专用确认文案：说清楚「执行什么技能、干什么、参数是什么」。"""
    inner_skill = str(arguments.get("skill") or arguments.get("name") or "")
    inner_args = (
        arguments.get("arguments") or arguments.get("args") or arguments.get("parameters") or {}
    )
    if not isinstance(inner_args, dict):
        inner_args = {}

    display_name, description = _lookup_skill_metadata(inner_skill)
    lines = ["即将执行技能："]
    if display_name:
        lines.append(f"【{display_name}】（{inner_skill}）")
    elif inner_skill:
        lines.append(f"【{inner_skill}】")
    else:
        lines.append("【未知技能】（调用参数中未声明技能名）")
    if description:
        lines.append(f"说明：{description}")
    lines.append(f"参数：{_summarize_visible_args(inner_args)}")
    lines.append("")
    lines.append(f"{risk_label}。是否继续？")
    return "\n".join(lines)


class Guardian:
    """Tool-call safety gate.

    Ref: Codex exec.rs architecture:
      - exec_command() checks policy before running
      - SandboxPolicy determines if command needs confirmation
      - UserConfirmation handles the interactive approval flow

    Our adaptation:
      - check() runs the three-stage pipeline
      - Stages are overridable for custom policies
      - Audit log records every decision
    """

    def __init__(
        self,
        policies: dict[str, GuardianPolicy] | None = None,
        strict_mode: bool = False,
    ) -> None:
        self._policies = policies or dict(_DEFAULT_POLICIES)
        self._strict_mode = strict_mode
        self._audit_log: list[dict[str, Any]] = []
        self._denied_count = 0
        self._allowed_count = 0
        self._confirmed_count = 0

    async def check(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: WorkflowContext | None = None,
        skill: Skill | None = None,
    ) -> GuardianDecision:
        policy = self._policies.get(tool_name)

        if policy is None:
            if self._strict_mode:
                decision = GuardianDecision(
                    action=GuardianAction.DENY,
                    reason=f"no policy for tool '{tool_name}' and strict mode is on",
                    risk_level="unknown",
                )
            else:
                decision = GuardianDecision(
                    action=GuardianAction.NEEDS_CONFIRM,
                    reason=f"no policy for tool '{tool_name}', requiring confirmation as safety default",
                    risk_level="unknown",
                    confirmation_prompt=(
                        f"Tool '{tool_name}' has no registered safety policy. "
                        f"This is an unreviewed tool — proceed with caution.\n\n"
                        f"是否允许执行？"
                    ),
                )
            self._record(tool_name, decision)
            return decision

        # Stage 1: auto-allow shortcut
        if policy.auto_allow and not policy.needs_confirmation:
            decision = GuardianDecision(
                action=GuardianAction.ALLOW,
                reason=f"auto-allowed by policy for '{tool_name}'",
                risk_level=policy.risk_level,
            )
            self._record(tool_name, decision)
            return decision

        # Stage 2: argument validation
        arg_decision = self._validate_arguments(tool_name, arguments, policy)
        if arg_decision is not None:
            self._record(tool_name, arg_decision)
            return arg_decision

        # Stage 3: permission gate (ref: Codex's permission check in exec.rs)
        if skill is not None and skill.required_permissions:
            from app.core.permissions import permission_gate

            for perm in skill.required_permissions:
                if not permission_gate.is_allowed(perm, context):
                    decision = GuardianDecision(
                        action=GuardianAction.DENY,
                        reason=f"permission denied: {perm.value}",
                        risk_level=policy.risk_level,
                        hint=(
                            f"该权限（{perm.value}）未开放，这不是任务终点。"
                            "向用户说明缺少该权限、开启方法（backend/.env 的 "
                            "PERMISSIONS_ALLOW 加入对应值并重启后端），并明确询问"
                            "用户是否开启后继续。不要默默放弃或只字不提地结束任务。"
                        ),
                        user_hint=(
                            f"该权限（{perm.value}）未开放，这不是任务终点。"
                            "请向用户说明缺少该权限，并询问是否开启后继续。"
                            "不要默默放弃或只字不提地结束任务。"
                        ),
                    )
                    self._record(tool_name, decision)
                    return decision

        # Stage 4: confirmation gate
        # 红线：bash / bash_exec 任何模式下都不自动放行（"deny 恒胜"原则，
        if policy.needs_confirmation:
            # 2026-10-22：bash 只读探查白名单——ls/cat/python -c 这类命令
            # 每次都弹高危确认会把任务摁死在半路（denied/injection 模式在
            # Stage 2 已查过，走到这里的白名单命令无危险构造）。
            if tool_name in ("bash", "bash_exec"):
                _cmd = str(arguments.get("command") or "")
                if _cmd and _is_bash_readonly(_cmd):
                    decision = GuardianDecision(
                        action=GuardianAction.ALLOW,
                        reason=f"bash readonly command auto-allowed: {_cmd[:80]}",
                        risk_level="low",
                    )
                    self._record(tool_name, decision)
                    return decision
                # 2026-11-02：媒体处理命令（ffprobe 任意 / ffmpeg 输出限 workspace）
                # 自动放行——视频任务此前每条 ffmpeg 都弹高危确认，自动执行
                # 场景无人确认即 GUARDIAN_PAUSED（干一半就停的主凶）。
                if _cmd and _is_bash_media_safe(_cmd):
                    decision = GuardianDecision(
                        action=GuardianAction.ALLOW,
                        reason=f"bash media command auto-allowed: {_cmd[:80]}",
                        risk_level="low",
                    )
                    self._record(tool_name, decision)
                    return decision
            prompt = self._build_confirmation_prompt(tool_name, arguments, policy.risk_level)
            decision = GuardianDecision(
                action=GuardianAction.NEEDS_CONFIRM,
                reason=f"tool '{tool_name}' requires user confirmation",
                confirmation_prompt=prompt,
                risk_level=policy.risk_level,
            )
            self._confirmed_count += 1
            self._record(tool_name, decision)
            return decision

        decision = GuardianDecision(
            action=GuardianAction.ALLOW,
            reason=f"allowed by policy for '{tool_name}'",
            risk_level=policy.risk_level,
        )
        self._record(tool_name, decision)
        return decision

    def _validate_arguments(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        policy: GuardianPolicy,
    ) -> GuardianDecision | None:
        args_str = str(arguments)

        if len(args_str) > policy.max_argument_length:
            return GuardianDecision(
                action=GuardianAction.DENY,
                reason=f"arguments too long ({len(args_str)} > {policy.max_argument_length})",
                risk_level=policy.risk_level,
            )

        for pattern in policy.denied_patterns:
            try:
                if re.search(pattern, args_str):
                    return GuardianDecision(
                        action=GuardianAction.DENY,
                        reason=f"argument matches denied pattern: {pattern}",
                        risk_level=policy.risk_level,
                    )
            except re.error as e:
                logger.warning(f"[guardian] invalid denied_pattern regex '{pattern}': {e}")

        for pattern in _ARGUMENT_INJECTION_PATTERNS:
            try:
                if re.search(pattern, args_str, re.IGNORECASE):
                    return GuardianDecision(
                        action=GuardianAction.DENY,
                        reason=f"argument contains potential injection: {pattern}",
                        risk_level="high",
                    )
            except re.error as e:
                logger.warning(f"[guardian] invalid injection_pattern regex '{pattern}': {e}")

        return None

    def _build_confirmation_prompt(
        self, tool_name: str, arguments: dict[str, Any], risk_level: str = "high"
    ) -> str:
        # 2026-11-04 改版：确认弹窗是给用户看的，不是给开发者看的。
        # 旧版 "Tool 'skill_call' is about to execute with arguments: {...}"
        # 直接把原始 dict str 截 200 字符甩给用户（内部字段 _user_id 都漏进
        # UI 且被拦腰截断），用户看不出这步是干嘛的、花不花钱——没法做
        # 知情决定。现在：中文文案 + 技能 display_name/description（数据
        # 驱动查注册表，内核不持技能名清单，纯净度契约不破）+ 过滤内部字段。
        if risk_level == "high":
            risk_label = "⚠️ 高风险操作，请谨慎确认"
        elif risk_level == "medium":
            risk_label = "中等风险操作，需要您确认"
        else:
            risk_label = "低风险操作，需要您确认"

        if tool_name == "skill_call":
            return _build_skill_call_confirmation(arguments, risk_label)

        return (
            f"即将执行操作「{tool_name}」。\n"
            f"参数：{_summarize_visible_args(arguments)}\n\n"
            f"{risk_label}。是否继续？"
        )

    def _record(self, tool_name: str, decision: GuardianDecision) -> None:
        entry = {
            "tool": tool_name,
            "action": decision.action.value,
            "reason": decision.reason,
            "risk_level": decision.risk_level,
        }
        self._audit_log.append(entry)
        if decision.action == GuardianAction.DENY:
            self._denied_count += 1
        elif decision.action == GuardianAction.ALLOW:
            self._allowed_count += 1

        if decision.action == GuardianAction.DENY:
            logger.warning(f"[guardian] DENIED: {entry}")
        elif decision.action == GuardianAction.NEEDS_CONFIRM:
            logger.debug(f"[guardian] NEEDS_CONFIRM: {entry}")
        else:
            logger.debug(f"[guardian] ALLOWED: {entry}")

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "allowed": self._allowed_count,
            "denied": self._denied_count,
            "confirmed": self._confirmed_count,
            "audit_log_size": len(self._audit_log),
        }

    def get_audit_log(self, last_n: int = 50) -> list[dict[str, Any]]:
        return self._audit_log[-last_n:]
