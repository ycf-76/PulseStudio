"""Reactor thin primitives: one function, one input, one output.

Each primitive wraps an existing Skill or direct API call into a minimal,
composable unit that the ReactorSession's ReAct loop can dispatch.
No prompt templates, no fallback logic, no orchestration — just raw capability.

Three domains:
  Domain 1 — File ops (reactor-native): read/write/edit/bash/glob/grep
  Domain 2 — Platform API (XHS-specific): search/publish/image_gen/image_analyze
  Domain 3 — LLM reasoning (universal): llm_generate
"""

from __future__ import annotations

import inspect
import json
import logging
import re
from abc import ABC, abstractmethod
from typing import Any

from pydantic import ValidationError
from pydantic_core import PydanticUndefined

from app.engine.schemas import Permission

logger = logging.getLogger(__name__)

_MEM_ADDR_RE = re.compile(r"0x[0-9A-Fa-f]{4,}")


def _sanitize_error_text(text: str) -> str:
    """抹掉异常文本里的对象内存地址（防 LLM 把「有个对象」误读成链路故障）。"""
    return _MEM_ADDR_RE.sub("0x…", text)


def _field_type_name(finfo: Any) -> str:
    """字段注解 → prompt 友好类型名（bool 判定须先于 int：bool 是 int 子类）。"""
    ann = getattr(finfo, "annotation", None)
    if ann is None:
        return "str"
    if getattr(ann, "__origin__", None) is list:
        return "list"
    if ann is bool or (isinstance(ann, type) and issubclass(ann, bool)):
        return "bool"
    if ann is int or (isinstance(ann, type) and issubclass(ann, int)):
        return "int"
    if ann is float or (isinstance(ann, type) and issubclass(ann, float)):
        return "float"
    return "str"


def _schema_param_spec(schema_cls: Any) -> list[dict[str, Any]]:
    """input_schema → [{name, type, required, description}]；无 schema 返回 []。"""
    fields = dict(getattr(schema_cls, "model_fields", None) or {})
    spec: list[dict[str, Any]] = []
    for fname, finfo in fields.items():
        spec.append({
            "name": fname,
            "type": _field_type_name(finfo),
            "required": getattr(finfo, "default", PydanticUndefined) is PydanticUndefined,
            "description": (getattr(finfo, "description", "") or "").strip(),
        })
    return spec


def _skill_input_error(
    skill_name: str,
    schema_cls: Any,
    received: dict[str, Any],
    err: ValidationError,
) -> dict[str, Any]:
    """把 Pydantic 校验失败翻译成 LLM 可执行的修复指令。

    为什么不用 str(err)：Pydantic 原文会带 input_value={'…': <object at 0x…>}，
    LLM 读到的是「框架往里塞了个对象然后坏了」，于是把「我漏了字段」误判成
    「链路 bug」并放弃重试（2026-10-29 某搜索技能五连败实测）。
    这里只取 loc/msg/type，并把该技能的全部字段规格一并返回。
    """
    issues: list[dict[str, Any]] = []
    for item in err.errors():
        loc = item.get("loc") or ()
        issues.append({
            "field": ".".join(str(p) for p in loc) or "(root)",
            "reason": str(item.get("msg") or ""),
            "type": str(item.get("type") or ""),
        })

    spec = _schema_param_spec(schema_cls)
    required = [p["name"] for p in spec if p["required"]]
    missing = [i["field"] for i in issues if i["type"] == "missing"]
    received_keys = sorted(
        k for k in received if not k.startswith("_") and k != "llm"
    )

    if missing:
        head = f"missing required field(s): {', '.join(missing)}"
    else:
        head = "; ".join(f"{i['field']}: {i['reason']}" for i in issues) or "invalid arguments"

    example = {p["name"]: f"<{p['type']}>" for p in spec if p["required"]}
    error = (
        f"skill {skill_name} rejected your arguments — {head}. "
        f"required fields: {', '.join(required) or '(none)'}; "
        f"you passed: {', '.join(received_keys) or '(nothing)'}. "
        f"Retry with: "
        f'{{"skill": "{skill_name}", "arguments": {json.dumps(example, ensure_ascii=False)}}}'
    )
    return {
        "ok": False,
        "error": error,
        "missing_fields": missing,
        "invalid_fields": issues,
        "received_fields": received_keys,
        "expected_params": spec,
        "retryable": True,
        "hint": "这是参数问题，不是系统故障：按 expected_params 补齐字段后重试，不要原样重发。",
    }


