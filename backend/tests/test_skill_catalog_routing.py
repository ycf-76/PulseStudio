"""P1 技能目录路由测试：SkillRouter 接线 + 热/冷加载。

验收标准（对应提示词架构 P1 方案）：
1. 路由顺序：触发命中的技能优先于 hot 保序入选（hot 池可超 max_tools）
2. build_session 接回 _build_skills：creation_type 裁剪技能池，None 全量兜底
3. ReactorSession 首轮 run() 按真实 user_intent 路由目录：
   - 完整条目 ≤ 上限，未选中技能列名兜底
   - _skills 保持全集（执行侧不受影响）
   - 二次调用目录固定（缓存友好）；显式 system_prompt 不覆盖
4. 体积：路由后目录段显著小于全量目录（实测 43.6k → ~8k）
"""

import pytest

from app.engine.reactor.prompt import (
    build_skill_catalog,
    build_skill_catalog_section,
)
from app.engine.reactor.session import ReactorSession, _SKILL_CATALOG_MAX_TOOLS
from app.tools.skill_router import SkillRouter


class _FakeSkill:
    """路由所需的全部属性：name / trigger_words / 显式档位标注。"""

    def __init__(self, name, category="create", priority="hot", triggers=()):
        self.name = name
        self.skill_category = category
        self.skill_priority = priority
        self.trigger_words = list(triggers)
        self.description = f"{name} 的测试描述"
        self.usage_hint = ""
        self.input_schema = None


def test_triggered_beats_hot_truncation():
    """测试: 路由选中顺序修复
    场景: hot 技能数(45) > max_tools(40)，1 个 warm 技能带触发词命中
    预期: 触发命中的 warm 技能必须入选（旧实现 hot 在前会把它截掉）
    """
    hot = [_FakeSkill(f"h{i:02d}") for i in range(45)]
    warm_hit = _FakeSkill("coffee_skill", category="analyze", priority="warm", triggers=["咖啡"])
    router = SkillRouter(max_tools=40, enforce_priority=True)
    result = router.select_skills(hot + [warm_hit], "帮我做一杯咖啡")
    selected_names = [getattr(s, "name", "") for s in result.selected_skills]
    assert "coffee_skill" in selected_names
    assert len(result.selected_skills) == 40
    # 命中技能应排在未命中 hot 之前
    assert selected_names.index("coffee_skill") < selected_names.index("h00")


def test_cold_requires_trigger_under_enforce_priority():
    """测试: cold 技能默认不进目录
    场景: cold 技能无触发词命中 vs 有命中
    预期: 无命中不入选；命中则入选
    """
    cold = [
        _FakeSkill("cold_a", category="platform", priority="cold"),
        _FakeSkill("cold_b", category="platform", priority="cold", triggers=["飞书"]),
    ]
    router = SkillRouter(max_tools=40, enforce_priority=True)
    r1 = router.select_skills(cold, "写一篇文案")
    assert [s.name for s in r1.selected_skills] == []
    r2 = router.select_skills(cold, "发一条飞书消息")
    assert "cold_b" in [s.name for s in r2.selected_skills]


def test_build_skills_creation_type_filtering():
    """测试: _build_skills 技能池裁剪（P1 接线核心）
    场景: creation_type=short_video / None / 未知值
    预期: short_video 池 < 全量且非空；None 与未知值保持全量兜底
    """
    from app.agents.registry import _build_skills

    full = _build_skills(None, None)
    short_video = _build_skills(None, "short_video")
    assert len(full) > 100, "全量池应 ≥ 100（当前 140+）"
    assert 0 < len(short_video) < len(full)
    assert set(map(id, short_video)) <= set(map(id, full)), "裁剪池必须是全量子集"
    unknown = _build_skills(None, "nonexistent_type")
    assert len(unknown) == len(full), "未知 creation_type 应兜底全量"


def test_build_skills_image_text_has_required_tools():
    """测试: image_text 必须包含图文编排与飞书工具。"""
    from app.agents.registry import _build_skills

    pool = _build_skills(None, "image_text")
    names = {getattr(s, "name", "") for s in pool}
    assert "generate_card_image" in names
    assert "compose_card_deck" in names
    assert "feishu_file_ops" in names
    assert "feishu_wiki_doc" in names
    assert "wechat_send_text" in names
    assert "wechat_send_file" in names
    assert "email_send" in names


