"""五期-C 专项验证：长任务进度通道。

10 项检查覆盖所有改动点，全部打印 OK/FAIL，退出码 = 失败数。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

failures = 0


def check(name: str, condition: bool, detail: str = ""):
    global failures
    tag = "OK" if condition else "FAIL"
    print(f"  [{tag}] {name}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures += 1


print("=" * 60)
print("五期-C 验证：长任务进度通道")
print("=" * 60)

# ─── 1. capability_service emit_progress 机制 ───
print("\n1. capability_service 进度发射机制")
from capability_service.main import _progress_callback, emit_progress, set_progress_callback

check("emit_progress 函数存在", callable(emit_progress))
check("set_progress_callback 函数存在", callable(set_progress_callback))
check("_progress_callback 初始为 None", _progress_callback is None)

# ─── 2. set_progress_callback 注入 ───
print("\n2. 回调注入")
received = []


async def _test_cb(event):
    received.append(event)


set_progress_callback(_test_cb)
from capability_service.main import _progress_callback as _cb_after

check("回调注入成功", _cb_after is _test_cb)
set_progress_callback(None)

# ─── 3. /internal/events 端点存在 ───
print("\n3. 主进程 /internal/events 端点")
from app.api.routers.internal import ProgressEventRequest, router

check("internal router 存在", router is not None)
check("router prefix = /internal", router.prefix == "/internal")
check("ProgressEventRequest 模型存在", ProgressEventRequest is not None)

fields = set(ProgressEventRequest.model_fields.keys())
check(
    "ProgressEventRequest 字段完整", fields == {"workflow_id", "event_type", "payload", "turn_id"}
)

# ─── 4. capability_event_bridge 模块 ───
print("\n4. CapabilityEventBridge")
from app.services.capability_event_bridge import (
    _httpx_progress_callback,
    _main_process_events_url,
    emit_progress_via_bridge,
    install_progress_bridge,
)

check("_main_process_events_url 函数存在", callable(_main_process_events_url))
check("_httpx_progress_callback 函数存在", callable(_httpx_progress_callback))
check("install_progress_bridge 函数存在", callable(install_progress_bridge))
check("emit_progress_via_bridge 函数存在", callable(emit_progress_via_bridge))

# ─── 5. CAPABILITY_EVENTS_URL 环境变量 ───
print("\n5. 环境变量配置")
import os

url = _main_process_events_url()
check("CAPABILITY_EVENTS_URL 默认为空", url == "", f"url={url!r}")

# ─── 6. install_progress_bridge 无环境变量时不安装 ───
print("\n6. 桥安装行为")
old = os.environ.pop("CAPABILITY_EVENTS_URL", None)
try:
    result = install_progress_bridge()
    check("无环境变量 → 不安装", result is False)
finally:
    if old is not None:
        os.environ["CAPABILITY_EVENTS_URL"] = old

# ─── 7. main.py 注册了 internal router ───
print("\n7. 主 API 路由注册")
from app.main import app

found_internal = False
for r in app.routes:
    if hasattr(r, "original_router"):
        for sub in getattr(r.original_router, "routes", []):
            if getattr(sub, "path", "") == "/internal/events":
                found_internal = True
                break
    if found_internal:
        break
check("/internal/events 路由已注册", found_internal)

# ─── 8. ProgressEventRequest schema 与 sse_bus.publish 对齐 ───
print("\n8. 事件 schema 对齐")
from app.services.sse_bus import SSEEvent

sse_fields = set(SSEEvent.model_fields.keys())
progress_fields = set(ProgressEventRequest.model_fields.keys())
mapping = {
    "workflow_id": "event_id",
    "event_type": "event_type",
    "payload": "payload",
    "turn_id": "turn_id",
}
check("event_type 字段对齐", "event_type" in progress_fields and "event_type" in sse_fields)
check("payload 字段对齐", "payload" in progress_fields and "payload" in sse_fields)
check("turn_id 字段对齐", "turn_id" in progress_fields and "turn_id" in sse_fields)

# ─── 9. emit_progress_via_bridge 进程内模式走 sse_bus ───
print("\n9. emit_progress_via_bridge 进程内模式")
import asyncio


async def _test_inprocess():
    from app.services.sse_bus import sse_bus

    wf = "test_bridge_inprocess"
    await emit_progress_via_bridge(wf, "test_event", {"k": "v"}, turn_id="t1")
    history = sse_bus._event_history.get(wf, [])
    ok = len(history) >= 1 and history[-1].event_type == "test_event"
    sse_bus.cleanup_workflow(wf)
    return ok


result = asyncio.get_event_loop().run_until_complete(_test_inprocess())
check("进程内模式 → sse_bus.publish", result)

# ─── 10. 零泄漏：internal 端点无鉴权依赖 ───
print("\n10. 零泄漏检查")
import inspect

from app.api.routers.internal import receive_capability_event

src = inspect.getsource(receive_capability_event)
check("internal 端点不 import get_current_user", "get_current_user" not in src)

# ─── 汇总 ───
print("\n" + "=" * 60)
if failures == 0:
    print("全部通过")
else:
    print(f"失败 {failures} 项")
print("=" * 60)
sys.exit(failures)
