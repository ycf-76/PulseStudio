"""端到端验证：skill_create 用户隔离。"""

import asyncio
from pathlib import Path

import pytest

from app.core.prompt_skills import _USER_SKILLS_DIR
from app.core.skill_registry import SkillRegistry, ensure_builtin_skills_registered
from app.engine.schemas import WorkflowContext
from app.skills.evolve.skill_create import SkillCreate


@pytest.fixture(autouse=True)
def _clean_registry():
    registry = SkillRegistry.instance()
    registry.reset()
    ensure_builtin_skills_registered()
    yield
    registry.reset()
    if _USER_SKILLS_DIR.exists():
        import shutil

        shutil.rmtree(_USER_SKILLS_DIR, ignore_errors=True)


def _run(coro):
    return asyncio.run(coro)


def test_no_user_id_rejected():
    skill = SkillCreate()
    result = _run(
        skill.execute(
            {
                "filename": "test-skill.md",
                "content": "---\nnode_type: test\nname: my_test\n---\nbody",
            }
        )
    )
    assert result["success"] is False
    assert "用户" in result["message"]


def test_user_a_create_skill():
    skill = SkillCreate()
    ctx = WorkflowContext(
        workflow_id="w1", node_id="n1", user_id="user_alice", account_id="", topic="t"
    )
    result = _run(
        skill.execute(
            {
                "filename": "my-copywrite-style.md",
                "content": "---\nnode_type: copywrite\nname: my_style\ndisplay_name: Alice Style\n---\nAlice body",
                "_context": ctx,
            }
        )
    )
    assert result["success"] is True
    assert result["skill_name"] == "my_style"


def test_same_name_different_users_no_conflict():
    skill = SkillCreate()
    ctx_a = WorkflowContext(
        workflow_id="w1", node_id="n1", user_id="user_alice", account_id="", topic="t"
    )
    ctx_b = WorkflowContext(
        workflow_id="w2", node_id="n2", user_id="user_bob", account_id="", topic="t"
    )

    r1 = _run(
        skill.execute(
            {
                "filename": "my-copywrite-style.md",
                "content": "---\nnode_type: copywrite\nname: my_style\ndisplay_name: Alice Style\n---\nAlice",
                "_context": ctx_a,
            }
        )
    )
    r2 = _run(
        skill.execute(
            {
                "filename": "my-copywrite-style.md",
                "content": "---\nnode_type: copywrite\nname: my_style\ndisplay_name: Bob Style\n---\nBob",
                "_context": ctx_b,
            }
        )
    )
    assert r1["success"] is True
    assert r2["success"] is True


def test_query_isolation():
    registry = SkillRegistry.instance()
    skill = SkillCreate()
    ctx_a = WorkflowContext(
        workflow_id="w1", node_id="n1", user_id="user_alice", account_id="", topic="t"
    )
    ctx_b = WorkflowContext(
        workflow_id="w2", node_id="n2", user_id="user_bob", account_id="", topic="t"
    )

    _run(
        skill.execute(
            {
                "filename": "my-copywrite-style.md",
                "content": "---\nnode_type: copywrite\nname: my_style\ndisplay_name: Alice Style\n---\nAlice",
                "_context": ctx_a,
            }
        )
    )
    _run(
        skill.execute(
            {
                "filename": "my-copywrite-style.md",
                "content": "---\nnode_type: copywrite\nname: my_style\ndisplay_name: Bob Style\n---\nBob",
                "_context": ctx_b,
            }
        )
    )

    a = registry.get("copywrite", "my_style", user_id="user_alice")
    b = registry.get("copywrite", "my_style", user_id="user_bob")
    c = registry.get("copywrite", "my_style", user_id="user_charlie")

    assert a is not None and a.display_name == "Alice Style"
    assert b is not None and b.display_name == "Bob Style"
    assert c is None


def test_file_written_to_user_dir():
    skill = SkillCreate()
    ctx = WorkflowContext(
        workflow_id="w1", node_id="n1", user_id="user_dave", account_id="", topic="t"
    )
    _run(
        skill.execute(
            {
                "filename": "dave-skill.md",
                "content": "---\nnode_type: test\nname: dave_test\ndisplay_name: Dave Test\n---\nDave body",
                "_context": ctx,
            }
        )
    )

    target = _USER_SKILLS_DIR / "user_dave" / "dave-skill.md"
    assert target.exists()

    target.unlink()
    try:
        (target.parent).rmdir()
    except OSError:
        pass


def test_unregister_user_skill():
    registry = SkillRegistry.instance()
    skill = SkillCreate()
    ctx = WorkflowContext(
        workflow_id="w1", node_id="n1", user_id="user_eve", account_id="", topic="t"
    )
    _run(
        skill.execute(
            {
                "filename": "eve-skill.md",
                "content": "---\nnode_type: test\nname: eve_test\ndisplay_name: Eve Test\n---\nEve body",
                "_context": ctx,
            }
        )
    )

    assert registry.get("test", "eve_test", user_id="user_eve") is not None
    assert registry.unregister("test", "eve_test", user_id="user_eve") is True
    assert registry.get("test", "eve_test", user_id="user_eve") is None

    target = _USER_SKILLS_DIR / "user_eve" / "eve-skill.md"
    if target.exists():
        target.unlink()
    try:
        (target.parent).rmdir()
    except OSError:
        pass
