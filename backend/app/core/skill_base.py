"""Skill abstract base class.

Corresponds to architecture doc Ch.5 (Layer C).
All concrete capabilities (search/analyze/image_gen/publish) inherit this.
Red line: skills layer must NOT import LangGraph or make flow decisions.

可插拔 Skill 架构（v2）：
- 每个节点（analyze/image_gen/copywrite/audit）对应一个 node_type
- 同一个 node_type 下可注册多个 Skill 实现（如 copywrite 节点下有「活泼少女」「知性优雅」等多个 Skill）
- 第三方 Skill 通过 backend/skills/ 目录扫描自动注册
- 节点运行时按 state.model_settings.skill_name 从 registry 加载对应 Skill
- 风格/温度/模型作为 Skill 构造参数注入，不再硬编码到 prompt 字符串
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.engine.schemas import Permission
from app.agents.clarification_schema import ClarifyFieldMeta

def fix_card_html_markers(text: str) -> str:
    """Fix LLM-wrapped card HTML: remove markdown code fences around <!--card-html--> blocks."""
    import re as _re
    text = _re.sub(r'```html\s*\n(.*?<!--card-html-->.*?)```', r'\1', text, flags=_re.DOTALL)
    text = _re.sub(r'```\s*\n(.*?<!--card-html-->.*?)```', r'\1', text, flags=_re.DOTALL)
    return text


class Skill(ABC):
    """Capability-layer base class.

    每个 Skill 包装一次具体的能力调用：
    - xhs_search: 小红书搜索（via MCP）
    - vl_analyze: Qwen-VL 图像理解
    - generate_image: 通义万相/即梦图片生成
    - analyze: 选题分析（viral_analyzer + LLM 归因）
    - copywrite: 文案生成
    - audit: 合规审核

    可插拔元数据（子类通过类属性声明）：
    - node_type: 该 Skill 服务于哪个节点（"analyze"/"copywrite"/"image_gen"/"audit"）
    - name: Skill 唯一标识（如 "lively_girl_copywrite"），用于前端选择和后端路由
    - display_name: 前端展示名（如 "活泼少女风文案"）
    - description: Skill 描述（前端 tooltip 用）
    - default_config: 默认配置（温度/模型等），可被用户在右侧工作区覆盖

    子类通过 `required_permissions` 声明所需权限，由 Executor/Harness
    在 execute() 之前调用 permission_gate.require() 做门控。
    """

    # ===== 可插拔元数据（子类必须声明） =====
    node_type: str = ""
    name: str = ""
    display_name: str = ""
    description: str = ""
    default_config: dict[str, Any] = {}

    # ===== 自描述提示词系统 =====
    prompt_guidance: str = ""
    platform: str = ""
    trigger_words: list[str] = []

    # ===== 路由分类（skill_router 使用）=====
    skill_category: str = ""
    skill_priority: str = ""

    # ===== 通用元数据 =====
    input_schema: type[BaseModel] = BaseModel
    output_schema: type[BaseModel] = BaseModel
    execution_policy: Literal["direct", "mcp", "sandbox"] = "direct"
    required_permissions: list[Permission] = []

    # ===== 用户隔离 =====
    owner_user_id: str = ""

    # ===== 渐进式澄清元数据 =====
    clarify_meta: dict[str, ClarifyFieldMeta] = {}

    @abstractmethod
    async def execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Execute the capability call, return structured result.

        约定：实现里不再做权限校验（由调用方统一在 execute 之前门控）。
        """
        raise NotImplementedError

    @classmethod
    def metadata(cls) -> dict[str, Any]:
        """返回 Skill 元数据（供前端渲染下拉框、tooltip）。"""
        module_file = getattr(cls, "__module__", "")
        is_third_party = module_file.startswith("_third_party_skill_")
        return {
            "node_type": cls.node_type,
            "name": cls.name,
            "display_name": cls.display_name or cls.name,
            "description": cls.description,
            "default_config": cls.default_config,
            "is_third_party": is_third_party,
            "is_prompt_skill": issubclass(cls, PromptDrivenSkill),
            "skill_category": cls.skill_category,
            "skill_priority": cls.skill_priority,
            "owner_user_id": cls.owner_user_id,
        }


