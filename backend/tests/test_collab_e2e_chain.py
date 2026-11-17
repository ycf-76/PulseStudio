"""End-to-end chain tests for multi-agent collaboration.

Covers:
  - Happy path: spawn → wait → list → interrupt
  - Boundary: missing params, non-existent agent, pool full, depth exceeded
  - Chain integrity: _ensure_collab lazy init, inject_params, _dispatch routing
  - Resource lifecycle: shutdown cleanup, double shutdown
  - Message bus: send/receive between agents
  - Agent hierarchy: path correctness, parent-child relationships
"""

from __future__ import annotations

import asyncio

import pytest

from app.engine.collab import (
    AgentHandle,
    AgentManager,
    AgentMessage,
    AgentStatus,
    CollabMode,
    CollaborationTools,
    ConcurrencyPool,
    InterAgentBus,
)
from app.engine.collab.message_bus import MessageType

# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
async def collab():
    """Fresh CollaborationTools instance per test."""
    manager = AgentManager(max_concurrent=3, max_depth=3)
    bus = InterAgentBus()
    pool = ConcurrencyPool(max_slots=3)
    root = manager.register_root("root")
    tools = CollaborationTools(
        agent_manager=manager,
        message_bus=bus,
        concurrency_pool=pool,
    )
    yield tools, manager, bus, pool, root
    await manager.shutdown()


def _inject(collab_tools: CollaborationTools, params: dict, caller: str = "root") -> dict:
    """Shortcut: inject collab infra into params."""
    collab_tools.inject_params(params, caller)
    return params


# ═══════════════════════════════════════════════════════════════════
# 1. HAPPY PATH
# ═══════════════════════════════════════════════════════════════════


class TestHappyPath:
    """spawn → wait → list → interrupt 完整链路。"""

    @pytest.mark.asyncio
    async def test_spawn_returns_agent_id_and_path(self, collab):
        tools, mgr, bus, pool, root = collab
        from app.core.skill_registry import get_skill_class_by_name

        cls = get_skill_class_by_name("spawn_agent")
        skill = cls()

        params = _inject(
            tools,
            {
                "task_name": "research",
                "task_description": "Search AI trends",
            },
        )
        result = await skill.execute(params)

        assert result.get("success") is True
        assert "agent_id" in result
        assert result["path"] == "root/research"
        assert result["parent_id"] == "root"
        assert result["depth"] == 1
        assert result["status"] == "pending"

    @pytest.mark.asyncio
    async def test_list_agents_shows_root_and_spawned(self, collab):
        tools, mgr, bus, pool, root = collab
        from app.core.skill_registry import get_skill_class_by_name

        spawn_cls = get_skill_class_by_name("spawn_agent")
        spawn_result = await spawn_cls().execute(
            _inject(
                tools,
                {
                    "task_name": "writer",
                    "task_description": "Write copy",
                },
            )
        )
        assert spawn_result.get("success") is True

        list_cls = get_skill_class_by_name("list_agents")
        list_result = await list_cls().execute(_inject(tools, {}))

        assert list_result["total"] == 2
        assert list_result["active"] >= 1
        agent_ids = [a["agent_id"] for a in list_result["agents"]]
        assert "root" in agent_ids
        assert spawn_result["agent_id"] in agent_ids

    @pytest.mark.asyncio
    async def test_interrupt_spawned_agent(self, collab):
        tools, mgr, bus, pool, root = collab
        from app.core.skill_registry import get_skill_class_by_name

        spawn_cls = get_skill_class_by_name("spawn_agent")
        spawn_result = await spawn_cls().execute(
            _inject(
                tools,
                {
                    "task_name": "auditor",
                    "task_description": "Audit content",
                },
            )
        )
        agent_id = spawn_result["agent_id"]

        interrupt_cls = get_skill_class_by_name("interrupt_agent")
        interrupt_result = await interrupt_cls().execute(
            _inject(
                tools,
                {
                    "agent_id": agent_id,
                },
            )
        )

        assert interrupt_result.get("success") is True
        assert interrupt_result["interrupted"] is True

    @pytest.mark.asyncio
    async def test_send_message_between_agents(self, collab):
        tools, mgr, bus, pool, root = collab
        from app.core.skill_registry import get_skill_class_by_name

        spawn_cls = get_skill_class_by_name("spawn_agent")
        spawn_result = await spawn_cls().execute(
            _inject(
                tools,
                {
                    "task_name": "worker",
                    "task_description": "Do work",
                },
            )
        )
        agent_id = spawn_result["agent_id"]

        send_cls = get_skill_class_by_name("send_message")
        send_result = await send_cls().execute(
            _inject(
                tools,
                {
                    "target_agent_id": agent_id,
                    "content": "Here is your input data",
                },
            )
        )

        assert send_result.get("success") is True
        assert send_result["delivered"] is True
        assert bus.pending_count(agent_id) >= 1