class Primitive(ABC):
    """Thin primitive base class.

    Unlike Skill, a Primitive:
    - Has no prompt template, no fallback, no internal orchestration
    - Does one thing and returns a dict
    - Is composable: the LLM decides which primitives to call in what order
    """

    name: str = ""
    description: str = ""
    required_permissions: list[Permission] = []

    @abstractmethod
    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        raise NotImplementedError

    def info(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "required_permissions": [p.value for p in self.required_permissions],
        }


# ============================================================================
# Domain 1: File operations (reactor-native)
# ============================================================================


class FileReadPrimitive(Primitive):
    name = "file_read"
    description = (
        "读取文件内容。输入 path（相对当前工作目录 cwd），"
        "可选 offset（起始行号）和 limit（最多读取行数）。"
        "返回文件内容、行数、文件大小。"
    )
    required_permissions = [Permission.FILE_READ]

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        # 内核核心实现（file_ops_core），不再 import 技能层
        import asyncio as _aio

        from app.engine.reactor.file_ops_core import read_file
        return await _aio.to_thread(
            read_file, kwargs.get("path", ""), kwargs.get("offset", 0), kwargs.get("limit", 200)
        )


class FileWritePrimitive(Primitive):
    name = "file_write"
    description = (
        "写入文件（原子写入，不会写到一半崩溃）。"
        "输入 path 和 content。返回写入结果。"
    )
    required_permissions = [Permission.FILE_WRITE]

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        import asyncio as _aio

        from app.engine.reactor.file_ops_core import write_file
        return await _aio.to_thread(write_file, kwargs.get("path", ""), kwargs.get("content", ""))


class FileEditPrimitive(Primitive):
    name = "file_edit"
    description = (
        "精确替换文件中的文本片段。"
        "输入 path、old_string、new_string。"
        "old_string 必须在文件中唯一匹配，否则拒绝替换。"
    )
    required_permissions = [Permission.FILE_WRITE]

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        import asyncio as _aio

        from app.engine.reactor.file_ops_core import edit_file
        return await _aio.to_thread(
            edit_file, kwargs.get("path", ""), kwargs.get("old_string", ""), kwargs.get("new_string", "")
        )


class BashPrimitive(Primitive):
    name = "bash"
    description = (
        "执行 shell 命令。白名单限制：git/ls/cat/head/tail/wc/find/grep/glob/ffmpeg 等"
        "（不含 python/node/npm）。只读命令由 Python 原生实现，跨平台一致。"
        "输入 command，可选 cwd（相对当前工作目录，默认当前工作目录）和 timeout（超时秒数，默认15）。"
        "返回 stdout、stderr、exit_code。"
    )
    required_permissions = [Permission.BASH_EXEC]

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        from app.tools.dev_tools import BashSkill
        return await BashSkill().execute(kwargs)


class GlobPrimitive(Primitive):
    name = "glob"
    description = (
        "按模式搜索文件路径。输入 pattern（如 **/*.py, src/**/*.tsx），"
        "可选 path（相对当前工作目录 cwd 的根，默认 .）和 limit（最大匹配数）。返回文件路径列表。"
    )
    required_permissions = [Permission.FILE_READ]

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        from app.tools.dev_tools import GlobSkill
        return await GlobSkill().execute(kwargs)


