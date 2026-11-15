"""五期-F 集成验收：全量跑通五期 A~E 所有验证项。"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
PYTHON = str(BACKEND / ".venv" / "Scripts" / "python.exe")

passed = 0
failed = 0


def run(label: str, cmd: list[str]) -> bool:
    global passed, failed
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(BACKEND))
    ok = result.returncode == 0
    if ok:
        passed += 1
        print(f"  [OK] {label}")
    else:
        failed += 1
        print(f"  [FAIL] {label}")
        tail = (result.stderr or result.stdout or "")[-500:]
        if tail:
            print(f"        {tail.strip()}")
    return ok


print("=" * 60)
print("五期-F 集成验收")
print("=" * 60)

# ─── 1. 契约测试 ───
print("\n1. 契约测试（34 项）")
run(
    "test_capability_http_contract.py 全绿",
    [PYTHON, "-m", "pytest", "tests/contract/test_capability_http_contract.py", "-q"],
)

# ─── 2. 五期-B 专项验证 ───
print("\n2. 五期-B：capability.toml runtime 声明")
run("verify_runtime_declaration.py", [PYTHON, "scripts/verify_runtime_declaration.py"])

# ─── 3. 五期-C 专项验证 ───
print("\n3. 五期-C：长任务进度通道")
run("verify_progress_channel.py", [PYTHON, "scripts/verify_progress_channel.py"])

# ─── 4. 五期-D 专项验证 ───
print("\n4. 五期-D：media-service")
run("verify_media_service.py", [PYTHON, "scripts/verify_media_service.py"])

# ─── 5. 五期-E 专项验证 ───
print("\n5. 五期-E：spider-service")
run("verify_spider_service.py", [PYTHON, "scripts/verify_spider_service.py"])

# ─── 6. capability.toml 四域 runtime 完整性 ───
print("\n6. capability.toml 四域 runtime 声明完整性")
from app.core.capability_loader import get_capability_runtime

for cap in ("grid", "browser", "media", "spider"):
    rt = get_capability_runtime(cap)
    ok = rt == "inprocess"
    if ok:
        passed += 1
        print(f"  [OK] [{cap}] runtime = inprocess")
    else:
        failed += 1
        print(f"  [FAIL] [{cap}] runtime = {rt!r}, expected 'inprocess'")

# ─── 7. capability-service 工具全景 ───
print("\n7. capability-service 工具全景（6 个）")
from capability_service.main import CAPABILITY_TOOLS

expected_tools = {"grid_detect", "grid_split", "render_htmls", "tts_piper", "video_compose", "spider_sync"}
actual_tools = set(CAPABILITY_TOOLS.keys())
ok = actual_tools == expected_tools
if ok:
    passed += 1
    print(f"  [OK] 工具集 = {sorted(expected_tools)}")
else:
    failed += 1
    print(f"  [FAIL] 期望 {sorted(expected_tools)}, 实际 {sorted(actual_tools)}")

# ─── 8. 访问层完整性 ───
print("\n8. 访问层完整性（grid / browser / media / spider）")
import importlib

for mod_name in ("grid_capability", "browser_capability", "media_capability", "spider_capability"):
    mod = importlib.import_module(f"app.services.{mod_name}")
    has_url = hasattr(mod, "_service_url")
    if has_url:
        passed += 1
        print(f"  [OK] {mod_name}._service_url() 存在")
    else:
        failed += 1
        print(f"  [FAIL] {mod_name}._service_url() 不存在")

# ─── 9. Settings 四域 service_url 字段 ───
print("\n9. Settings 四域 service_url 字段")
from app.config import get_settings

s = get_settings()
for field in ("grid_service_url", "browser_service_url", "media_service_url", "spider_service_url"):
    val = getattr(s, field, None)
    ok = val == ""
    if ok:
        passed += 1
        print(f"  [OK] Settings.{field} = '' (默认 inprocess)")
    else:
        failed += 1
        print(f"  [FAIL] Settings.{field} = {val!r}, expected ''")

# ─── 10. _SERVICE_URL_FIELDS 映射完整性 ───
print("\n10. _SERVICE_URL_FIELDS 映射完整性")
from app.core.capability_loader import _SERVICE_URL_FIELDS

expected_map = {
    "grid": "grid_service_url",
    "browser": "browser_service_url",
    "media": "media_service_url",
    "spider": "spider_service_url",
}
for cap, field in expected_map.items():
    actual = _SERVICE_URL_FIELDS.get(cap)
    ok = actual == field
    if ok:
        passed += 1
        print(f"  [OK] {cap} -> {field}")
    else:
        failed += 1
        print(f"  [FAIL] {cap} -> {actual!r}, expected {field!r}")

# ─── 11. 进度通道端点注册 ───
print("\n11. /internal/events 端点注册")
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
if found_internal:
    passed += 1
    print("  [OK] /internal/events 端点已注册")
else:
    failed += 1
    print("  [FAIL] /internal/events 端点未找到")

# ─── 12. CapabilityEventBridge 完整性 ───
print("\n12. CapabilityEventBridge 完整性")
from app.services import capability_event_bridge

for fn in ("install_progress_bridge", "emit_progress_via_bridge", "_main_process_events_url", "_httpx_progress_callback"):
    ok = hasattr(capability_event_bridge, fn)
    if ok:
        passed += 1
        print(f"  [OK] {fn} 存在")
    else:
        failed += 1
        print(f"  [FAIL] {fn} 不存在")

# ─── 结果 ───
print("\n" + "=" * 60)
if failed == 0:
    print(f"全部通过 ({passed} 项)")
else:
    print(f"失败 {failed} 项，通过 {passed} 项")
    sys.exit(1)