def test_feishu_file_ops_trigger_matches_generic_feishu():
    """测试: 用户只说“飞书”时，文件操作也进入触发命中集。"""
    from app.tools.skill_router import SkillRouter

    router = SkillRouter(max_tools=40, enforce_priority=True)
    from app.agents.registry import _build_skills

    pool = _build_skills(None, "image_text")
    result = router.select_skills(pool, "用飞书打开我的知识库")
    selected = {getattr(s, "name", "") for s in result.selected_skills}
    assert "feishu_file_ops" in selected
    assert "feishu_wiki_doc" in selected


def test_build_skills_whitelist_exact():
    """测试: 白名单模式
    场景: agent 定义了 skills 白名单
    预期: 返回的技能名 ⊆ 白名单
    """
    from app.agents.registry import _build_skills

    pool = _build_skills(["trending_search"], None)
    names = {getattr(s, "name", "") for s in pool}
    assert names <= {"trending_search"}


def _make_session(n_hot=45, extra=()):
    """构造带 fake 技能的 ReactorSession（无 LLM，不走网络）。"""
    skills = [_FakeSkill(f"h{i:02d}") for i in range(n_hot)] + list(extra)
    return ReactorSession(skills=skills), skills


def test_session_routes_catalog_on_first_run():
    """测试: 首轮 run 路由目录
    场景: 45 hot + 1 触发命中技能，首轮 _build_initial_messages
    预期: 目录完整条目 = max_tools 上限；命中技能在目录中；
         _skills 全集不变；列名兜底段存在
    """
    session, skills = _make_session(extra=[
        _FakeSkill("coffee_skill", category="analyze", priority="warm", triggers=["咖啡"])
    ])
    before = len(session._skills)
    msgs = session._build_initial_messages("帮我做一杯咖啡", None)
    sp = msgs[0]["content"]
    assert "## 可用技能" in sp
    assert "- coffee_skill —" in sp
    assert "其他可用技能（未展开，可按名直接调用 skill_call）：" in sp
    # 目录完整条目数受上限约束（数条目行而非全文 bullet，模板里也有列表）
    catalog = sp[sp.index("## 可用技能"):sp.index("skill_call 用法")]
    entries = [l for l in catalog.splitlines() if l.startswith("- ")]
    assert len(entries) <= _SKILL_CATALOG_MAX_TOOLS
    # 执行侧全集不受路由影响
    assert len(session._skills) == before
    assert session._prompt_routed is True


def test_session_catalog_stable_on_second_run():
    """测试: 目录首轮后固定（缓存友好）
    场景: 同一 session 连续两次 _build_initial_messages，意图不同
    预期: system prompt 不变
    """
    session, _ = _make_session(n_hot=5)
    m1 = session._build_initial_messages("做图文", None)
    m2 = session._build_initial_messages("完全不同的意图：发飞书", None)
    assert m1[0]["content"] == m2[0]["content"]


def test_session_explicit_prompt_not_overridden():
    """测试: 显式 system_prompt 不被路由覆盖
    场景: 调用方显式传入 system_prompt
    预期: 路由跳过，prompt 原样保留
    """
    skills = [_FakeSkill(f"h{i:02d}") for i in range(5)]
    session = ReactorSession(system_prompt="CUSTOM PROMPT", skills=skills)
    msgs = session._build_initial_messages("任意意图", None)
    assert msgs[0]["content"] == "CUSTOM PROMPT"
    assert session._prompt_routed is False


def test_session_routing_failure_falls_back_to_full_catalog():
    """测试: 路由异常兜底
    场景: 路由器抛异常（select_skills 被 monkeypatch 破坏）
    预期: 保留全量目录，不抛错
    """
    session, _ = _make_session(n_hot=5)
    import app.tools.skill_router as sr

    original = sr.SkillRouter.select_skills

    def _boom(self, all_skills, user_message):
        raise RuntimeError("router down")

    sr.SkillRouter.select_skills = _boom
    try:
        msgs = session._build_initial_messages("做图文", None)
        assert "## 可用技能" in msgs[0]["content"]
        assert "- h00 —" in msgs[0]["content"], "兜底应保留全量目录"
    finally:
        sr.SkillRouter.select_skills = original