class GrepPrimitive(Primitive):
    name = "grep"
    description = (
        "按正则搜索文件内容。输入 pattern（正则表达式）和 path（相对当前工作目录 cwd 的文件或目录），"
        "可选 glob（文件名过滤）、ignore_case、max_matches。返回匹配行。"
    )
    required_permissions = [Permission.FILE_READ]

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        from app.tools.dev_tools import GrepSkill
        return await GrepSkill().execute(kwargs)


# ============================================================================
# Domain 2: Platform API — 已迁移至 skill_call
# ============================================================================
# XhsSearchPrimitive  → skill_call(搜索类技能)
# ImageGenPrimitive   → skill_call(生图类技能)
# ImageReadPrimitive  → skill_call(skill="file_read") + base64
# ImageAnalyzePrimitive → skill_call(图片理解类技能)
#
# 这些业务能力不再作为内核 Primitive，统一走 SkillCallPrimitive 桥接。
# 如需恢复，在 capabilities/ 下注册对应 Skill 即可。


# ============================================================================
# Domain 3: LLM reasoning (universal)
# ============================================================================


class LLMGeneratePrimitive(Primitive):
    name = "llm_generate"
    description = (
        "调用大语言模型生成文本。可用于任何文本任务："
        "文案撰写、内容分析、合规审核、翻译、规划、代码生成等。"
        "输入 prompt（你构造的提示词），可选 max_tokens（默认2000）、"
        "temperature（默认0.7）。返回生成的文本和 token 用量。"
    )
    required_permissions = []

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        from app.engine.factory import get_deepseek_llm

        prompt = kwargs.get("prompt", "")
        max_tokens = kwargs.get("max_tokens", 2000)
        temperature = kwargs.get("temperature", 0.7)

        if not prompt:
            return {"ok": False, "error": "prompt is required"}

        llm = get_deepseek_llm(temperature=temperature)
        if llm is None:
            return {"ok": False, "error": "LLM unavailable (no API key or adapter)"}

        try:
            result = await llm.chat(
                messages=[{"role": "user", "content": prompt}],
            )
            return {
                "ok": True,
                "text": result.get("content", ""),
                "reasoning_content": result.get("reasoning_content"),
                "token_usage": result.get("token_usage", 0),
            }
        except Exception as e:
            logger.exception("[llm_generate] LLM call failed")
            return {"ok": False, "error": str(e)}


class ViralScorePrimitive(Primitive):
    name = "viral_score"
    description = (
        "对搜索结果做爆款因子评分（纯规则计算，0 LLM token 消耗）。"
        "输入 results（搜索结果列表）。返回带评分指标的结果和排序。"
    )
    required_permissions = []

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        from app.tools.viral_analyzer import analyze_viral

        results = kwargs.get("results", [])
        if not results:
            return {"ok": False, "error": "results is required and must be non-empty"}

        try:
            scored = analyze_viral(results)
            return {"ok": True, "results": scored}
        except Exception as e:
            logger.exception("[viral_score] analysis failed")
            return {"ok": False, "error": str(e)}


class ContentCheckPrimitive(Primitive):
    name = "content_check"
    description = (
        "内容合规检查（纯规则，0 LLM token 消耗）。"
        "输入 text（待检查文本）。返回是否通过和具体问题列表。"
    )
    required_permissions = []

    _SENSITIVE_PATTERNS: list[str] = [
        "包治百病",
        "根治",
        "特效药",
        "投资回报",
        "稳赚不赔",
        "年化收益",
        "包过",
        "代考",
    ]

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        text = kwargs.get("text", "")
        if not text:
            return {"ok": True, "passed": True, "issues": []}

        issues: list[dict[str, str]] = []
        for pattern in self._SENSITIVE_PATTERNS:
            if pattern in text:
                issues.append({
                    "type": "sensitive_word",
                    "pattern": pattern,
                    "severity": "high",
                })

        return {
            "ok": True,
            "passed": len(issues) == 0,
            "issues": issues,
        }


