"""Agent 注册表测试 — 适配 ReactorSession 架构。

旧版 build_harness 返回 Harness（.llm, .skills, .run, .shutdown），
新版 build_session 返回 ReactorSession（.llm, .primitives, .run）。
测试全部重写以匹配新接口。
"""

from app.adapters.llm_base import BaseLLM
from app.agents.registry import AgentRegistry
from app.config import Settings
from app.engine.reactor.session import ReactorSession


class _FakeSettings:
    """只覆盖 LLM 相关配置，其余字段委托给真实 Settings。

    build_session 的调用链会间接 import app.db.session 等模块，它们同样
    get_settings()；若这里只摆死几个字段，配置一新增（如 database_url、
    deepseek_model_r1）就会 AttributeError。委托兜底可避免测试随配置演进失效。
    """

    def __init__(self, api_key: str = "sk-test"):
        self._real = Settings()
        self.deepseek_api_key = api_key
        self.deepseek_base_url = "https://api.deepseek.com"
        self.deepseek_model_v3 = "deepseek-chat"
        self.deepseek_model_r1 = "deepseek-reasoner"

    def __getattr__(self, name: str):
        real = self.__dict__.get("_real")
        if real is None:
            raise AttributeError(name)
        return getattr(real, name)


def _patch_settings(monkeypatch):
    import app.config as config_mod

    monkeypatch.setattr(config_mod, "get_settings", lambda: _FakeSettings())


def test_registry_has_all_builtin_agents():
    registry = AgentRegistry()
    assert len(registry.list_agents()) == 22
    assert registry.get("search").llm_model == "deepseek-v3"
    assert registry.get("copywrite").llm_model == "deepseek-r1"


def test_registry_has_ops_team():
    registry = AgentRegistry()
    ops_ids = {
        "trend-hunter", "strategist", "copywriter", "longform", "visual",
        "meme", "video", "sound", "community", "inspector", "reviewer",
    }
    for agent_id in ops_ids:
        adef = registry.get(agent_id)
        assert adef is not None, agent_id
        assert adef.skills, f"{agent_id} 必须有技能白名单"
        assert adef.persona, f"{agent_id} 必须有人格提示词"
    # 不做自动发布，团队里不设发布角色
    assert registry.get("publisher") is None



def test_build_session_returns_reactor_session(monkeypatch):
    _patch_settings(monkeypatch)
    registry = AgentRegistry()

    session = registry.build_session("search")
    assert isinstance(session, ReactorSession)


def test_build_session_locks_specific_model(monkeypatch):
    _patch_settings(monkeypatch)
    registry = AgentRegistry()

    search_session = registry.build_session("search")
    assert isinstance(search_session.llm, BaseLLM)
    assert search_session.llm.model_name == "deepseek-chat"

    copywrite_session = registry.build_session("copywrite")
    assert copywrite_session.llm.model_name == "deepseek-reasoner"


def test_build_session_assembles_primitives(monkeypatch):
    _patch_settings(monkeypatch)
    registry = AgentRegistry()

    search_session = registry.build_session("search")
    primitive_names = list(search_session.primitives.keys())
    assert "file_read" in primitive_names
    assert "bash" in primitive_names
    assert "glob" in primitive_names

    publish_session = registry.build_session("publish")
    publish_primitives = list(publish_session.primitives.keys())
    assert "file_read" in publish_primitives


def test_build_session_explore_agent(monkeypatch):
    _patch_settings(monkeypatch)
    registry = AgentRegistry()

    assert registry.get("explore") is not None
    explore_session = registry.build_session("explore")
    primitive_names = list(explore_session.primitives.keys())
    assert "file_read" in primitive_names
    assert "bash" in primitive_names


def test_build_session_has_guardian(monkeypatch):
    _patch_settings(monkeypatch)
    registry = AgentRegistry()

    session = registry.build_session("search")
    assert hasattr(session, "_guardian")
    from app.engine.governance.guardian import Guardian
    assert isinstance(session._guardian, Guardian)


def test_build_session_auto_approve_flag(monkeypatch):
    _patch_settings(monkeypatch)
    registry = AgentRegistry()

    session_auto = registry.build_session("search", auto_approve=True)
    assert session_auto.auto_approve is True

    session_manual = registry.build_session("search", auto_approve=False)
    assert session_manual.auto_approve is False


def test_build_session_respects_max_iterations(monkeypatch):
    _patch_settings(monkeypatch)
    registry = AgentRegistry()

    chat_session = registry.build_session("chat_agent")
    assert chat_session.max_iterations == 40

    search_session = registry.build_session("search")
    assert search_session.max_iterations == 4


def test_build_session_has_prompt_template():
    registry = AgentRegistry()

    analyze_def = registry.get("analyze")
    assert "{topic}" in analyze_def.prompt_template

    copywrite_def = registry.get("copywrite")
    assert "{topic}" in copywrite_def.prompt_template