class PromptDrivenSkill(Skill):
    """SKILL.md 驱动的 Skill —— 零代码新增策划/分析/审核类能力。

    只需提供 skill_md_path（SKILL.md 文件路径），基类自动：
    1. 读取 SKILL.md 作为 prompt 模板
    2. 注入用户画像（有/无双轨）
    3. 注入 references 知识
    4. 调 LLM 生成结果
    5. 返回结构化 dict

    子类只需声明类属性，不需要写 execute()。
    """

    skill_md_path: str = ""
    card_width: int = 1080
    card_height: int = 1440

    class _InputSchema(BaseModel):
        topic: str = Field(default="", description="选题主题（选题评估用）")
        content: str = Field(default="", description="待检查内容文本（质量关卡用）")
        platform: str = Field(default="", description="目标平台（小红书/抖音/知乎等）")
        extra_context: str = Field(default="", description="补充背景信息")

    input_schema: type[BaseModel] = _InputSchema
    reference_paths: list[str] = []
    _md_cache: str | None = None
    # 四期：技能级权限声明（Permission 枚举值元组，空 = 不声明不检查）
    skill_permissions: tuple = ()
    # 四期：SKILL.md 正文长度上限（0 = 不限制），超限截断防炸上下文
    max_body_chars: int = 0
    _ref_cache: str | None = None

    CARD_HTML_OPEN = "<!--card-html-->"
    CARD_HTML_CLOSE = "<!--/card-html-->"

    _CARD_THEME_CSS = """<style>
:root{color-scheme:light dark;
--surface:#faf9f7;--card:#ffffff;--elevated:#ffffff;
--text:#403c35;--text-strong:#211e1a;--muted:#6e6960;
--border:#e8e4dc;--border-strong:#d6d0c5;
--accent:#bd4531;--accent-fill:#bd4531;--accent-fg:#ffffff;
--ok:#15803d;--warn:#b45309;--danger:#dc2626;--info:#2563eb;
--radius:10px;--radius-full:9999px;
--font-body:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
--font-mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
--accent-subtle:color-mix(in srgb,var(--accent) 10%,transparent);
--ok-subtle:color-mix(in srgb,var(--ok) 10%,transparent);
--warn-subtle:color-mix(in srgb,var(--warn) 12%,transparent);
--danger-subtle:color-mix(in srgb,var(--danger) 10%,transparent);
--info-subtle:color-mix(in srgb,var(--info) 10%,transparent)}
@media(prefers-color-scheme:dark){:root{
--surface:#0e1015;--card:#161920;--elevated:#191c24;
--text:#d4d4d8;--text-strong:#f4f4f5;--muted:#8b8b94;
--border:#1e2028;--border-strong:#2e3040;
--accent:#ff5c5c;--accent-fill:#d13c3c;--accent-fg:#ffffff;
--ok:#22c55e;--warn:#f59e0b;--danger:#ef4444;--info:#3b82f6}}
*{box-sizing:border-box;margin:0;padding:0}
html,body{margin:0;padding:0}
body{font:14px/1.5 var(--font-body);color:var(--text);background:var(--elevated)}
h1,h2,h3{margin:0 0 8px;color:var(--text-strong);font-weight:600}
h1{font-size:18px}h2{font-size:16px}h3{font-size:14px}
p{margin:0 0 8px}a{color:var(--accent)}
.ui-card{background:var(--card);border:1px solid var(--border);border-radius:var(--radius);padding:14px}
.badge{display:inline-block;font-size:12px;padding:2px 10px;border-radius:9999px;background:var(--accent-subtle);color:var(--accent)}
.metric{font-size:24px;font-weight:600;color:var(--text-strong)}
.muted{color:var(--muted)}
</style>"""

    @staticmethod
    def _strip_non_html_prefix(text: str) -> str:
        """剥离 LLM 输出中 HTML 标签前的说明性文字。

        LLM 有时会在 <!--card-html--> 标记内部开头添加设计思路/配色解释等文字，
        如 '>为什么是青蓝...' 或 'DEVELOPER TOOL · CLI' 等。
        此方法找到第一个 < 字符（HTML 标签开始），删除之前所有非 HTML 文本。
        """
        text = text.strip()
        if text.startswith("<"):
            return text
        first_tag = text.find("<")
        if first_tag <= 0:
            return text
        prefix = text[:first_tag].strip()
        if prefix:
            import logging
            logging.getLogger(__name__).warning(
                f"[_wrap_card_html] Stripped non-HTML prefix ({len(prefix)} chars): "
                f"{prefix[:80]}{'...' if len(prefix) > 80 else ''}"
            )
        return text[first_tag:]

    @staticmethod
    def _strip_non_html_suffix(text: str) -> str:
        """剥离 LLM 输出中 HTML 标签后的说明性文字。"""
        text = text.strip()
        last_close = text.rfind(">")
        if last_close < 0 or last_close >= len(text) - 1:
            return text
        suffix = text[last_close + 1:].strip()
        if suffix and not suffix.startswith("<"):
            import logging
            logging.getLogger(__name__).warning(
                f"[_wrap_card_html] Stripped non-HTML suffix ({len(suffix)} chars): "
                f"{suffix[:80]}{'...' if len(suffix) > 80 else ''}"
            )
        return text[:last_close + 1]

    @staticmethod
    def _wrap_card_html(html_block: str, viewport_width: int = 1080) -> str:
        """包裹卡片 HTML 为完整文档。

        卡片 HTML 在 iframe 中渲染，必须是自包含的——
        不注入外部主题 CSS，避免 .card / body / * 等选择器冲突覆盖卡片自身样式。
        仅补齐缺失的 <meta> 标签。

        viewport_width 默认 1080（小红书卡片），图表/思维导图等宽屏内容传更大值。
        前端 iframe 通过 transform:scale() 缩放到容器大小。

        自动清理：剥离 LLM 混入的说明性文字（HTML 标签前后的非 HTML 文本）。
        """
        stripped = PromptDrivenSkill._strip_non_html_prefix(html_block)
        viewport_meta = f"width={viewport_width},initial-scale=1"

        if stripped.lower().startswith("<!doctype") or stripped.lower().startswith("<html"):
            if "<meta " not in stripped[:500]:
                if "<head>" in stripped:
                    head_open = stripped.index("<head>") + len("<head>")
                    stripped = stripped[:head_open] + f'<meta charset="utf-8"><meta name="viewport" content="{viewport_meta}">' + stripped[head_open:]
            else:
                import re
                stripped = re.sub(r'<meta\s+name=["\']viewport["\'][^>]*>', f'<meta name="viewport" content="{viewport_meta}">', stripped, flags=re.IGNORECASE)
            return PromptDrivenSkill._strip_non_html_suffix(stripped)

        wrapped = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="{viewport_meta}"></head><body>{stripped}</body></html>"""
        return PromptDrivenSkill._strip_non_html_suffix(wrapped)

    @staticmethod
    def _extract_card_pages(raw: str) -> tuple[list[dict], list[str]]:
        """从 LLM 输出中提取 <!--card-html--> 块，构建 card_draft.pages 结构。

        每个 <!--card-html-->...<!--/card-html--> 块对应一张卡片页面。
        借鉴 Pulse show_widget：对提取的 HTML 调用 _wrap_card_html 包裹主题 CSS。
        返回 (pages, html_urls)，无卡片时返回 ([], [])。
        """
        import re
        import uuid

        if not raw or PromptDrivenSkill.CARD_HTML_OPEN not in raw:
            return [], []

        pattern = re.escape(PromptDrivenSkill.CARD_HTML_OPEN) + r"([\s\S]*?)" + re.escape(PromptDrivenSkill.CARD_HTML_CLOSE)
        blocks = re.findall(pattern, raw)
        if not blocks:
            return [], []

        pages = []
        html_urls = []
        for i, html_block in enumerate(blocks):
            html_block = html_block.strip()
            if not html_block:
                continue
            wrapped = PromptDrivenSkill._wrap_card_html(html_block, PromptDrivenSkill.card_width)
            page_id = f"page_{uuid.uuid4().hex[:8]}"
            page_type = "cover" if i == 0 else ("ending" if i == len(blocks) - 1 else "content")
            pages.append({
                "id": page_id,
                "type": page_type,
                "mediaType": "html",
                "title": f"卡片 {i + 1}",
                "htmlContent": wrapped,
                "width": PromptDrivenSkill.card_width,
                "height": PromptDrivenSkill.card_height,
            })
            html_urls.append(f"card-html://inline/{page_id}")

        return pages, html_urls

    @staticmethod
    def _audit_card_pages(pages: list[dict]) -> list[str]:
        """对提取的卡片 HTML 做文本级预检（不需要浏览器渲染）。

        检测项：
        1. margin-top:auto 推底栏（死空白主因）
        2. flex:1 在非布局容器上（.body/.xhs-card 的 flex:1 是合理的布局撑满，不算违规）
        3. justify-content:center / align-items:center 在根容器
        4. 缺少风格专属装饰关键词
        5. 大标题用了粗黑体(font-weight:700-900 + font-size>80px)
        """
        import re as _re
        warnings: list[str] = []

        for i, page in enumerate(pages):
            html = page.get("htmlContent", "")
            page_type = page.get("type", "content")
            label = f"card_{i+1}({page_type})"

            margin_auto_hits = list(_re.finditer(r"margin-top\s*:\s*auto", html))
            for hit in margin_auto_hits:
                start = max(0, hit.start() - 200)
                context = html[start:hit.end() + 50]
                is_layout_body = bool(_re.search(r"\.(body|main|content)\s*\{", context))
                is_inline_spacer = bool(_re.search(r'<div\s+style="[^"]*margin-top\s*:\s*auto', context))
                if not is_layout_body:
                    if is_inline_spacer:
                        warnings.append(f"{label}: inline margin-top:auto 空div推底(死空白)")
                    else:
                        warnings.append(f"{label}: margin-top:auto 推底栏(可能死空白)")

            flex1_hits = list(_re.finditer(r"flex\s*:\s*1\b", html))
            for hit in flex1_hits:
                start = max(0, hit.start() - 200)
                context = html[start:hit.end() + 50]
                is_layout_container = bool(_re.search(
                    r"\.(body|xhs-card|main|content-area|sbody|rbody|rt|sc|sum)\s*\{", context
                ))
                is_inline_equal = bool(_re.search(r'style="[^"]*flex\s*:\s*1', context))
                if not is_layout_container and not is_inline_equal:
                    warnings.append(f"{label}: flex:1 在非布局容器上(可能死空白)")

            root_center = _re.search(
                r"\.xhs-card\s*\{[^}]*justify-content\s*:\s*center", html
            )
            if root_center:
                warnings.append(f"{label}: 根容器 justify-content:center")

            if "terminal" in html.lower() or "#0d1117" in html or "#3fb950" in html:
                if "winbar" not in html and "window" not in html.lower() and "dot" not in html:
                    warnings.append(f"{label}: 终端风格缺窗口标题栏(winbar/三圆点)")
                if "$" not in html and ">" not in html:
                    warnings.append(f"{label}: 终端风格缺提示符($/>)")

            is_y2k = (
                "#FFD500" in html or "#C5E803" in html or "#FF5CA8" in html
                or "dopamine" in html.lower() or "y2k" in html.lower()
            )
            is_journal = (
                "journal" in html.lower() or "scrapbook" in html.lower()
                or "手账" in html or "胶带" in html
            )
            if is_journal and not is_y2k:
                if "tape" not in html and "clip-path" not in html:
                    warnings.append(f"{label}: 手账风格缺胶带条(tape/clip-path)")
                if "rotate" not in html:
                    warnings.append(f"{label}: 手账风格缺便利贴旋转(rotate)")

            if "inkwash" in html.lower() or "#a8322d" in html or "#f3efe4" in html:
                if "seal" not in html and "印" not in html:
                    warnings.append(f"{label}: 墨韵风格缺朱砂印章(seal)")

            if not is_y2k:
                bold_title = _re.search(
                    r"font-weight\s*:\s*[89]\d\d.*font-size\s*:\s*(?:[9]\d|\d{3,})px", html
                )
                if not bold_title:
                    bold_title = _re.search(
                        r"font-size\s*:\s*(?:[9]\d|\d{3,})px.*font-weight\s*:\s*[89]\d\d", html
                    )
                if bold_title:
                    warnings.append(f"{label}: 大标题用粗黑体(违反字越大越细)")

        return warnings

    _FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

    @classmethod
    def _load_skill_md(cls) -> str:
        cache = cls.__dict__.get("_md_cache")
        if cache is not None:
            return cache
        from pathlib import Path
        p = Path(__file__).resolve().parent.parent / "agents" / "prompts" / "skills" / cls.skill_md_path
        raw = p.read_text(encoding="utf-8") if p.exists() else ""
        match = cls._FRONTMATTER_RE.match(raw)
        content = raw[match.end():] if match else raw
        cls._md_cache = content
        return content

    @classmethod
    def _load_references(cls) -> str:
        cache = cls.__dict__.get("_ref_cache")
        if cache is not None:
            return cache
        from pathlib import Path
        parts: list[str] = []
        base = Path(__file__).resolve().parent.parent / "agents" / "prompts"
        for ref in cls.reference_paths:
            rp = base / ref
            if rp.exists():
                content = rp.read_text(encoding="utf-8").strip()
                if content:
                    parts.append(f"---\n# {ref}\n\n{content}")
        result = "\n\n".join(parts)
        cls._ref_cache = result
        return result

    @staticmethod
    def _fix_dead_whitespace(card_pages: list[dict]) -> list[dict]:
        """自动修复 LLM 生成的死空白问题。

        策略：
        1. 移除 margin-top: auto（头重脚轻根因）→ 替换为 padding-top: 40px
        2. 移除 flex:1 空div → 删除该 div
        3. 移除 inline style 里的 flex:1 → 删除该属性
        4. 清理重复 padding-top（修复后可能产生重复声明）
        5. 对 body padding-bottom 过大的，缩小到合理值
        """
        import re as _re
        fixed_pages = []
        for page in card_pages:
            html = page.get("htmlContent", "")
            if not html:
                fixed_pages.append(page)
                continue

            html = _re.sub(
                r'margin-top\s*:\s*auto',
                'padding-top:40px',
                html
            )

            html = _re.sub(
                r'<div\s+style="[^"]*flex\s*:\s*1[^"]*"\s*>\s*</div>',
                '',
                html
            )

            html = _re.sub(
                r'(<div\s+style="[^"]*)flex\s*:\s*1;?\s*([^"]*")',
                r'\1\2',
                html
            )

            html = _re.sub(
                r'((?:padding-top:[^;]+;\s*){2,})',
                lambda m: _re.sub(
                    r'padding-top:\s*\d+px',
                    'padding-top:40px',
                    m.group(1).split(';')[0] + ';'
                ),
                html
            )

            html = _re.sub(
                r'padding-bottom\s*:\s*(\d+)px',
                lambda m: f'padding-bottom:{min(int(m.group(1)), 40)}px',
                html
            )

            page = {**page, "htmlContent": html}
            fixed_pages.append(page)
        return fixed_pages

    async def execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        _model_tier = getattr(self, "model_tier", "")
        _task_type = getattr(self, "task_type", "")
        _temp_override = getattr(self, "temperature_override", None)

        llm = None

        # 1) 「模型配置」页的显式指定优先（技能专属模型 > 全局默认模型）
        try:
            from app.services.model_assignment import get_cached_skill_model_with_default

            _ctx = inputs.get("_context")
            if _ctx is None:
                _uid = ""
            elif isinstance(_ctx, dict):
                _uid = str(_ctx.get("user_id", "") or "")
            else:
                _uid = str(getattr(_ctx, "user_id", "") or "")

            _assigned = get_cached_skill_model_with_default(_uid, self.node_type, self.name)
            if _assigned:
                from app.engine.factory import get_llm_for_model

                llm = await get_llm_for_model(_assigned, user_id=_uid, temperature=_temp_override)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(
                f"[{getattr(self, 'name', '?')}] model assignment resolve failed: {e}"
            )
            llm = None

        # 2) 技能自身声明的 model_tier / task_type 路由
        if llm is None and (_model_tier or _task_type):
            try:
                from app.adapters.model_router import get_model_router, ChatRequest, TaskType
                router = get_model_router()
                _task_enum = TaskType.GENERAL
                if _task_type:
                    try:
                        _task_enum = TaskType(_task_type)
                    except ValueError:
                        pass
                request = ChatRequest(
                    messages=[],
                    task_type=_task_enum,
                    require_high_quality=(_model_tier == "premium"),
                    require_fast_response=(_model_tier == "budget"),
                )
                selected = router._select_model(request)
                if selected:
                    llm = router.resolve_llm(model=selected.model_id, temperature=_temp_override)
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(
                    f"[{getattr(self, 'name', '?')}] ModelRouter resolve failed: {e}, falling back to main LLM"
                )

        if not llm:
            llm = inputs.get("llm")
        if not llm:
            return {"error": "LLM not available", "verdict": "❌ LLM不可用"}

        skill_md = self._load_skill_md()
        if not skill_md:
            return {"error": f"SKILL.md not found: {self.skill_md_path}", "verdict": "❌ 配置缺失"}

        # 四期摘要降级：超长正文截断，单技能不炸上下文
        _max_chars = int(getattr(self, "max_body_chars", 0) or 0)
        if _max_chars and len(skill_md) > _max_chars:
            skill_md = (
                skill_md[:_max_chars]
                + f"\n\n[⚠️ SKILL.md 正文超长已截断：原文 {len(skill_md)} 字符，注入前 {_max_chars}（可在 frontmatter 调 max_body_chars）]"
            )

        references = self._load_references()

        user_profile = inputs.get("user_profile") or {}
        profile_block = ""
        if user_profile:
            from app.api.schemas.profile import UserProfile
            try:
                profile_obj = UserProfile(**user_profile)
                profile_block = profile_obj.to_prompt_context()
            except Exception:
                profile_block = ""
        no_profile_hint = ""
        if not profile_block:
            no_profile_hint = "\n\n[提示：用户未提供创作者画像，使用通用标准评估。提供画像可获得更精准的匹配评估。]"

        topic = inputs.get("topic", "")
        content = inputs.get("content", "")
        platform = inputs.get("platform", "")
        extra_context = inputs.get("extra_context", "")
        style = inputs.get("style", "")

        user_msg_parts = []
        if style:
            user_msg_parts.append(f"指定风格: {style}（必须严格使用此风格，不许替换）")
        if topic:
            user_msg_parts.append(f"选题: {topic}")
        if content:
            user_msg_parts.append(f"待检查内容:\n{content}")
        if platform:
            user_msg_parts.append(f"目标平台: {platform}")
        if extra_context:
            user_msg_parts.append(f"补充背景: {extra_context}")
        if not user_msg_parts:
            user_msg_parts.append("(未提供具体输入，请根据上下文执行)")
        user_msg = "\n".join(user_msg_parts)

        system_parts = [skill_md.strip()]
        if references:
            system_parts.append(f"\n\n## 领域知识参考\n\n{references}")
        if profile_block:
            system_parts.append(f"\n\n## 创作者画像\n\n{profile_block}")
        system_parts.append(no_profile_hint)
        system_prompt = "\n".join(system_parts)

        try:
            from app.engine.governance.skill_hooks import skill_governance
            await skill_governance.pre_llm_call(estimated_tokens=3000)

            is_card_skill = "card" in self.name or "poster" in self.name or "infographic" in self.name
            card_max_tokens = 16384 if is_card_skill else None

            # 2026-10-17 修复：max_tokens 只在显式需要时才传。原先无条件
            # 传（非卡片 skill 时为 None），会打死所有只声明
            # `chat(messages, response_format)` 的窄签名 LLM 实现
            # （TypeError 被外层 except 吞成 fallback，静默降级）。
            chat_kwargs: dict[str, Any] = {
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_msg},
                ],
            }
            if card_max_tokens is not None:
                chat_kwargs["max_tokens"] = card_max_tokens
            resp = await llm.chat(**chat_kwargs)
            raw = resp.get("content", "")
            skill_governance.post_llm_call(tokens_used=len(raw) * 2)

            # DSML 残片防护（2026-11-03）：PromptDrivenSkill 是单次裸 LLM 调用、
            # 无内嵌工具执行器。若 SKILL.md 指示内层模型"调工具"（如 web_fetch），
            # DeepSeek 会吐出原生 <｜｜DSML｜｜ invoke> tool-call token——没人解析
            # 执行，原样落进 content。绝不能把这种协议碎片当 report 流给上层
            # Agent（2026-11-03 热点链路全失效根因之一）。返回 error 让上层
            # ReAct 观察失败并降级换路。
            if "｜｜DSML｜｜" in raw:
                import logging
                logging.getLogger(__name__).warning(
                    f"[{self.name}] DSML tool-call residue in LLM output "
                    f"(prompt skill has no inner tool executor), refusing "
                    f"to return as report: {raw[:200]!r}"
                )
                return {
                    "ok": False,
                    "error": (
                        f"技能 '{self.name}' 的内层模型试图调用不存在的工具，"
                        "输出的是未执行的工具调用碎片（DSML 残片）。"
                        "该技能无法完成需要真实数据抓取的任务——请换用带真实"
                        "执行器的技能，或改由你（主 Agent）自己完成该子任务。"
                    ),
                    "verdict": "❌ 内层模型输出了未执行的工具调用碎片",
                    "_source": "llm",
                }

            safety_warnings = skill_governance.content_safety_check(raw)
            if safety_warnings:
                from app.engine.governance.skill_hooks import logger as gov_logger
                gov_logger.warning(f"[{self.name}] content safety: {safety_warnings}")

            raw = fix_card_html_markers(raw)

            card_pages, html_urls = self._extract_card_pages(raw)

            if card_pages and is_card_skill:
                audit_warnings = self._audit_card_pages(card_pages)
                if audit_warnings:
                    import logging
                    logging.getLogger(__name__).warning(
                        f"[{self.name}] card audit: {audit_warnings}"
                    )
                card_pages = self._fix_dead_whitespace(card_pages)

            result = {
                "report": raw,
                "platform": platform or "未指定",
                "has_profile": bool(profile_block),
                "_source": "llm",
            }
            if topic:
                result["topic"] = topic
            if content:
                result["content_length"] = len(content)
            if card_pages:
                result["card_draft"] = {
                    "title": topic or "",
                    "template": self.name,
                    "pages": card_pages,
                    "htmlUrls": html_urls,
                }
            return result
        except Exception as e:
            import logging
            logging.getLogger(__name__).exception(f"[{self.name}] LLM call failed: {e}")
            return {"error": str(e), "verdict": "❌ 评估失败", "_source": "fallback"}