class SkillCallPrimitive(Primitive):
    """调用平台技能（Skill）——对话 ReAct 入口与 Skill 体系的唯一桥（P0-1）。

    背景：reactor 原有 13 个基础工具够不到 SkillRegistry 里的业务技能，
    内核由此把 SkillRegistry 的全部技能暴露给 LLM：
    LLM 输出 {"name": "skill_call", "arguments": {"skill": "技能名",
    "arguments": {...}}} → 这里解析 class → 注入 user_id/workflow_id → execute。
    技能选择与专属用法指引由系统提示的「可用技能」段提供
    （来自各技能的 usage_hint，内核零业务知识）。

    - skill 返回 dict 原样作为 observation；抛错按 {"ok": False, "error"} 返回，
      让 ReAct 循环可以观察失败并换路（降级/重试），不中断循环。
    """

    name = "skill_call"
    description = (
        "调用平台技能（完整业务能力）。技能清单与各技能的专属用法说明"
        "见系统提示的「可用技能」一节，参数以该技能的 input schema 为准。"
        "返回技能执行结果或错误信息（error 时可换参数重试）。"
    )
    required_permissions = []

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        from app.core.skill_registry import (
            ensure_builtin_skills_registered,
            get_skill_class_by_name,
        )
        from app.engine.schemas import WorkflowContext

        ensure_builtin_skills_registered()

        skill_name = (kwargs.get("skill") or "").strip()
        arguments = kwargs.get("arguments")
        if not isinstance(arguments, dict):
            arguments = {}
        user_id = str(kwargs.get("_user_id") or "")
        workflow_id = str(kwargs.get("_workflow_id") or "")

        if not skill_name:
            _fallback_name = (kwargs.get("name") or "").strip()
            if _fallback_name and _fallback_name != "skill_call":
                skill_name = _fallback_name
                logger.info(f"[skill_call] promoted kwargs.name='{_fallback_name}' → skill_name")

        if not skill_name:
            return {"ok": False, "error": "skill_call requires 'skill' (skill name). Correct format: {\"name\": \"skill_call\", \"arguments\": {\"skill\": \"技能名\", \"arguments\": {该技能参数}}}. The skill name must come from the available skills list in your context, not invented."}
        if not user_id:
            return {"ok": False, "error": "skill_call missing user context (_user_id)"}

        try:
            skill_cls = get_skill_class_by_name(skill_name, user_id=user_id)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        if skill_cls is None:
            return {
                "ok": False,
                "error": f"unknown skill: {skill_name}",
                "hint": "技能名见系统提示「可用技能」清单",
            }

        # 空探针：技能有必填参数但本次一个都没给 → 不执行，只回参数说明书。
        # 为什么：目录（一层）剥掉了独立参数行，LLM 首调常只报技能名（
        # 2026-10-29 实测连续多轮空参数失败）。若照样执行，必然以校验失败
        # 结束、白白消耗一轮迭代；直接返回 expected_params 相当于把「读
        # SKILL.md」这一步做进框架，与 skills.md 的承诺一致。
        # 全可选 schema（如 .md 驱动的四字段技能）不走此分支：空参数是合法调用。
        _spec = _schema_param_spec(getattr(skill_cls, "input_schema", None))
        _required = [p for p in _spec if p["required"]]
        if _required and not arguments:
            logger.info(
                "[skill_call] probe call for %s (no arguments, %d required) → returning spec only",
                skill_name,
                len(_required),
            )
            return {
                "ok": True,
                "probe": True,
                "skill": skill_name,
                "note": (
                    "未执行：该技能有必填参数，但本次调用没有提供 arguments。"
                    "请按 required_params 补齐后重新调用 skill_call。"
                ),
                "required_params": _required,
                "expected_params": _spec,
            }

        skill = skill_cls()
        inputs: dict[str, Any] = dict(arguments)
        turn_id = str(kwargs.get("_turn_id") or "")
        inputs["_context"] = WorkflowContext(
            workflow_id=workflow_id,
            node_id="reactor_skill_call",
            user_id=user_id,
            account_id="",
        )
        inputs["_workflow_id"] = workflow_id
        if turn_id:
            inputs["_turn_id"] = turn_id

        if "llm" not in inputs or inputs["llm"] is None:
            from app.engine.factory import get_deepseek_llm
            _llm = get_deepseek_llm(model="deepseek-chat")
            if _llm is not None:
                inputs["llm"] = _llm
            else:
                logger.warning(
                    "[skill_call] get_deepseek_llm(model='deepseek-chat') returned None; "
                    "skill %s may fail with 'LLM not available'",
                    skill_name,
                )

        try:
            raw = skill.execute(inputs)
            if inspect.isasyncgen(raw):
                result = None
                async for chunk in raw:
                    result = chunk
                if result is None:
                    result = {"ok": True, "message": "async generator yielded nothing"}
            else:
                result = await raw
        except ValidationError as e:
            logger.warning("[skill_call] skill %s input validation failed: %s", skill_name, e.errors())
            return _skill_input_error(
                skill_name, getattr(skill_cls, "input_schema", None), arguments, e
            )
        except Exception as e:
            logger.exception("[skill_call] skill %s failed", skill_name)
            return {
                "ok": False,
                "error": _sanitize_error_text(
                    f"skill {skill_name} failed: {type(e).__name__}: {e}"
                ),
            }

        if not isinstance(result, dict):
            result = {"ok": True, "value": result}
        result.setdefault("ok", True)
        result["_skill"] = skill_name
        return result


