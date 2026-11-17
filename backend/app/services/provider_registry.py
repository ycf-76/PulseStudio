"""Provider Registry — 全局模型 Provider 定义（BYOK 基础设施）。

每个 Provider 声明自己支持哪些模型、默认 Base URL、调用方式。
用户只需提供自己的 API Key，即可解锁对应 Provider 的所有模型。

设计原则：
  - Provider 定义随代码版本维护，不放 DB（平台级目录）
  - 用户凭据（API Key）per-user 存 DB，加密存储
  - 前端调 /api/models/available 动态获取可用模型列表
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from app.config import get_settings

logger = logging.getLogger(__name__)


class ProviderType(str, Enum):
    OPENAI_COMPAT = "openai_compat"
    QWEN_VL = "qwen_vl"
    ANTHROPIC = "anthropic"


@dataclass(frozen=True)
class ModelDefinition:
    model_id: str
    api_model: str
    display_name: str
    description: str
    capabilities: tuple[str, ...] = ("text", "chat")
    max_tokens: int = 4096


@dataclass(frozen=True)
class ProviderDefinition:
    provider: str
    provider_name: str
    provider_type: ProviderType
    default_base_url: str
    default_models: tuple[ModelDefinition, ...]
    icon: str = ""
    allow_custom_base_url: bool = True
    allow_custom_models: bool = True
    docs_url: str = ""
    key_placeholder: str = ""


def _build_providers() -> dict[str, ProviderDefinition]:
    s = get_settings()
    return {
        "deepseek": ProviderDefinition(
            provider="deepseek",
            provider_name="DeepSeek",
            provider_type=ProviderType.OPENAI_COMPAT,
            default_base_url=s.deepseek_base_url,
            default_models=(
                ModelDefinition(
                    model_id="DeepSeek-V4-Flash",
                    api_model=s.deepseek_model_v3,
                    display_name="DeepSeek V4 Flash",
                    description="最新 Flash 模型，速度快、中文与代码能力强",
                    capabilities=("text", "chat", "code"),
                    max_tokens=8192,
                ),
                ModelDefinition(
                    model_id="DeepSeek-R1",
                    api_model=s.deepseek_model_r1,
                    display_name="DeepSeek R1",
                    description="推理模型，擅长复杂逻辑、数学与多步推导",
                    capabilities=("text", "reasoning"),
                    max_tokens=8192,
                ),
            ),
            icon="deepseek",
            docs_url="https://platform.deepseek.com/api_keys",
            key_placeholder="sk-...",
        ),
        "openai": ProviderDefinition(
            provider="openai",
            provider_name="OpenAI",
            provider_type=ProviderType.OPENAI_COMPAT,
            default_base_url=s.openai_base_url,
            default_models=(
                ModelDefinition(
                    model_id="GPT-4o",
                    api_model=s.openai_model,
                    display_name="GPT-4o",
                    description="旗舰多模态模型，通用能力最强",
                    capabilities=("text", "chat", "vision", "function_calling"),
                    max_tokens=4096,
                ),
                ModelDefinition(
                    model_id="GPT-4o-Mini",
                    api_model="gpt-4o-mini",
                    display_name="GPT-4o Mini",
                    description="性价比模型，速度快",
                    capabilities=("text", "chat", "vision"),
                    max_tokens=4096,
                ),
            ),
            icon="openai",
            docs_url="https://platform.openai.com/api-keys",
            key_placeholder="sk-...",
        ),
        "anthropic": ProviderDefinition(
            provider="anthropic",
            provider_name="Anthropic",
            provider_type=ProviderType.ANTHROPIC,
            default_base_url=s.anthropic_base_url,
            default_models=(
                ModelDefinition(
                    model_id="Claude-3.5-Sonnet",
                    api_model=s.anthropic_model,
                    display_name="Claude 3.5 Sonnet",
                    description="长上下文，写作与代码俱佳，安全稳健",
                    capabilities=("text", "chat", "long_context"),
                    max_tokens=8192,
                ),
            ),
            icon="anthropic",
            docs_url="https://console.anthropic.com/settings/keys",
            key_placeholder="sk-ant-...",
        ),
        "qwen": ProviderDefinition(
            provider="qwen",
            provider_name="阿里通义",
            provider_type=ProviderType.OPENAI_COMPAT,
            default_base_url=s.qwen_text_base_url,
            default_models=(
                ModelDefinition(
                    model_id="Qwen-Max",
                    api_model=s.qwen_text_model,
                    display_name="通义千问 Max",
                    description="文本大模型，长文本与综合能力强",
                    capabilities=("text", "chat", "chinese"),
                    max_tokens=8000,
                ),
                ModelDefinition(
                    model_id="Qwen-VL",
                    api_model=s.qwen_vl_model,
                    display_name="通义千问 VL",
                    description="视觉语言模型，支持图片理解",
                    capabilities=("text", "vision"),
                    max_tokens=2000,
                ),
            ),
            icon="qwen",
            docs_url="https://bailian.console.aliyun.com/",
            key_placeholder="sk-...",
        ),
        "gemini": ProviderDefinition(
            provider="gemini",
            provider_name="Google",
            provider_type=ProviderType.OPENAI_COMPAT,
            default_base_url=s.google_base_url,
            default_models=(
                ModelDefinition(
                    model_id="Gemini-1.5-Flash",
                    api_model=s.google_model,
                    display_name="Gemini 1.5 Flash",
                    description="多模态 Flash，速度快、超长上下文",
                    capabilities=("text", "chat", "vision"),
                    max_tokens=8192,
                ),
            ),
            icon="gemini",
            docs_url="https://aistudio.google.com/apikey",
            key_placeholder="AIza...",
        ),
        "moonshot": ProviderDefinition(
            provider="moonshot",
            provider_name="Moonshot",
            provider_type=ProviderType.OPENAI_COMPAT,
            default_base_url=s.moonshot_base_url,
            default_models=(
                ModelDefinition(
                    model_id="Kimi",
                    api_model=s.moonshot_model,
                    display_name="Kimi",
                    description="超长上下文，中文长文处理见长",
                    capabilities=("text", "chat", "long_context"),
                    max_tokens=8192,
                ),
            ),
            icon="kimi",
            docs_url="https://platform.moonshot.cn/console/api-keys",
            key_placeholder="sk-...",
        ),
        "zhipu": ProviderDefinition(
            provider="zhipu",
            provider_name="智谱",
            provider_type=ProviderType.OPENAI_COMPAT,
            default_base_url=s.zhipu_base_url,
            default_models=(
                ModelDefinition(
                    model_id="GLM-4",
                    api_model=s.zhipu_model,
                    display_name="GLM-4",
                    description="国产大模型，中文综合能力均衡",
                    capabilities=("text", "chat", "chinese"),
                    max_tokens=8000,
                ),
            ),
            icon="glm",
            docs_url="https://open.bigmodel.cn/usercenter/apikeys",
            key_placeholder="...",
        ),
        "custom": ProviderDefinition(
            provider="custom",
            provider_name="自定义",
            provider_type=ProviderType.OPENAI_COMPAT,
            default_base_url="",
            default_models=(),
            icon="custom",
            allow_custom_base_url=True,
            allow_custom_models=True,
            key_placeholder="sk-...",
        ),
    }


_PROVIDER_CACHE: dict[str, ProviderDefinition] | None = None


def get_all_providers() -> dict[str, ProviderDefinition]:
    global _PROVIDER_CACHE
    if _PROVIDER_CACHE is None:
        _PROVIDER_CACHE = _build_providers()
    return _PROVIDER_CACHE


def get_provider(provider: str) -> ProviderDefinition | None:
    return get_all_providers().get(provider)


def find_model_by_id(model_id: str) -> tuple[ProviderDefinition, ModelDefinition] | None:
    for prov in get_all_providers().values():
        for model in prov.default_models:
            if model.model_id == model_id:
                return prov, model
    return None


# Provider → 平台级 Key 的环境变量名
_PROVIDER_ENV_KEY = {
    "deepseek": "DEEPSEEK_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "qwen": "DASHSCOPE_API_KEY",
    "gemini": "GOOGLE_API_KEY",
    "moonshot": "MOONSHOT_API_KEY",
    "zhipu": "ZHIPU_API_KEY",
}

_ENV_FILE_CACHE: tuple[float, frozenset[str] | None] | None = None


def _env_file_path() -> Path | None:
    """定位项目 .env：优先 backend/.env（相对本文件），其次当前工作目录。"""
    backend_dir = Path(__file__).resolve().parents[2]
    for candidate in (backend_dir / ".env", Path(".env").resolve()):
        if candidate.is_file():
            return candidate
    return None


def _declared_env_providers() -> frozenset[str] | None:
    """项目 .env 里显式声明了 Key 的厂商集合。

    只认项目 .env 文件，不认操作系统里残留的同名环境变量 —— 否则机器上别的项目留下的
    `OPENAI_API_KEY` 会让输入框凭空多出用户从未配置的厂商。
    返回 None 表示「没有 .env 文件」，此时按纯环境变量部署，回退到 settings 判断。
    """
    global _ENV_FILE_CACHE
    env_path = _env_file_path()
    if env_path is None:
        return None
    try:
        mtime = env_path.stat().st_mtime
    except OSError:
        return None
    if _ENV_FILE_CACHE and _ENV_FILE_CACHE[0] == mtime:
        return _ENV_FILE_CACHE[1]

    declared: set[str] = set()
    try:
        from dotenv import dotenv_values

        values = dotenv_values(env_path)
        for provider, env_name in _PROVIDER_ENV_KEY.items():
            if (values.get(env_name) or "").strip():
                declared.add(provider)
    except Exception:
        logger.warning("[Provider] 解析 .env 失败，回退到环境变量判断", exc_info=True)
        _ENV_FILE_CACHE = (mtime, None)
        return None

    result = frozenset(declared)
    _ENV_FILE_CACHE = (mtime, result)
    return result


def get_global_fallback_key(provider: str) -> str:
    """平台级（非用户 BYOK）Key。

    有 .env 时，只认 .env 里显式配置过的厂商；没有 .env 时按环境变量注入判断。
    """
    s = get_settings()
    mapping: dict[str, str] = {
        "deepseek": s.deepseek_api_key,
        "openai": s.openai_api_key,
        "anthropic": s.anthropic_api_key,
        "qwen": s.dashscope_api_key,
        "gemini": s.google_api_key,
        "moonshot": s.moonshot_api_key,
        "zhipu": s.zhipu_api_key,
    }
    declared = _declared_env_providers()
    if declared is not None and provider not in declared:
        return ""
    return mapping.get(provider, "")


def invalidate_cache() -> None:
    global _PROVIDER_CACHE
    _PROVIDER_CACHE = None