def test_extra_names_block_rendering():
    """测试: 列名段渲染
    场景: build_skill_catalog_section 传入 extra_names
    预期: 含标题行与名字；空列表时不渲染该段
    （注意：skills.md 模板的约定说明里也含「其他可用技能」字样，
     断言须用带全角冒号的完整标题行区分）
    """
    skills = [_FakeSkill("s1"), _FakeSkill("s2")]
    section = build_skill_catalog_section(skills, extra_names=["a", "b", "c"])
    assert "其他可用技能（未展开，可按名直接调用 skill_call）：" in section
    assert "a、b、c" in section
    plain = build_skill_catalog_section(skills, extra_names=None)
    assert "其他可用技能（未展开" not in plain


def test_extra_names_deduped_against_selected():
    """测试: session 路由后列名与目录条目不重复
    场景: 池子(45 hot) > max_tools(40)，必然产生列名段
    预期: extra_names 排除已选中技能名，且条目+列名覆盖全集
    """
    session, _ = _make_session(n_hot=45)
    session._build_initial_messages("做图文", None)
    sp = session.system_prompt
    catalog = sp[sp.index("## 可用技能"):sp.index("skill_call 用法")]
    entry_names = {
        l[2:].split(" — ")[0]
        for l in catalog.splitlines()
        if l.startswith("- ")
    }
    assert len(entry_names) == 40, "45 hot 池应截断到 40 条目"
    extra_block = catalog[catalog.index("其他可用技能（未展开"):]
    listed = {
        n.strip()
        for l in extra_block.splitlines()
        if l.startswith("  ") and "、" in l
        for n in l.strip().split("、")
    }
    assert not (entry_names & listed), "列名段不得与目录条目重复"
    assert len(listed) == 5, "45 - 40 = 5 个未选中技能列名兜底"
    assert (entry_names | listed) == {f"h{i:02d}" for i in range(45)}


@pytest.mark.integration
def test_real_registry_routed_catalog_size():
    """测试: 真实 registry 体积验收（P1）
    场景: chat_agent + creation_type=short_video，首轮路由
    预期: 目录段 < 10000 字符（P0 后全量 21.7k，P0 前 43.6k）
    """
    import app.engine.factory as factory_mod
    from app.agents.registry import get_agent_registry

    original = factory_mod.get_deepseek_llm
    factory_mod.get_deepseek_llm = lambda model=None: None
    try:
        session = get_agent_registry().build_session(
            "chat_agent", creation_type="short_video"
        )
    finally:
        factory_mod.get_deepseek_llm = original
    msgs = session._build_initial_messages("帮我做一期手冲咖啡的短视频", None)
    sp = msgs[0]["content"]
    catalog = sp[sp.index("## 可用技能"):sp.index("skill_call 用法")]
    entries = [l for l in catalog.splitlines() if l.startswith("- ")]
    assert len(entries) <= _SKILL_CATALOG_MAX_TOOLS
    assert len(catalog) < 10000
    # 目录条目 + 列名应覆盖全部技能池（不丢可发现性）
    pool = session._skills
    entry_names = {e[2:].split(" — ")[0] for e in entries}
    extra_block = catalog[catalog.index("其他可用技能"):] if "其他可用技能" in catalog else ""
    listed = {
        n.strip()
        for l in extra_block.splitlines()
        if l.startswith("  ") and "、" in l
        for n in l.strip().split("、")
    }
    pool_names = {getattr(s, "name", "") for s in pool}
    assert (entry_names | listed) >= pool_names, "条目+列名必须覆盖技能池全集"


# ---------------------------------------------------------------------------
# P2: 二层披露（skill_doc）+ design 块独立消息
# ---------------------------------------------------------------------------


class _DocFakeSkill:
    """skill_doc 测试用 fake：带 schema 与多行纪律。

    name 必须在 session._dispatch 的只读技能名单内（web_search 等），
    否则 Guardian 确认门会在执行前拦截，到不了 skill_doc 注入段。
    """

    name = "web_search"
    description = "搜索网络信息"
    usage_hint = "先 confirmed=false 探测\n确认后再执行"
    skill_category = "analyze"
    skill_priority = "hot"
    trigger_words = []

    def __init__(self):
        from pydantic import BaseModel, Field

        class _Schema(BaseModel):
            query: str = Field(description="搜索词")
            limit: int = Field(default=10, description="数量上限")

        self.input_schema = _Schema


def _make_transport_stub():
    """返回可编程 transport stub（get_tool + call 返回固定结果）。"""
    from app.engine.reactor.session import InProcessTransport  # 确保模块可用

    assert InProcessTransport is not None

    class _StubTool:
        name = "stub_tool"
        required_permissions: tuple = ()

    class _Stub:
        def __init__(self):
            self.calls: list[tuple[str, dict]] = []

        def get_tool(self, name):
            return _StubTool()

        async def call(self, name, args):
            self.calls.append((name, args))
            return {"ok": True, "result": "done"}

    return _Stub()