class AskUserQuestionPrimitive(Primitive):
    """LLM 主动向用户提问——澄清决策权的主体（Layer 1）。

    和 Claude Code 的 AskUserQuestion 工具对齐：
    - LLM 自己决定什么时候拿不准需要问
    - 调这个工具，框架自动暂停循环、保存状态、发射 SSE 事件
    - 用户回答后，答案作为 observation 回喂 LLM，循环继续

    返回值复用现有 _awaiting_clarification 信号格式，
    _dispatch 中已有的暂停/保存/SSE 发射逻辑完全复用。
    """
    name = "ask_user_question"
    description = (
        "当你不确定用户的创作偏好时，主动向用户提问。"
        "传入 questions 列表（每个含 question + options），"
        "系统会暂停执行、向用户展示选项卡片、等用户回答后继续。"
        "只在真正拿不准时使用——用户已经明确表达过偏好时不要重复问。"
        "一次最多问 3 个最关键的问题。"
    )
    required_permissions = []

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        from app.agents.clarification_schema import (
            ClarificationBatch,
            ClarificationQuestion,
            ClarifyOption,
            QuestionType,
        )

        questions_raw = kwargs.get("questions", [])
        if not questions_raw or not isinstance(questions_raw, list):
            return {"ok": False, "error": "ask_user_question requires 'questions' list"}

        title = kwargs.get("title", "创作偏好确认")
        skill = kwargs.get("skill", "")

        clarification_questions: list[ClarificationQuestion] = []
        for i, q in enumerate(questions_raw):
            if not isinstance(q, dict):
                continue
            options_raw = q.get("options", [])
            options: list[ClarifyOption] = []
            for opt in options_raw:
                if isinstance(opt, dict):
                    # value 缺失时回退 label，保证选项值唯一——
                    # 否则所有选项 value 同为 ""，前端单选会整列全选
                    label = str(opt.get("label", ""))
                    value = str(opt.get("value", "") or label)
                    if not value:
                        value = f"option_{len(options)}"
                    options.append(ClarifyOption(
                        label=label,
                        value=value,
                        description=str(opt.get("description", "")),
                        preview_url=str(opt.get("preview_url", "")),
                    ))
                elif isinstance(opt, str):
                    options.append(ClarifyOption(label=opt, value=opt))
            qt_raw = q.get("type", "single_choice")
            try:
                qt = QuestionType(qt_raw)
            except ValueError:
                qt = QuestionType.single_choice
            clarification_questions.append(ClarificationQuestion(
                id=q.get("id", f"q_{i}"),
                source_skill=skill,
                question=str(q.get("question", "")),
                type=qt,
                options=options,
                required=q.get("required", True),
                default=q.get("default", ""),
            ))

        if not clarification_questions:
            return {"ok": False, "error": "no valid questions provided"}

        batch = ClarificationBatch(
            batch_id=kwargs.get("batch_id", "ask_0"),
            title=title,
            description=kwargs.get("description", ""),
            questions=clarification_questions,
            batch_index=0,
            total_batches=1,
        )

        return {
            "_awaiting_clarification": True,
            "_clarification_prompt": title,
            "_clarification_skill": skill,
            "_clarification_arguments": kwargs.get("arguments", {}),
            "_clarification_batches": [batch.model_dump()],
        }


