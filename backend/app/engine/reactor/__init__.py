"""Reactor sub-package: lightweight ReAct loop + thin primitives.

Re-exports from sub-modules:
    from app.engine.reactor import ReactorSession, Primitive, ...
"""

from app.engine.reactor.primitives import (
    ALL_PRIMITIVES,
    PRIMITIVES_BY_NAME,
    BashPrimitive,
    ContentCheckPrimitive,
    FileEditPrimitive,
    FileReadPrimitive,
    FileWritePrimitive,
    GlobPrimitive,
    GrepPrimitive,
    LLMGeneratePrimitive,
    Primitive,
    ViralScorePrimitive,
)
from app.engine.reactor.prompt import build_tool_descriptions, get_reactor_system_prompt
from app.engine.reactor.session import ReactorSession, build_reactor_session

__all__ = [
    "ReactorSession",
    "build_reactor_session",
    "build_tool_descriptions",
    "get_reactor_system_prompt",
    "Primitive",
    "ALL_PRIMITIVES",
    "PRIMITIVES_BY_NAME",
    "FileReadPrimitive",
    "FileWritePrimitive",
    "FileEditPrimitive",
    "BashPrimitive",
    "GlobPrimitive",
    "GrepPrimitive",
    "LLMGeneratePrimitive",
    "ViralScorePrimitive",
    "ContentCheckPrimitive",
]
