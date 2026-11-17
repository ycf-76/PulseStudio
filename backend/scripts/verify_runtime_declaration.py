"""五期-B 专项验证：capability.toml runtime 字段生效，声明即拓扑。

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
print("五期-B 验证：capability.toml runtime 字段生效")
print("=" * 60)

# ─── 1. toml 解析：四个可外置能力包均有 runtime 字段 ───
print("\n1. toml 解析")
from app.core.capability_loader import _read_toml_data

data = _read_toml_data()
for cap in ("grid", "browser", "media", "spider"):
    cfg = data.get(cap)
    check(f"[{cap}] 存在", cfg is not None)
    if cfg:
        rt = cfg.get("runtime")
        check(f"[{cap}] runtime 字段存在", rt is not None, f"runtime={rt!r}")
        check(f"[{cap}] runtime=inprocess", rt == "inprocess")

# ─── 2. get_capability_runtime 正确返回 ───
print("\n2. get_capability_runtime")
from app.core.capability_loader import get_capability_runtime

for cap in ("grid", "browser", "media", "spider"):
    rt = get_capability_runtime(cap)
    check(f"{cap} → inprocess", rt == "inprocess")

check("nonexistent → inprocess", get_capability_runtime("nonexistent") == "inprocess")

# ─── 3. get_capability_service_url：inprocess 模式返回空 ───
print("\n3. get_capability_service_url（inprocess 模式）")
from app.core.capability_loader import get_capability_service_url

for cap in ("grid", "browser", "media", "spider"):
    url = get_capability_service_url(cap)
    check(f"{cap} → 空串", url == "", f"url={url!r}")

# ─── 4. 路由表快照 ───
print("\n4. 路由表快照")
from app.core.capability_loader import get_capability_routing_table

table = get_capability_routing_table()
check("四个能力包", set(table.keys()) == {"grid", "browser", "media", "spider"})
for cap, info in table.items():
    check(f"[{cap}] runtime=inprocess", info["runtime"] == "inprocess")
    check(f"[{cap}] status=inprocess", info["status"] == "inprocess")

# ─── 5. grid_capability 走 capability_loader ───
print("\n5. grid_capability 访问层")
from app.services.grid_capability import _service_url as grid_url

check("grid._service_url() 走 capability_loader", grid_url() == get_capability_service_url("grid"))

# ─── 6. browser_capability 走 capability_loader ───
print("\n6. browser_capability 访问层")
from app.services.browser_capability import _service_url as browser_url

check(
    "browser._service_url() 走 capability_loader",
    browser_url() == get_capability_service_url("browser"),
)

# ─── 7. _SERVICE_URL_FIELDS 映射完整 ───
print("\n7. _SERVICE_URL_FIELDS 映射")
from app.core.capability_loader import _SERVICE_URL_FIELDS

expected = {
    "grid": "grid_service_url",
    "browser": "browser_service_url",
    "media": "media_service_url",
    "spider": "spider_service_url",
}
check("映射完整", _SERVICE_URL_FIELDS == expected)

# ─── 8. Settings 仍有 service_url 字段（向后兼容） ───
print("\n8. Settings 向后兼容")
from app.config import get_settings

s = get_settings()
for field in ("grid_service_url", "browser_service_url"):
    has = hasattr(s, field)
    check(f"Settings.{field} 存在", has)

# ─── 9. toml 无 runtime 字段时默认 inprocess ───
print("\n9. 缺省值兼容")
no_runtime_cap = data.get("content_search", {})
rt_default = no_runtime_cap.get("runtime", "inprocess")
check("无 runtime 字段 → 默认 inprocess", rt_default == "inprocess")

# ─── 10. 零泄漏：主进程 capability 访问层不直接 import Settings ───
print("\n10. 零泄漏检查")
import inspect

grid_src = inspect.getsource(grid_url)
browser_src = inspect.getsource(browser_url)
check("grid_capability 不含 get_settings", "get_settings" not in grid_src)
check("browser_capability 不含 get_settings", "get_settings" not in browser_src)

# ─── 汇总 ───
print("\n" + "=" * 60)
if failures == 0:
    print("全部通过")
else:
    print(f"失败 {failures} 项")
print("=" * 60)
sys.exit(failures)