class WorkEditPrimitive(Primitive):
    name = "work_edit"
    description = (
        "修改用户当前正在查看的作品内容（仅草稿可用，采集/已发布作品是只读的）。"
        "只提交需要改的字段：title（标题）、content_text（正文）、"
        "tags（话题数组）、images（图片URL数组）、cover_img_url（封面）。"
        "未提交的字段保持原样，整体替换而非追加。"
        "写的是独立覆盖层，原始采集快照不动，随时可回溯。"
        "返回 work_patch（前端据此就地刷新作品，无需再次读取）。"
        "作品不是草稿时返回 ok=false，此时如实告诉用户当前是只读分析，不要声称已修改。"
    )
    required_permissions = [Permission.WORK_WRITE]

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        user_id = str(kwargs.get("_user_id") or "").strip()
        work_id = str(kwargs.get("work_id") or "").strip()
        if not user_id:
            return {"ok": False, "error": "缺少用户上下文，无法写入作品"}
        if not work_id:
            return {"ok": False, "error": "缺少作品上下文（work_id），无法定位要修改的作品"}

        raw: dict[str, Any] = {}
        for field in ("title", "content_text", "tags", "images", "cover_img_url"):
            value = kwargs.get(field)
            if value is not None:
                raw[field] = value
        if not raw:
            return {"ok": False, "error": "没有需要修改的字段"}

        from app.services.work_content_edit import (
            ContentEditRejected,
            apply_content_edit,
            normalize_edit,
            read_record_edit,
            save_record_edit,
        )

        try:
            edit = normalize_edit(raw)
        except ValueError as exc:
            return {"ok": False, "error": f"字段不合法：{exc}"}
        if not edit:
            return {"ok": False, "error": "没有需要修改的字段"}

        try:
            record = await save_record_edit(user_id, work_id, edit, require_status="draft")
        except LookupError as exc:
            return {"ok": False, "error": str(exc)}
        except ContentEditRejected as exc:
            return {
                "ok": False,
                "error": str(exc),
                "hint": "该作品是采集/已发布内容，本次对话只能做只读分析；要改内容请让用户先存为草稿。",
            }

        applied = apply_content_edit(
            {
                "title": record.title,
                "content_text": record.content_text,
                "tags": record.tags,
                "images": record.images,
                "cover_img_url": record.cover_img_url,
            },
            read_record_edit(record),
        )
        return {
            "ok": True,
            "content_status": record.content_status,
            "work_patch": {"work_id": work_id, "fields": edit, "applied": applied},
            "message": "已写入草稿覆盖层，原始快照保持不变",
        }


# ============================================================================
# All primitives registry
# ============================================================================

ALL_PRIMITIVES: list[Primitive] = [
    FileReadPrimitive(),
    FileWritePrimitive(),
    FileEditPrimitive(),
    BashPrimitive(),
    GlobPrimitive(),
    GrepPrimitive(),
    LLMGeneratePrimitive(),
    ViralScorePrimitive(),
    ContentCheckPrimitive(),
    SkillCallPrimitive(),
    AskUserQuestionPrimitive(),
    WorkEditPrimitive(),
]

PRIMITIVES_BY_NAME: dict[str, Primitive] = {p.name: p for p in ALL_PRIMITIVES}