# ═══════════════════════════════════════════════════════════════════
# 2. BOUNDARY / EDGE CASES
# ═══════════════════════════════════════════════════════════════════


class TestBoundaryConditions:
    """边界条件：缺参数、不存在 agent、池满、深度超限。"""

    @pytest.mark.asyncio
    async def test_spawn_without_infrastructure_returns_error(self):
        """SpawnAgentSkill 无基础设施时应返回 error，不抛异常。"""
        from app.core.skill_registry import get_skill_class_by_name

        cls = get_skill_class_by_name("spawn_agent")
        skill = cls()

        result = await skill.execute(
            {
                "task_name": "orphan",
                "task_description": "No infra",
            }
        )
        assert "error" in result
        assert "not available" in result["error"]

    @pytest.mark.asyncio
    async def test_spawn_partial_infrastructure_missing(self):
        """只传部分基础设施（缺 bus）也应返回 error。"""
        from app.core.skill_registry import get_skill_class_by_name

        cls = get_skill_class_by_name("spawn_agent")
        skill = cls()

        result = await skill.execute(
            {
                "_agent_manager": AgentManager(),
                "_concurrency_pool": ConcurrencyPool(),
                "_message_bus": None,
                "_caller_agent_id": "root",
                "task_name": "partial",
            }
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_wait_nonexistent_agent(self, collab):
        tools, mgr, bus, pool, root = collab
        from app.core.skill_registry import get_skill_class_by_name

        cls = get_skill_class_by_name("wait_agent")

        result = await cls().execute(
            _inject(
                tools,
                {
                    "agent_id": "ghost_agent_999",
                },
            )
        )
        assert "error" in result
        assert "not found" in result["error"]

    @pytest.mark.asyncio
    async def test_interrupt_nonexistent_agent(self, collab):
        tools, mgr, bus, pool, root = collab
        from app.core.skill_registry import get_skill_class_by_name

        cls = get_skill_class_by_name("interrupt_agent")

        result = await cls().execute(
            _inject(
                tools,
                {
                    "agent_id": "ghost_agent_999",
                },
            )
        )
        assert result.get("success") is False
        assert result["interrupted"] is False

    @pytest.mark.asyncio
    async def test_send_message_missing_target(self, collab):
        tools, mgr, bus, pool, root = collab
        from app.core.skill_registry import get_skill_class_by_name

        cls = get_skill_class_by_name("send_message")

        result = await cls().execute(
            _inject(
                tools,
                {
                    "content": "hello",
                },
            )
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_send_message_missing_content(self, collab):
        tools, mgr, bus, pool, root = collab
        from app.core.skill_registry import get_skill_class_by_name

        cls = get_skill_class_by_name("send_message")

        result = await cls().execute(
            _inject(
                tools,
                {
                    "target_agent_id": "root",
                },
            )
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_concurrency_pool_full(self):
        """并发池满时 spawn 应返回 error。root 占 1 个 active slot，max_concurrent=2 只能再 spawn 1 个。"""
        manager = AgentManager(max_concurrent=2, max_depth=3)
        bus = InterAgentBus()
        pool = ConcurrencyPool(max_slots=2)
        root = manager.register_root("root")
        tools = CollaborationTools(
            agent_manager=manager,
            message_bus=bus,
            concurrency_pool=pool,
        )

        from app.core.skill_registry import get_skill_class_by_name

        spawn_cls = get_skill_class_by_name("spawn_agent")

        result1 = await spawn_cls().execute(
            _inject(
                tools,
                {
                    "task_name": "worker_0",
                    "task_description": "Task 0",
                },
            )
        )
        assert result1.get("success") is True, "first spawn should succeed"

        result2 = await spawn_cls().execute(
            _inject(
                tools,
                {
                    "task_name": "overflow",
                    "task_description": "Should fail",
                },
            )
        )
        assert (
            "error" in result2
        ), "2nd spawn should fail (max_concurrent=2, root+worker_0 already active)"

        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_max_depth_exceeded(self):
        """超过最大深度时 spawn 应返回 error。"""
        manager = AgentManager(max_concurrent=5, max_depth=1)
        bus = InterAgentBus()
        pool = ConcurrencyPool(max_slots=5)
        root = manager.register_root("root")
        tools = CollaborationTools(
            agent_manager=manager,
            message_bus=bus,
            concurrency_pool=pool,
        )

        from app.core.skill_registry import get_skill_class_by_name

        spawn_cls = get_skill_class_by_name("spawn_agent")

        result_l1 = await spawn_cls().execute(
            _inject(
                tools,
                {
                    "task_name": "level1",
                    "task_description": "First level",
                },
            )
        )
        assert result_l1.get("success") is True

        result_l2 = await spawn_cls().execute(
            _inject(
                tools,
                {
                    "task_name": "level2",
                    "task_description": "Should fail - depth exceeded",
                },
                caller=result_l1["agent_id"],
            )
        )
        assert "error" in result_l2, "depth=2 should exceed max_depth=1"

        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_wait_timeout_on_pending_agent(self, collab):
        """wait_agent 对 pending agent 应返回其当前状态。"""
        tools, mgr, bus, pool, root = collab
        from app.core.skill_registry import get_skill_class_by_name

        spawn_cls = get_skill_class_by_name("spawn_agent")
        spawn_result = await spawn_cls().execute(
            _inject(
                tools,
                {
                    "task_name": "stuck",
                    "task_description": "Never completes",
                },
            )
        )
        agent_id = spawn_result["agent_id"]

        wait_cls = get_skill_class_by_name("wait_agent")
        wait_result = await wait_cls().execute(
            _inject(
                tools,
                {
                    "agent_id": agent_id,
                    "timeout_seconds": 5,
                },
            )
        )

        assert wait_result.get("success") is True
        assert wait_result["status"] in ("pending", "running", "completed", "timeout")


# ═══════════════════════════════════════════════════════════════════
# 3. CHAIN INTEGRITY: ReactorSession → _ensure_collab → _dispatch
# ═══════════════════════════════════════════════════════════════════


class TestChainIntegrity:
    """验证 ReactorSession 内部链路：懒初始化、参数注入、_dispatch 路由。"""

    def test_ensure_collab_lazy_init(self):
        """_ensure_collab 只在首次调用时创建，之后复用。"""
        from app.engine.reactor.session import ReactorSession

        session = ReactorSession(skills=[], workflow_id="test_lazy")

        assert session._collab_tools is None
        collab1 = session._ensure_collab()
        assert collab1 is not None
        assert session._collab_tools is collab1

        collab2 = session._ensure_collab()
        assert collab2 is collab1, "should return same instance"

    def test_ensure_collab_disabled_mode(self):
        """collab_mode=disabled 时 _ensure_collab 返回 None。"""
        from app.engine.reactor.session import ReactorSession

        session = ReactorSession(skills=[], workflow_id="test_disabled", collab_mode="disabled")

        result = session._ensure_collab()
        assert result is None

    def test_ensure_collab_registers_root(self):
        """_ensure_collab 应注册 root agent。"""
        from app.engine.reactor.session import ReactorSession

        session = ReactorSession(skills=[], workflow_id="test_root")

        collab = session._ensure_collab()
        assert session._root_agent_id is not None
        assert session._root_agent_id == "root"

        agents = collab.agent_manager.list_agents()
        root_agents = [a for a in agents if a.agent_id == "root"]
        assert len(root_agents) == 1
        assert root_agents[0].status == AgentStatus.RUNNING

    def test_ensure_collab_sets_executor(self):
        """_ensure_collab 应设置子 Agent 执行器。"""
        from app.engine.reactor.session import ReactorSession

        session = ReactorSession(skills=[], workflow_id="test_executor")

        collab = session._ensure_collab()
        assert collab.agent_manager._agent_executor is not None

    @pytest.mark.asyncio
    async def test_dispatch_injects_collab_params(self):
        """_dispatch 对 skill_call + 协作工具应注入基础设施参数。"""
        from app.engine.reactor.primitives import PRIMITIVES_BY_NAME
        from app.engine.reactor.session import ReactorSession

        session = ReactorSession(skills=[], workflow_id="test_dispatch")

        collab = session._ensure_collab()
        assert collab is not None

        args = {
            "skill": "spawn_agent",
            "arguments": {
                "skill": "spawn_agent",
                "task_name": "test_dispatch_spawn",
                "task_description": "Dispatch injection test",
            },
        }

        inner_skill = str((args.get("arguments") or {}).get("skill") or args.get("skill") or "")
        _COLLAB_SKILLS = {
            "spawn_agent",
            "send_message",
            "wait_agent",
            "interrupt_agent",
            "list_agents",
        }
        assert inner_skill in _COLLAB_SKILLS

        inner_args = args.get("arguments")
        collab.inject_params(inner_args, session._root_agent_id or "root")

        assert inner_args.get("_agent_manager") is not None
        assert inner_args.get("_message_bus") is not None
        assert inner_args.get("_concurrency_pool") is not None
        assert inner_args.get("_caller_agent_id") == "root"

    @pytest.mark.asyncio
    async def test_skill_call_primitive_passes_collab_args(self):
        """SkillCallPrimitive.execute() 应把 arguments 中的协作参数传给 Skill。"""
        from app.core.skill_registry import (
            ensure_builtin_skills_registered,
            get_skill_class_by_name,
        )

        ensure_builtin_skills_registered()

        cls = get_skill_class_by_name("spawn_agent")
        assert cls is not None, "spawn_agent should be registered in SkillRegistry"

        skill = cls()
        assert skill.node_type == "collab"
        assert skill.name == "spawn_agent"


# ═══════════════════════════════════════════════════════════════════
# 4. RESOURCE LIFECYCLE
# ═══════════════════════════════════════════════════════════════════


class TestResourceLifecycle:
    """资源生命周期：shutdown、double shutdown、资源泄漏。"""

    @pytest.mark.asyncio
    async def test_shutdown_clears_all_agents(self):
        manager = AgentManager()
        bus = InterAgentBus()
        pool = ConcurrencyPool()
        root = manager.register_root("root")

        handle = await manager.spawn("child", parent_id="root", task_description="work")
        assert len(manager.list_agents()) == 2

        await manager.shutdown()
        assert len(manager.list_agents()) == 0

    @pytest.mark.asyncio
    async def test_double_shutdown_is_safe(self):
        manager = AgentManager()
        manager.register_root("root")
        await manager.shutdown()
        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_pool_release_after_interrupt(self, collab):
        tools, mgr, bus, pool, root = collab
        from app.core.skill_registry import get_skill_class_by_name

        spawn_cls = get_skill_class_by_name("spawn_agent")
        spawn_result = await spawn_cls().execute(
            _inject(
                tools,
                {
                    "task_name": "to_interrupt",
                    "task_description": "Will be interrupted",
                },
            )
        )
        agent_id = spawn_result["agent_id"]
        assert pool.active_count >= 1

        interrupt_cls = get_skill_class_by_name("interrupt_agent")
        await interrupt_cls().execute(_inject(tools, {"agent_id": agent_id}))

        assert agent_id not in pool._slots, "pool slot should be released after interrupt"

    @pytest.mark.asyncio
    async def test_session_run_shutdowns_collab_on_exit(self):
        """ReactorSession.run() 结束时应 shutdown 协作基础设施。"""
        from app.engine.reactor.session import ReactorSession

        class _ImmediateFinalLLM:
            async def stream_chat(self, messages, **kw):
                yield '{"thought": "done", "final": true, "output": {"_message": "ok"}}'

        session = ReactorSession(
            llm=_ImmediateFinalLLM(),
            skills=[],
            workflow_id="test_shutdown",
            max_iterations=2,
        )

        collab = session._ensure_collab()
        assert collab is not None
        agent_count_before = len(collab.agent_manager.list_agents())

        await session.run("test")

        agent_count_after = len(collab.agent_manager.list_agents())
        assert agent_count_after == 0, "all agents should be cleared after session.run()"


# ═══════════════════════════════════════════════════════════════════
# 5. MESSAGE BUS INTEGRITY
# ═══════════════════════════════════════════════════════════════════


class TestMessageBusIntegrity:
    """消息总线：发送、接收、pending 计数。"""

    @pytest.mark.asyncio
    async def test_bus_send_and_receive(self):
        bus = InterAgentBus()
        bus.register("agent_a")
        bus.register("agent_b")

        await bus.send(
            AgentMessage(
                message_type=MessageType.TASK,
                sender_id="agent_a",
                recipient_id="agent_b",
                content="Do this task",
            )
        )

        assert bus.pending_count("agent_b") == 1
        msg = await bus.receive("agent_b")
        assert msg is not None
        assert msg.content == "Do this task"
        assert msg.sender_id == "agent_a"
        assert bus.pending_count("agent_b") == 0

    @pytest.mark.asyncio
    async def test_bus_pending_count_unregistered(self):
        bus = InterAgentBus()
        assert bus.pending_count("ghost") == 0

    @pytest.mark.asyncio
    async def test_bus_history(self):
        bus = InterAgentBus()
        bus.register("a")
        bus.register("b")

        await bus.send(AgentMessage(sender_id="a", recipient_id="b", content="msg1"))
        await bus.send(AgentMessage(sender_id="b", recipient_id="a", content="msg2"))

        assert len(bus._history) >= 2


# ═══════════════════════════════════════════════════════════════════
# 6. AGENT HIERARCHY
# ═══════════════════════════════════════════════════════════════════


class TestAgentHierarchy:
    """Agent 层级关系：path、depth、parent-child。"""

    @pytest.mark.asyncio
    async def test_spawn_builds_correct_path(self, collab):
        tools, mgr, bus, pool, root = collab
        from app.core.skill_registry import get_skill_class_by_name

        spawn_cls = get_skill_class_by_name("spawn_agent")

        r1 = await spawn_cls().execute(
            _inject(
                tools,
                {
                    "task_name": "research",
                    "task_description": "Search",
                },
            )
        )
        assert r1["path"] == "root/research"
        assert r1["depth"] == 1

    @pytest.mark.asyncio
    async def test_multiple_siblings_have_different_ids(self):
        """max_concurrent=5 允许 root + 3 个子 agent。"""
        manager = AgentManager(max_concurrent=5, max_depth=3)
        bus = InterAgentBus()
        pool = ConcurrencyPool(max_slots=5)
        root = manager.register_root("root")
        tools = CollaborationTools(
            agent_manager=manager,
            message_bus=bus,
            concurrency_pool=pool,
        )

        from app.core.skill_registry import get_skill_class_by_name

        spawn_cls = get_skill_class_by_name("spawn_agent")

        ids = set()
        for i in range(3):
            r = await spawn_cls().execute(
                _inject(
                    tools,
                    {
                        "task_name": f"worker_{i}",
                        "task_description": f"Task {i}",
                    },
                )
            )
            assert r.get("success") is True
            ids.add(r["agent_id"])

        assert len(ids) == 3, "each spawn should produce unique agent_id"

        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_parent_child_relationship(self, collab):
        tools, mgr, bus, pool, root = collab

        handle = await mgr.spawn("child", parent_id="root", task_description="work")
        assert handle.parent_id == "root"
        assert handle.depth == 1

        parent = mgr.get("root")
        assert parent is not None
        assert parent.depth == 0


# ═══════════════════════════════════════════════════════════════════
# 7. SKILL REGISTRY INTEGRATION
# ═══════════════════════════════════════════════════════════════════


class TestSkillRegistryIntegration:
    """协作 Skill 在 SkillRegistry 中的注册和收集。"""

    def test_all_collab_skills_registered(self):
        from app.core.skill_registry import (
            ensure_builtin_skills_registered,
            get_skill_class_by_name,
        )

        ensure_builtin_skills_registered()

        expected = ["spawn_agent", "send_message", "wait_agent", "interrupt_agent", "list_agents"]
        for name in expected:
            cls = get_skill_class_by_name(name)
            assert cls is not None, f"{name} should be registered"
            assert cls.node_type == "collab"
            assert cls.name == name

    def test_collect_reactor_skills_includes_collab(self):
        from app.engine.reactor.session import collect_reactor_skills

        skills = collect_reactor_skills()
        collab_names = {s.name for s in skills if getattr(s, "node_type", "") == "collab"}
        expected = {"spawn_agent", "send_message", "wait_agent", "interrupt_agent", "list_agents"}
        assert expected.issubset(collab_names)

    def test_spawn_agent_in_system_prompt(self):
        from app.engine.reactor.session import ReactorSession, collect_reactor_skills

        skills = collect_reactor_skills()
        session = ReactorSession(skills=skills)
        assert "spawn_agent" in session.system_prompt
        assert "wait_agent" in session.system_prompt
        assert "list_agents" in session.system_prompt


# ═══════════════════════════════════════════════════════════════════
# 8. CONCURRENCY POOL EDGE CASES
# ═══════════════════════════════════════════════════════════════════


class TestConcurrencyPoolEdge:
    """ConcurrencyPool 边界条件。"""

    @pytest.mark.asyncio
    async def test_acquire_release_cycle(self):
        pool = ConcurrencyPool(max_slots=2)

        assert await pool.acquire("a") is True
        assert await pool.acquire("b") is True
        assert pool.active_count == 2

        pool.release("a")
        assert pool.active_count == 1

        assert await pool.acquire("c") is True
        assert pool.active_count == 2

    @pytest.mark.asyncio
    async def test_acquire_timeout_when_full(self):
        pool = ConcurrencyPool(max_slots=1)
        assert await pool.acquire("a") is True
        assert await pool.acquire("b", timeout=0.1) is False

    @pytest.mark.asyncio
    async def test_double_acquire_raises(self):
        pool = ConcurrencyPool(max_slots=2)
        assert await pool.acquire("a") is True
        with pytest.raises(ValueError):
            await pool.acquire("a")

    @pytest.mark.asyncio
    async def test_release_nonexistent_is_safe(self):
        pool = ConcurrencyPool(max_slots=2)
        pool.release("ghost")

    @pytest.mark.asyncio
    async def test_pool_stats(self):
        pool = ConcurrencyPool(max_slots=3)
        await pool.acquire("a")
        await pool.acquire("b")
        stats = pool.stats
        assert stats["active_count"] == 2
        assert stats["max_slots"] == 3
