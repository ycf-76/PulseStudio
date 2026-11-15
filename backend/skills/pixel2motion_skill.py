"""Pixel2Motion 接入 Skill：Logo 图片 → 矢量化 → 品牌动效动画。

把开源技能 Pixel2Motion（github.com/nolangz/pixel2motion，已安装在
<项目根>/.trae/skills/pixel2motion/）包装为本平台可调用的 produce 技能。

第三方 Skill 开发约定（见 app/core/skill_registry.py 模块注释）：
- 放在 backend/skills/ 下，重启后端自动扫描注册，无需修改 app/ 内代码
- 前端 GET /api/skills 可见；ReAct 智能体通过 trigger_words 路由 + skill_call 调用

执行管线（复用上游技能自带脚本，保证与上游质量标准一致）：
1. raster_logo_trace.py   光栅 → 多色分组矢量描摹（产出 logo.svg + IoU metrics）
2. 本文件后处理           按 SKILL.md「Motion-Ready Structure」规范注入稳定 part id + pathLength
3. 本文件生成 motion.css  默认揭示编舞（错峰 scale-pop + 溢出回弹，20/50/30 时间轴，字面量缓动）
4. animate_svg_showcase.py 打包为独立可打开的 logo_motion.html（含原子动效/回放/慢速/QA钩子）

注意：上游技能的完整质量闭环（10 轮几何迭代 + 多模态 QA + 缓动探针）是
智能体驱动的流程；本包装提供确定性的一键管线（trace → 动效 → 打包），
覆盖「给定 Logo 图直接出动效」的主路径。几何精修可由智能体拿到 svg 路径后
按 .trae/skills/pixel2motion/SKILL.md 继续。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.core.skill_base import Skill
from app.core.skill_registry import register

_SVG_NS = "http://www.w3.org/2000/svg"
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_P2M_SCRIPTS = _PROJECT_ROOT / ".trae" / "skills" / "pixel2motion" / "scripts"
_OUTPUTS_ROOT = _PROJECT_ROOT / "backend" / "outputs" / "pixel2motion"

# 动效人格预设（对应 SKILL.md Phase 1 的 motion brief）：
# ease 曲线均为字面量 cubic-bezier —— @keyframes 内 var() 会被 Chromium
# 静默丢弃降级为 linear（上游 v2 实测教训），因此直接写死。
_MOTION_PRESETS: dict[str, dict[str, Any]] = {
    "swift": {
        "label": "swift, precise, confident（工程/科技感）",
        "stagger": 0.10,       # 相邻部件错峰占部件时长比例
        "enter_bezier": "cubic-bezier(0.16, 1, 0.3, 1)",      # 快进缓停
        "overshoot": 1.03,     # 溢出幅度小 = 克制
    },
    "confident": {
        "label": "confident, bold, grounded（品牌宣言感）",
        "stagger": 0.16,
        "enter_bezier": "cubic-bezier(0.22, 1.2, 0.36, 1)",
        "overshoot": 1.08,
    },
    "playful": {
        "label": "playful, warm, bouncy（活泼/生活方式）",
        "stagger": 0.20,
        "enter_bezier": "cubic-bezier(0.34, 1.56, 0.64, 1)",
        "overshoot": 1.12,
    },
}


def _guess_preset(motion_words: str) -> str:
    """按用户写出的第一个匹配词定预设（第一个词通常是主人格）。"""
    words = (motion_words or "").lower()
    for key, keywords in (
        ("swift", ("swift", "precise", "fast", "快", "利落")),
        ("confident", ("confident", "bold", "grounded", "自信", "大胆", "宣言")),
        ("playful", ("playful", "warm", "bouncy", "活泼", "可爱", "温暖", "弹")),
    ):
        if any(w in words for w in keywords):
            return key
    return "swift"


class Pixel2MotionInput(BaseModel):
    logo_asset_id: str = Field(
        default="",
        description="用户已上传的 Logo 图片 asset_id（素材库）；与 logo_path 二选一",
    )
    logo_path: str = Field(
        default="",
        description="Logo 图片本地路径或 /uploads/... URL；与 logo_asset_id 二选一",
    )
    title: str = Field(default="", max_length=60, description="作品标题，默认取文件名")
    duration_ms: int = Field(default=1500, ge=600, le=6000, description="动效总时长（毫秒）")
    motion_words: str = Field(
        default="",
        max_length=60,
        description="品牌动效人格词（如 'swift, precise, confident'），决定缓动与节奏",
    )
    usage_context: Literal["splash", "header", "loading", "hover"] = Field(
        default="splash",
        description="用途场景：splash=开场揭示（默认）、header=页头、loading=循环、hover=微交互",
    )


def _postprocess_svg(svg_path: Path) -> int:
    """按上游 SKILL.md「Motion-Ready Structure」给每个 path 注入稳定 id + pathLength。

    动画只允许瞄准稳定 id（禁止 nth-child 结构选择器）；pathLength="1"
    让 dash 动画与真实路径长度解耦。返回部件数。
    """
    ET.register_namespace("", _SVG_NS)
    tree = ET.parse(svg_path)
    root = tree.getroot()
    part_index = 0
    for elem in root.iter(f"{{{_SVG_NS}}}path"):
        part_index += 1
        elem.set("id", f"p2m-part-{part_index}")
        elem.set("pathLength", "1")
    tree.write(svg_path, encoding="unicode", xml_declaration=False)
    return part_index


def _build_motion_css(
    parts: int,
    duration_ms: int,
    preset_key: str,
    usage_context: str,
) -> str:
    """生成默认揭示编舞 motion.css。

    时间轴遵循上游 20/50/30 法则（anticipation : action : follow-through）：
    部件时长 = 50% 总时长，错峰 stagger 覆盖前 20%，剩余 30% 自然沉降。
    所有 timing-function 均为字面量 cubic-bezier（@keyframes 内 var() 会静默失效）。
    """
    preset = _MOTION_PRESETS[preset_key]
    part_ms = int(duration_ms * 0.5)
    stagger_ms = int(part_ms * preset["stagger"])
    overshoot = preset["overshoot"]
    is_loop = usage_context == "loading"

    lines = [
        "/* Pixel2Motion default reveal choreography",
        f" * preset: {preset['label']}",
        f" * timeline: 20/50/30 of {duration_ms}ms, stagger {stagger_ms}ms/part",
        " * easing: literal cubic-bezier only (var() inside @keyframes silently",
        " *   degrades to linear in Chromium — see upstream SKILL.md v2 notes)",
        " */",
        f":root {{ --p2m-duration: {duration_ms}ms; }}",
        "",
        f"#logo-root path {{",
        "  transform-box: fill-box;",
        "  transform-origin: center;",
        "}",
        "",
    ]

    if is_loop:
        # loading/idle：无缝循环呼吸（100% 状态 == 0% 状态）
        lines += [
            "#logo-root path {",
            f"  animation: p2m-breathe {duration_ms * 2}ms ease-in-out infinite both;",
            "}",
            "",
            "@keyframes p2m-breathe {",
            "  0%   { opacity: 0.85; transform: scale(1); }",
            "  50%  { opacity: 1;    transform: scale(1.02); }",
            "  100% { opacity: 0.85; transform: scale(1); }",
            "}",
        ]
    else:
        lines.append("#logo-root path {")
        lines.append(f"  animation: p2m-reveal {part_ms}ms both;")
        lines.append("  /* per-part delay injected below */")
        lines.append("}")
        lines.append("")
        lines.append("@keyframes p2m-reveal {")
        lines.append("  0% {")
        lines.append("    opacity: 0;")
        lines.append("    transform: scale(0.72) translateY(5px);")
        lines.append(f"    animation-timing-function: {preset['enter_bezier']};")
        lines.append("  }")
        lines.append(f"  70% {{ opacity: 1; transform: scale({overshoot}); }}")
        lines.append("  100% { opacity: 1; transform: scale(1); }")
        lines.append("}")
        lines.append("")
        for i in range(parts):
            lines.append(f"#logo-root #p2m-part-{i + 1} {{ animation-delay: {i * stagger_ms}ms; }}")

    # 注意：prefers-reduced-motion 由 animate_svg_showcase.py 统一包裹，
    # motion.css 里不能再自带（脚本会拒绝重复声明）。
    return "\n".join(lines)


async def _resolve_logo_path(inputs: dict[str, Any], args: Pixel2MotionInput) -> Path | None:
    """asset_id → 素材库查询；logo_path → /uploads/ URL 或直接路径。"""
    if args.logo_asset_id:
        from sqlalchemy import select

        from app.db.models import ImageAsset
        from app.db.session import AsyncSessionLocal
        from app.services.asset_library import url_to_local_path

        context = inputs.get("_context")
        user_id = str(getattr(context, "user_id", "") or "") if context is not None else ""
        async with AsyncSessionLocal() as session:
            stmt = select(ImageAsset).where(ImageAsset.id == args.logo_asset_id)
            if user_id:
                stmt = stmt.where(ImageAsset.user_id == user_id)
            asset = (await session.execute(stmt)).scalar_one_or_none()
        if asset is None:
            return None
        return Path(url_to_local_path(asset.original_url) or "")

    if args.logo_path:
        if args.logo_path.startswith("/uploads/"):
            from app.services.asset_library import url_to_local_path

            local = url_to_local_path(args.logo_path)
            return Path(local) if local else None
        p = Path(args.logo_path)
        return p if p.is_file() else None
    return None


def _run_script(script: Path, *cli_args: str, cwd: Path, timeout: int = 180) -> None:
    proc = subprocess.run(
        [sys.executable, str(script), *cli_args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"{script.name} failed (exit {proc.returncode}):\n{proc.stdout[-800:]}\n{proc.stderr[-800:]}"
        )


@register
class Pixel2MotionSkill(Skill):
    """Logo 动效动画：光栅 Logo → 干净 SVG 矢量 → 编排揭示动效 HTML。"""

    node_type = "produce"
    name = "pixel2motion"
    display_name = "Logo动效动画"
    description = (
        "把 Logo 图片（PNG/JPG/WebP）矢量化为干净 SVG，并编排成品牌动效动画，"
        "输出可独立打开的 logo_motion.html（含回放/慢速控制与动效研究）+ logo.svg。"
        "适用于 logo 揭示动画、开场动画、品牌 intro、加载动效。"
        "需用户提供 Logo 图片（asset_id 或路径）。"
    )
    usage_hint = (
        "调用示例：skill_call(skill='pixel2motion', arguments={'logo_asset_id': '...', "
        "'motion_words': 'swift, precise, confident', 'duration_ms': 1500})。"
        "logo_asset_id 或 logo_path 必须提供一个；用户没传图时先要图，不要编造路径。"
    )
    skill_category = "create"
    skill_priority = "normal"
    trigger_words = [
        "logo动画", "logo动效", "标志动画", "标志动效", "开场动画",
        "品牌动画", "logo动图", "动画logo", "logo揭示", "加载动画", "logo reveal",
    ]
    input_schema = Pixel2MotionInput

    async def execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        if not _P2M_SCRIPTS.is_dir():
            return {
                "error": f"Pixel2Motion 技能脚本缺失：{_P2M_SCRIPTS}（先安装到 .trae/skills/pixel2motion）",
            }

        args = Pixel2MotionInput.model_validate({
            key: value for key, value in inputs.items() if key in Pixel2MotionInput.model_fields
        })

        logo = await _resolve_logo_path(inputs, args)
        if logo is None or not logo.is_file():
            return {
                "error": "未找到 Logo 图片。请用户提供 Logo 图片（上传后传 logo_asset_id），"
                         "或直接给图片路径。不要编造 asset_id。",
            }

        preset_key = _guess_preset(args.motion_words)
        title = args.title or logo.stem
        workdir = _OUTPUTS_ROOT / time.strftime("%Y%m%d_%H%M%S")
        workdir.mkdir(parents=True, exist_ok=True)

        # Phase 2（确定性部分）：光栅描摹 → logo.svg + IoU metrics
        _run_script(
            _P2M_SCRIPTS / "raster_logo_trace.py",
            str(logo),
            "--out", str(workdir),
            cwd=workdir,
        )
        metrics = json.loads((workdir / "metrics.json").read_text(encoding="utf-8"))
        svg_path = workdir / "logo.svg"

        parts = _postprocess_svg(svg_path)

        # Phase 3：默认揭示编舞 → showcase HTML
        css_path = workdir / "motion.css"
        css_path.write_text(
            _build_motion_css(parts, args.duration_ms, preset_key, args.usage_context),
            encoding="utf-8",
        )
        html_path = workdir / "logo_motion.html"
        _run_script(
            _P2M_SCRIPTS / "animate_svg_showcase.py",
            str(svg_path),
            "--css", str(css_path),
            "--out", str(html_path),
            "--title", title,
            "--duration-hint", str(args.duration_ms),
            cwd=workdir,
        )

        iou = (metrics.get("metrics") or {}).get("iou")
        return {
            "title": title,
            "status": "completed",
            "html_path": str(html_path),
            "path": str(workdir),
            "svg_path": str(svg_path),
            "css_path": str(css_path),
            "parts": parts,
            "iou": iou,
            "preset": preset_key,
            "motion_words": args.motion_words or _MOTION_PRESETS[preset_key]["label"],
            "report": (
                f"Logo 已矢量化（{parts} 个部件，IoU={iou}）并编排为 "
                f"{args.duration_ms}ms 揭示动效。成品：{html_path}。"
                f"如需按品牌人格精修几何与编舞，智能体可依据 "
                f".trae/skills/pixel2motion/SKILL.md 用 bash 继续迭代该目录下的工件。"
            ),
            "_source": "pixel2motion",
        }
