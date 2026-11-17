"""BUG-2 修复回归（2026-11-12）：恢复端点按 loop_state_path 串行化。

修复前：5 个恢复端点里 3 个用 threading.RLock 跨 await（同线程可重入＝失效），
2 个完全无锁；两个并发请求会各自读到同一份 loop_state 并重复续跑。
修复后：统一走 loop_state._get_async_lock(path)（asyncio.Lock），
锁字典有上限清理，不再只增不减。

全离线，零真机、零网络。
"""

# 注意：本文件故意不启用 `from __future__ import annotations`。
# 最后一个用例在局部作用域定义 FastAPI 依赖，若注解被延迟成字符串，
# FastAPI 会解析不到 Request 而把参数当成 query（422）。
import ast
import asyncio
import json
from pathlib import Path

import pytest

from app.engine.reactor import loop_state

CHAT_AGENT = Path(__file__).resolve().parents[1] / "app/api/routers/chat_agent.py"


@pytest.fixture
def state_name(tmp_path, monkeypatch):
    """把状态目录指到临时目录，放一份待恢复状态。"""
    monkeypatch.setattr(loop_state, "_STATE_DIR", tmp_path)
    name = "resume_state.json"
    (tmp_path / name).write_text(
        json.dumps({"messages": [], "context_snapshot": {}}, ensure_ascii=False),
        encoding="utf-8",
    )
    return name


async def test_concurrent_resume_only_first_succeeds(state_name):
    """核心回归：两个并发恢复必须串行，后到者读到状态已清理 → 404。"""
    lock = loop_state._get_async_lock(state_name)
    trace: list[str] = []

    async def resume(tag: str) -> None:
        await lock.acquire()
        try:
            trace.append(f"{tag}:enter")
            loop_state.load_loop_state(state_name)
            trace.append(f"{tag}:loaded")
            await asyncio.sleep(0)  # 模拟端点里 await session.run() 让出事件循环
            loop_state.cleanup_loop_state(state_name)
            trace.append(f"{tag}:cleanup")
        except FileNotFoundError:
            trace.append(f"{tag}:404")
        finally:
            lock.release()

    await asyncio.gather(resume("A"), resume("B"))
    assert trace == [
        "A:enter",
        "A:loaded",
        "A:cleanup",
        "B:enter",
        "B:404",
    ], f"恢复未串行化，会有重复续跑风险：{trace}"


def test_same_path_shares_one_async_lock():
    a = loop_state._get_async_lock("same-path-probe")
    b = loop_state._get_async_lock("same-path-probe")
    assert a is b, "同一 loop_state_path 必须共用同一把锁，否则串行化无效"


def test_lock_tables_are_bounded():
    """两个锁字典都有上限清理，不能随暂停次数无限增长。"""
    prefix = "bounded-probe-"
    try:
        for i in range(loop_state._MAX_LOCK_ENTRIES + 20):
            loop_state._get_async_lock(f"{prefix}{i}")
            loop_state._get_lock(f"{prefix}{i}")
        assert len(loop_state._async_locks) <= loop_state._MAX_LOCK_ENTRIES
        assert len(loop_state._locks) <= loop_state._MAX_LOCK_ENTRIES
    finally:
        for table in (loop_state._locks, loop_state._async_locks):
            for key in [k for k in table if str(k).startswith(prefix)]:
                table.pop(key, None)


def test_all_five_resume_endpoints_are_guarded():
    """覆盖面：3 个端点函数体内用 async 锁，2 个端点由路由依赖保护。"""
    tree = ast.parse(CHAT_AGENT.read_text(encoding="utf-8-sig"))
    funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)}
    inline = (
        "clarify_action",
        "confirm_storyboard_action",
        "confirm_cut_plan_action",
    )
    for name in inline:
        src = ast.unparse(funcs[name])
        assert "_get_async_lock" in src, f"{name} 未使用 async 锁"
        assert "await _file_lock.acquire()" in src, f"{name} 未 await 获取锁"
        assert "threading.RLock" not in src

    by_dependency = ("confirm_action", "confirm_guardian_action")
    for name in by_dependency:
        decorators = funcs[name].decorator_list
        has_dep = any(
            isinstance(d, ast.Call) and any(kw.arg == "dependencies" for kw in d.keywords)
            for d in decorators
        )
        assert has_dep, f"{name} 缺少串行化依赖"

    assert funcs["_serialize_resume_by_state"]  # 依赖函数必须存在


def test_yield_dependency_does_not_break_body_parsing():
    """依赖项里 await request.json() 后，端点仍能正常解析 Pydantic body。"""
    from fastapi import Depends, FastAPI, Request
    from fastapi.testclient import TestClient
    from pydantic import BaseModel

    class _Body(BaseModel):
        loop_state_path: str
        action: str = "approve"

    app = FastAPI()
    seen: list[str] = []

    async def dep(http_request: Request):
        body = await http_request.json()
        seen.append(str(body.get("loop_state_path") or ""))
        yield

    @app.post("/probe", dependencies=[Depends(dep)])
    async def probe(body: _Body):
        return {"path": body.loop_state_path, "action": body.action}

    resp = TestClient(app).post("/probe", json={"loop_state_path": "p1", "action": "approve"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"path": "p1", "action": "approve"}
    assert seen == ["p1"]