def _wire_session_for_dispatch(session):
    """只替换 transport，保留真实 Guardian（低风险默认放行）。"""
    session._transport = _make_transport_stub()
    return session


@pytest.mark.asyncio
async def test_skill_doc_attached_on_first_call_only():
    """测试: skill_doc 首次调用附带、二次不附（P2-2 核心）
    场景: 同一技能连续两次 skill_call
    预期: 第一次返回含 skill_doc（纪律+参数全文），第二次不含
    """
    from app.engine.reactor.session import ReactorSession

    session = ReactorSession(skills=[_DocFakeSkill()])
    _wire_session_for_dispatch(session)
    inner = {"skill": "web_search", "arguments": {"query": "咖啡"}}

    r1 = await session._dispatch({"name": "skill_call", "arguments": dict(inner)})
    assert r1["ok"] is True
    assert "skill_doc" in r1, "首次调用必须附带 skill_doc"
    assert "先 confirmed=false 探测" in r1["skill_doc"], "纪律 hint 进 doc"
    assert "query:str — 搜索词" in r1["skill_doc"], "参数全文进 doc"
    assert "web_search" in session._skill_doc_sent

    r2 = await session._dispatch({"name": "skill_call", "arguments": dict(inner)})
    assert "skill_doc" not in r2, "同一技能只附一次"
    assert len(session._transport.calls) == 2


@pytest.mark.asyncio
async def test_skill_doc_unknown_skill_no_attachment():
    """测试: 未注册技能名不附 doc、不记哨兵
    场景: skill_call 调用 _skills 里不存在的技能
    预期: 返回原样，_skill_doc_sent 不变
    """
    from app.engine.reactor.session import ReactorSession

    session = ReactorSession(skills=[_DocFakeSkill()])
    _wire_session_for_dispatch(session)
    r = await session._dispatch({"name": "skill_call", "arguments": {"skill": "ghost", "arguments": {}}})
    assert "skill_doc" not in r
    assert "ghost" not in session._skill_doc_sent


@pytest.mark.asyncio
async def test_skill_doc_non_skill_call_untouched():
    """测试: 非 skill_call 的工具调用不受二层披露影响
    场景: 直接调用普通工具名
    预期: 返回原样无 skill_doc
    """
    from app.engine.reactor.session import ReactorSession

    session = ReactorSession(skills=[])
    _wire_session_for_dispatch(session)
    r = await session._dispatch({"name": "file_read", "arguments": {"path": "x"}})
    assert r == {"ok": True, "result": "done"}


def test_design_block_injected_as_separate_message():
    """测试: design 块以独立 system 消息注入（P2-3）
    场景: _build_initial_messages 且 design 块非空
    预期: messages[0] 静态主 prompt；design 内容不在 [0] 中、在后续 system 消息
    """
    import app.engine.reactor.session as sess_mod
    from app.engine.reactor.session import ReactorSession

    session = ReactorSession(skills=[_DocFakeSkill()], creation_type="short_video")
    # monkeypatch design 块为可识别固定文案（真实 injector 内容不影响本测试）
    original = sess_mod.build_design_context_block
    sess_mod.build_design_context_block = lambda **kw: "DESIGN_BLOCK_MARKER"
    try:
        msgs = session._build_initial_messages("做视频", None)
    finally:
        sess_mod.build_design_context_block = original
    assert msgs[0]["role"] == "system"
    assert "DESIGN_BLOCK_MARKER" not in msgs[0]["content"], "动态块不得进主 prompt"
    assert any(
        m["role"] == "system" and m["content"] == "DESIGN_BLOCK_MARKER" for m in msgs
    ), "design 块应以独立 system 消息存在"
    # 同意图二次构建，主 prompt 稳定
    msgs2 = session._build_initial_messages("做视频", None)
    assert msgs2[0]["content"] == msgs[0]["content"]


def test_design_block_absent_when_no_input():
    """测试: 无 user_message/creation_type 时无 design 消息
    场景: build_design_context_block('', None)
    预期: 返回空串（调用方不追加消息）
    """
    from app.engine.reactor.prompt import build_design_context_block

    assert build_design_context_block("", None) == ""
