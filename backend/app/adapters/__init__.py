"""Adapters subpackage.

Exports: BaseLLM, DeepSeekAdapter, QwenVLAdapter, ImageGenAdapter, WanxAdapter, PollinationsAdapter.

注意：capabilities 层的适配器必须惰性导出（PEP 562）。
`capabilities.image_gen.adapters.qwen_vl` 依赖 `app.adapters.llm_base`，
导入它必然先执行本包的 __init__；若这里再反向 `from capabilities... import`，
就形成「qwen_vl → app.adapters.llm_base → 本包初始化 → qwen_vl」的循环，
此时 qwen_vl 里的类还没定义，直接 ImportError。
惰性导出让本包初始化不再触碰 capabilities，导入顺序彻底无关。
"""

from app.adapters.deepseek import DeepSeekAdapter
from app.adapters.llm_base import BaseLLM, ChatMessage, LLMConfig

_LAZY_EXPORTS = {
    "QwenVLAdapter": ("capabilities.image_gen.adapters.qwen_vl", "QwenVLAdapter"),
    "ImageGenAdapter": ("capabilities.image_gen.adapters.image_gen", "ImageGenAdapter"),
    "WanxAdapter": ("capabilities.image_gen.adapters.image_gen", "WanxAdapter"),
    "PollinationsAdapter": ("capabilities.image_gen.adapters.image_gen", "PollinationsAdapter"),
    "get_default_adapter": ("capabilities.image_gen.adapters.image_gen", "get_default_adapter"),
}

__all__ = [
    "BaseLLM",
    "ChatMessage",
    "LLMConfig",
    "DeepSeekAdapter",
    *_LAZY_EXPORTS,
]


def __getattr__(name: str):
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr = target
    from importlib import import_module

    value = getattr(import_module(module_name), attr)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY_EXPORTS))
