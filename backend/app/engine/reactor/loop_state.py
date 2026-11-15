"""Loop state persistence for ReactorSession pause/resume.

Stores serialized session state to disk so that confirm/clarify endpoints
can resume a paused ReAct loop after user decision.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import threading
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

def _default_state_dir() -> str:
    _tmp = os.environ.get("TEMP") or os.environ.get("TMP") or os.environ.get("TMPDIR")
    if _tmp:
        return str(Path(_tmp) / "reactor_loop_states")
    return "/tmp/reactor_loop_states"


_STATE_DIR = Path(os.environ.get("REACTOR_STATE_DIR", _default_state_dir()))
_STATE_TTL_SECONDS = int(os.environ.get("REACTOR_STATE_TTL", "3600"))

_MAX_LOCK_ENTRIES = 1024

# 同步锁：只用于 save/load/cleanup 的短临界区（这些函数不跨 await）。
_locks: dict[str, threading.RLock] = {}
_locks_lock = threading.Lock()

# 异步锁：恢复端点按 loop_state_path 串行化（2026-11-12 BUG-2 修复）。
# threading.RLock 按线程可重入，单 worker 事件循环里 A 持锁 await 后 B 能立刻
# acquire 成功；必须用 asyncio.Lock 才能让后来的协程真正等在前一个后面。
_async_locks: dict[str, asyncio.Lock] = {}
_async_locks_guard = threading.Lock()


def _prune_locks(table: dict, in_use) -> None:
    """超过上限时清掉「当前没被持有」的条目，防止字典只增不减。

    正在被持有的锁不会删（等待中的请求仍共用同一把锁对象），
    所以清理不会破坏并发串行化语义。
    """
    if len(table) < _MAX_LOCK_ENTRIES:
        return
    for key, lock in list(table.items()):
        if not in_use(lock):
            table.pop(key, None)


def _ensure_dir() -> None:
    _STATE_DIR.mkdir(parents=True, exist_ok=True)


def _get_lock(path: str) -> threading.RLock:
    with _locks_lock:
        if path not in _locks:
            _prune_locks(_locks, lambda lk: getattr(lk, "_is_owned", lambda: False)())
            _locks[path] = threading.RLock()
    return _locks[path]


def _get_async_lock(path: str) -> asyncio.Lock:
    """按 loop_state_path 返回一把 asyncio.Lock，供恢复端点跨 await 串行化。

    用法：`await lock.acquire()` + try/finally `lock.release()`，或 `async with lock:`。
    不要混用 threading.RLock —— 后者在单线程事件循环里可重入，等于没有锁。
    """
    with _async_locks_guard:
        if path not in _async_locks:
            _prune_locks(_async_locks, lambda lk: lk.locked())
            _async_locks[path] = asyncio.Lock()
    return _async_locks[path]


def save_loop_state(state: dict[str, Any], path: str | None = None) -> str:
    """Save loop state to disk, return the path."""
    _ensure_dir()
    if path is None:
        path = f"loop_{state.get('context_snapshot', {}).get('workflow_id', 'unknown')}_{int(time.time()*1000)}"
    lock = _get_lock(path)
    with lock:
        full_path = _STATE_DIR / path
        state["_saved_at"] = time.time()
        full_path.write_text(json.dumps(state, ensure_ascii=False, default=str), encoding="utf-8")
    return path


def load_loop_state(path: str) -> dict[str, Any]:
    """Load loop state from disk."""
    # 2026-10-21：前端某张澄清卡会带空 loop_state_path，`_STATE_DIR / ""`
    # 等于状态目录本身，read_text 对目录抛 PermissionError（非
    # FileNotFoundError），/clarify 的 404 分支接不住变成 500。空路径
    # 统一按"状态不存在"处理。
    if not path or not str(path).strip():
        raise FileNotFoundError("Loop state path is empty")
    # 路径穿越防护：解析后必须仍位于状态目录内（拒绝 ../ 与绝对路径）。
    try:
        base = _STATE_DIR.resolve()
        full_path = (_STATE_DIR / path).resolve()
    except OSError as exc:
        raise FileNotFoundError(f"Illegal loop state path: {path}") from exc
    if base != full_path and base not in full_path.parents:
        raise FileNotFoundError(f"Illegal loop state path: {path}")
    lock = _get_lock(path)
    with lock:
        full_path = _STATE_DIR / path
        if not full_path.is_file():
            raise FileNotFoundError(f"Loop state not found: {path}")
        data = json.loads(full_path.read_text(encoding="utf-8"))
    return data


def cleanup_loop_state(path: str) -> None:
    """Delete loop state file after resume."""
    lock = _get_lock(path)
    with lock:
        full_path = _STATE_DIR / path
        try:
            full_path.unlink(missing_ok=True)
        except Exception:
            pass


def check_clarification_timeout(state: dict[str, Any]) -> str:
    """Check if clarification has timed out. Returns 'expired', 'pending', or 'ok'."""
    if state.get("_clarification_expired"):
        return "expired"
    saved_at = state.get("_saved_at", 0)
    timeout = state.get("clarification_timeout", _STATE_TTL_SECONDS)
    if saved_at and (time.time() - saved_at) > timeout:
        return "expired"
    return "ok"


def mark_loop_state_expired(path: str) -> None:
    """Mark loop state as expired."""
    try:
        state = load_loop_state(path)
        state["_clarification_expired"] = True
        save_loop_state(state, path)
    except Exception:
        pass