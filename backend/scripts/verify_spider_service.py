"""五期-E 专项验证：spider-service（Scrapling+Playwright 隔离）。"""
from __future__ import annotations

import sys

passed = 0
failed = 0


def check(label: str, cond: bool, detail: str = ""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  [OK] {label}")
    else:
        failed += 1
        print(f"  [FAIL] {label}  {detail}")


print("=" * 60)
print("五期-E 验证：spider-service（Scrapling+Playwright 隔离）")
print("=" * 60)

# ─── 1. capability-service 注册 ───
print("\n1. capability-service 工具注册")
from capability_service.main import CAPABILITY_TOOLS

check("spider_sync 已注册", "spider_sync" in CAPABILITY_TOOLS)
check("工具总数 = 6", len(CAPABILITY_TOOLS) == 6, f"actual={len(CAPABILITY_TOOLS)}")

# ─── 2. handler 签名 ───
print("\n2. handler 签名")
import inspect

spider_handler = CAPABILITY_TOOLS["spider_sync"]["handler"]
check("spider_sync handler 是协程函数", inspect.iscoroutinefunction(spider_handler))

sig = inspect.signature(spider_handler)
check("签名含 account_id", "account_id" in sig.parameters)
check("签名含 user_id", "user_id" in sig.parameters)
check("签名含 platform", "platform" in sig.parameters)
check("签名含 force", "force" in sig.parameters)

# ─── 3. is_async 标记 ───
print("\n3. is_async 标记")
check("spider_sync is_async=True", CAPABILITY_TOOLS["spider_sync"].get("is_async") is True)

# ─── 4. spider_capability 访问层 ───
print("\n4. spider_capability 访问层")
from app.services import spider_capability

check("start_sync 函数存在", hasattr(spider_capability, "start_sync"))
check("_service_url 函数存在", hasattr(spider_capability, "_service_url"))
check("start_sync 是协程函数", inspect.iscoroutinefunction(spider_capability.start_sync))

# ─── 5. _service_url 走 capability_loader ───
print("\n5. _service_url 走 capability_loader")
from app.core.capability_loader import get_capability_service_url

check(
    "_service_url() == get_capability_service_url('spider')",
    spider_capability._service_url() == get_capability_service_url("spider"),
)
check("默认 inprocess -> 空串", spider_capability._service_url() == "")

# ─── 6. Settings.spider_service_url ───
print("\n6. Settings.spider_service_url")
from app.config import get_settings

s = get_settings()
check("字段存在", hasattr(s, "spider_service_url"))
check("默认为空", s.spider_service_url == "")

# ─── 7. capability.toml [spider] runtime ───
print("\n7. capability.toml [spider] runtime 字段")
from app.core.capability_loader import get_capability_runtime

check("spider runtime 存在", get_capability_runtime("spider") is not None)
check("spider runtime = inprocess", get_capability_runtime("spider") == "inprocess")

# ─── 8. _SERVICE_URL_FIELDS 映射 ───
print("\n8. _SERVICE_URL_FIELDS 映射")
from app.core.capability_loader import _SERVICE_URL_FIELDS

check("spider -> spider_service_url", _SERVICE_URL_FIELDS.get("spider") == "spider_service_url")

# ─── 9. 进程内模式 import 链 ───
print("\n9. 进程内模式 import 链")
import ast
import textwrap

src = textwrap.dedent(inspect.getsource(spider_capability.start_sync))
tree = ast.parse(src)
local_imports = [
    n.module
    for n in ast.walk(tree)
    if isinstance(n, ast.ImportFrom) and n.module and "runner" in n.module
]
check("start_sync 进程内 import runner", len(local_imports) > 0, f"found={local_imports}")

# ─── 10. 零泄漏 ───
print("\n10. 零泄漏检查")
sc_src = inspect.getsource(spider_capability)
check("spider_capability 不含 get_settings", "get_settings" not in sc_src)

# ─── 结果 ───
print("\n" + "=" * 60)
if failed == 0:
    print(f"全部通过 ({passed} 项)")
else:
    print(f"失败 {failed} 项，通过 {passed} 项")
    sys.exit(1)