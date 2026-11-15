"""VL analyze skill (Qwen-VL).

Analyzes uploaded images and returns structured tags.

2026-11-03 修复：原先 execute 是 Phase 3 占位（self.llm 未注入直接 raise），
经 reactor skill_call 调用时 skill_call 只往 inputs 塞 deepseek-chat 文本 LLM，
本技能永远拿不到 VL 适配器 → 必然失败。现改为：
1. self.llm 显式注入时优先使用（保持构造器契约）；
2. 否则惰性构建 QwenVLAdapter（dashscope_api_key + 分级路由 VL 模型），
   模块级缓存复用。skill_call 注入的文本 LLM 不可看图，不采用。
3. 实现真实 VL 调用，消息格式仿 overlay_review（DashScope 原生
   MultiModalConversation：{"text": ...} + {"image": data_uri}，响应
   content 可能为 list 需拉平）。
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from app.core.skill_base import Skill
from app.core.skill_registry import register

if TYPE_CHECKING:
    from app.adapters.llm_base import LLMProtocol

logger = logging.getLogger(__name__)

_DEFAULT_PROMPT = (
    "分析这张图片，严格只输出一个 JSON 对象（不要 markdown 代码块、不要解释），字段：\n"
    '{"tags": ["内容主题标签，4-8个，中文"], '
    '"mood": "整体情绪氛围，中文短语", '
    '"composition": "构图方式，中文短语", '
    '"color_palette": ["主要颜色，3-6个，中文或色名"]}'
)

# 模块级缓存：QwenVLAdapter 构造会覆盖 dashscope.api_key，全局复用一个实例
_VL_ADAPTER: Any | None = None

# 分析结果缓存：同一张图在同一轮对话里常被反复分析（多轮追问 / 重试），
# 每次真调 VL 都是钱。key=(asset_id, prompt)，有界 FIFO 防内存无上限。
_ANALYSIS_CACHE: dict[tuple[str, str], dict[str, Any]] = {}
_ANALYSIS_CACHE_MAX = 32


def _cache_get(key: tuple[str, str]) -> dict[str, Any] | None:
    return _ANALYSIS_CACHE.get(key)


def _cache_put(key: tuple[str, str], value: dict[str, Any]) -> None:
    if len(_ANALYSIS_CACHE) >= _ANALYSIS_CACHE_MAX:
        _ANALYSIS_CACHE.pop(next(iter(_ANALYSIS_CACHE)))
    _ANALYSIS_CACHE[key] = value


class VLAnalyzeInput(BaseModel):
    """Input for VL analyze skill."""

    asset_id: str = Field(
        default="",
        description="用户已上传图片的 asset_id（聊天里透出的 id）；最省事的通道，技能自己查库读图",
    )
    image_base64: str = Field(
        default="",
        description="Base64-encoded image（裸 base64 或 data:image/...;base64,... 均可）；与 image_path/image_url/asset_id 四选一",
    )
    image_path: str = Field(
        default="",
        description="图片文件路径（工作区或 backend/outputs 白名单内）；与 image_base64/image_url/asset_id 四选一",
    )
    image_url: str = Field(
        default="",
        description=(
            "作品配图的 URL 或本地路径。http(s) 直传 VL；"
            "/outputs/... 与工作区路径读文件转 base64。"
            "作品上下文（work_context）里的配图走这条通道。"
        ),
    )
    account_id: str = Field(default="", description="XHS account ID（可选）")
    prompt: str = Field(default="", description="自定义分析指令（可选，默认输出结构化标签 JSON）")


class VLAnalyzeOutput(BaseModel):
    """Output from VL analyze skill."""

    tags: list[str] = Field(default_factory=list, description="Detected tags")
    mood: str = Field(default="", description="Detected mood")
    composition: str = Field(default="", description="Detected composition")
    color_palette: list[str] = Field(default_factory=list, description="Detected colors")


def _get_vl_llm() -> Any | None:
    """惰性构建 QwenVLAdapter（dashscope key + 分级路由 VL 模型），模块级缓存。"""
    global _VL_ADAPTER
    if _VL_ADAPTER is not None:
        return _VL_ADAPTER
    from app.config import get_settings

    settings = get_settings()
    if not settings.dashscope_api_key:
        return None
    try:
        from app.services.model_tier import PipelineStage, get_tier_router

        vl_model = get_tier_router().resolve(PipelineStage.VL_UNDERSTAND, "vl")
    except Exception:
        vl_model = settings.tier_economy_vl
    from capabilities.image_gen.adapters.qwen_vl import QwenVLAdapter

    _VL_ADAPTER = QwenVLAdapter(api_key=settings.dashscope_api_key, model=vl_model)
    return _VL_ADAPTER


_MIME_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".gif": "image/gif",
    ".avif": "image/avif",
}


def _to_data_uri(image_base64: str, source: str | Path | None = None) -> str:
    """裸 base64 补 data URI 前缀；已是 data URI 原样返回。

    MIME 按来源文件扩展名推断：采集作品缓存图多为 webp/jpg，统一写
    image/png 会让部分 VL 侧解码走错分支。
    """
    s = image_base64.strip()
    if s.startswith("data:"):
        return s
    mime = "image/png"
    if source:
        mime = _MIME_BY_EXT.get(Path(source).suffix.lower(), mime)
    return f"data:{mime};base64," + s


def _flatten_content(raw: Any) -> str:
    """DashScope 多模态响应 content 可能是 list[{"text": ...}] — 拉平为纯文本。"""
    if isinstance(raw, list):
        return "".join(
            item.get("text", "") if isinstance(item, dict) else str(item)
            for item in raw
        )
    return str(raw or "")


def _parse_analysis(text: str) -> dict[str, Any]:
    """从 VL 输出中提取 JSON 分析结果；失败时降级（全文放 tags 外的 raw）。"""
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(0))
            if isinstance(data, dict):
                return {
                    "tags": [str(t) for t in data.get("tags", []) if t],
                    "mood": str(data.get("mood", "")),
                    "composition": str(data.get("composition", "")),
                    "color_palette": [str(c) for c in data.get("color_palette", []) if c],
                }
        except (json.JSONDecodeError, ValueError):
            pass
    return {"tags": [], "mood": "", "composition": "", "color_palette": [], "raw": text}


def _context_user_id(inputs: dict[str, Any]) -> str:
    """从 skill_call 注入的 _context 取 user_id（dataclass 或 dict 两种形态）。"""
    ctx = inputs.get("_context")
    if isinstance(ctx, dict):
        return str(ctx.get("user_id", "") or "")
    return str(getattr(ctx, "user_id", "") or "")


async def _load_asset_as_base64(asset_id: str, inputs: dict[str, Any]) -> str:
    """asset_id → 原图 base64。查库拿 original_url，再走 asset_library 转 data URI。

    绕开 file_ops 沙箱：uploads/ 不在白名单内，但这条路径只读「本用户自己的」
    素材（get_asset 已校验 user_id），不会触碰密钥/源码红线。
    """
    from app.services.asset_library import get_asset, url_to_base64

    user_id = _context_user_id(inputs)
    if not user_id:
        raise ValueError("asset_id 通道需要用户上下文（_context.user_id 缺失）")
    asset = await get_asset(user_id, asset_id)
    if asset is None:
        raise ValueError(f"asset not found: {asset_id}（或不属于当前用户）")
    data_uri = url_to_base64(asset.get("original_url", ""))
    if not data_uri:
        raise ValueError(f"asset 文件读取失败: {asset_id}")
    return data_uri


def _outputs_root() -> Path:
    """产物根目录：与 main.py 的 /outputs 静态挂载用同一套解析逻辑。

    只写 backend/outputs 会在产物落到项目根 outputs 时读空（output_index
    支持两处），所以优先复用 output_index，缺省再退到 backend/outputs。
    """
    from app.services.output_index import output_root

    root = output_root()
    if root is not None:
        return root
    return Path(__file__).resolve().parents[3] / "outputs"


def _resolve_local_image(raw: str) -> Path:
    """把图片引用还原成可读的本地文件路径。

    作品配图有两种后端 web 路径形态，都是静态挂载点、真实文件在 backend 下：
      `/outputs/xxx.png`  → backend/outputs/     （技能产物 / 卡片图）
      `/uploads/xxx.webp` → backend/uploads/    （采集作品缓存图 / 上传图）
    这两类目录不在 file_ops 沙箱白名单里，但只含运行产物与用户自己的素材，
    不含密钥/源码；这里按固定前缀映射后自校验不逃逸，其余形态仍走沙箱守卫。
    """
    from app.engine.reactor.file_ops_core import resolve_and_guard
    from app.services.image_store import uploads_root

    text = raw.strip()

    for prefix, root in (
        ("/outputs/", _outputs_root()),
        ("/uploads/", uploads_root()),
    ):
        if not text.startswith(prefix):
            continue
        rel = text[len(prefix) :]
        if ".." in Path(rel).parts or rel.startswith(("/", "\\")):
            raise ValueError(f"invalid image path: {raw}")
        base = root.resolve()
        target = (base / rel).resolve()
        if base not in target.parents and target != base:
            raise ValueError(f"image path escapes {prefix}: {raw}")
        return target

    return resolve_and_guard(text)


def _read_local_image_as_data_uri(raw: str) -> str:
    """本地图片 → data URI（VL 无法直接吃后端私有路径）。"""
    import base64 as _b64

    target = _resolve_local_image(raw)
    if not target.is_file():
        raise ValueError(f"image not found: {raw}")
    return _to_data_uri(_b64.b64encode(target.read_bytes()).decode(), target)


@register
class VLAnalyzeSkill(Skill):
    """VL analyze skill using Qwen-VL.

    Does NOT store images in DB (D11).
    Analyzes and discards after use.
    """

    node_type = "analyze"
    name = "vl_analyze"
    description = "Analyze image with Qwen-VL"
    usage_hint = (
        "用户上传/附带图片时用这个看图（你自己看不见像素）。"
        "调用示例：skill_call(skill='vl_analyze', arguments={'asset_id': '01XXXX'}); "
        "asset_id 从聊天里的 [用户附带了 N 张图片...] 提示中取，多图逐张调用。"
        "作品配图（work_context 里的图片引用）用 image_url 通道："
        "skill_call(skill='vl_analyze', arguments={'image_url': '/outputs/a.png'})；"
        "已有本地路径时也可传 image_path。"
    )
    input_schema = VLAnalyzeInput
    output_schema = VLAnalyzeOutput

    def __init__(self, llm: LLMProtocol | None = None) -> None:
        super().__init__()
        self.llm = llm

    async def execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Analyze image using Qwen-VL."""
        in_ = VLAnalyzeInput.model_validate(inputs)

        asset_id = in_.asset_id.strip()
        image_url = in_.image_url.strip()
        # 缓存键统一用「图片引用」：asset_id 或 image_url 都能定位到同一张图，
        # 作品配图在详情页多轮追问时会反复被分析，缓存能挡住重复计费。
        cache_ref = asset_id or image_url
        cache_key = (cache_ref, in_.prompt.strip()) if cache_ref else None
        if cache_key is not None:
            cached = _cache_get(cache_key)
            if cached is not None:
                logger.debug(f"[vl_analyze] cache hit ref={cache_ref}")
                return {"ok": True, **cached}

        # 四条通道统一收敛成「VL 能直接吃的 image 值」：data URI 或 http(s) URL。
        image_src = ""
        image_b64 = in_.image_base64.strip()
        if image_b64:
            image_src = _to_data_uri(image_b64)
        elif in_.image_path.strip():
            # 智能体侧没有 base64 工具（file_read 只回文本），提供按路径读图
            # 并自行编码的通道；路径走 file_ops_core 沙箱守卫（工作区 +
            # backend/outputs 白名单），不放开任意文件读。
            image_src = _read_local_image_as_data_uri(in_.image_path)
        elif image_url:
            # 远端 CDN 链接直传（DashScope 原生支持）；/outputs/ 这类后端私有
            # 路径 VL 访问不到，必须先落成本地文件再转 base64。
            if image_url.startswith(("http://", "https://")):
                image_src = image_url
            else:
                image_src = _read_local_image_as_data_uri(image_url)
        elif asset_id:
            image_src = await _load_asset_as_base64(asset_id, inputs)
        if not image_src:
            raise ValueError(
                "image_base64 / image_path / image_url / asset_id 至少需要一个"
            )

        llm = self.llm or _get_vl_llm()
        if llm is None:
            raise ValueError(
                "VLAnalyzeSkill requires a VL adapter "
                "(configure dashscope_api_key, or inject llm at construction)"
            )

        messages = [
            {
                "role": "user",
                "content": [
                    {"text": in_.prompt or _DEFAULT_PROMPT},
                    {"image": image_src},
                ],
            }
        ]
        result = await llm.chat(messages)
        text = _flatten_content(result.get("content", ""))
        if not text:
            return {"ok": False, "error": "VL returned empty content"}

        analysis = _parse_analysis(text)
        if cache_key is not None and analysis.get("tags"):
            _cache_put(cache_key, analysis)
        return {"ok": True, **analysis}