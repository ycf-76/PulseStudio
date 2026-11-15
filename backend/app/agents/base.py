"""Agent 声明式定义（独立模块，避免 registry 与 ops_team 循环依赖）。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AgentDef(BaseModel):
    """Agent 的声明式定义。"""

    agent_id: str
    role: str = ""
    description: str = ""
    llm_model: str | None = None
    executor: str = "loop"
    skills: list[str] | None = Field(default=None)
    disallowed_skills: list[str] = Field(default_factory=list)
    hard_rules: list[str] = Field(default_factory=list)
    soft_semantic: dict[str, Any] | None = None
    recovery: dict[str, Any] = Field(default_factory=dict)
    max_iterations: int = 4
    persona: str = ""
    prompt_template: str = ""