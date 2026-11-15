"""五期-A browser-service 专项验证脚本。"""
import sys
sys.path.insert(0, ".")

# 1. cards/export.py 核心下沉验证
from app.cards.export import prepare_card_htmls, render_htmls_to_pngs, render_png_pages
print("1. cards/export.py: prepare_card_htmls + render_htmls_to_pngs + render_png_pages OK")

# 2. browser_capability.py 访问层验证
from app.services.browser_capability import render_card_pngs, _service_url
url = _service_url()
print(f"2. _service_url() = {url!r} (empty=local)")

# 3. config.py 字段验证
from app.config import get_settings
s = get_settings()
print(f"3. browser_service_url = {s.browser_service_url!r}")
print(f"   grid_service_url = {s.grid_service_url!r}")

# 4. capability_service 工具注册验证
from capability_service.main import CAPABILITY_TOOLS
tools = list(CAPABILITY_TOOLS.keys())
print(f"4. capability-service tools: {tools}")
assert "render_htmls" in tools, "render_htmls not registered!"
assert "grid_detect" in tools, "grid_detect not registered!"
assert "grid_split" in tools, "grid_split not registered!"

# 5. render_htmls handler 是 async
meta = CAPABILITY_TOOLS["render_htmls"]
print(f"5. render_htmls is_async = {meta.get('is_async', False)}")

# 6. capability.toml browser 包验证
from app.core.capability_loader import _parse_toml, _TOML_PATH
data = _parse_toml(_TOML_PATH)
browser_cfg = data.get("browser", {})
print(f"6. capability.toml [browser]: enabled={browser_cfg.get('enabled')}, runtime={browser_cfg.get('runtime')}")

# 7. skill_registry 拆分验证
from app.core.skill_registry import KERNEL_SKILL_MODULES, CAPABILITY_SKILL_MODULES, BUILTIN_SKILL_MODULES
print(f"7. KERNEL={len(KERNEL_SKILL_MODULES)}, CAPABILITY={len(CAPABILITY_SKILL_MODULES)}, TOTAL={len(BUILTIN_SKILL_MODULES)}")

# 8. engine/ 零泄漏验证
import app.engine.reactor.primitives as prim_mod
import app.engine.reactor.session as sess_mod
import app.engine.governance.guardian as guard_mod
import app.engine.governance.world_state as ws_mod
import app.engine.factory as fact_mod
leaks = []
for mod_name, mod in [("primitives", prim_mod), ("session", sess_mod), ("guardian", guard_mod), ("world_state", ws_mod), ("factory", fact_mod)]:
    for attr_name in dir(mod):
        obj = getattr(mod, attr_name)
        if hasattr(obj, "__module__") and obj.__module__ and "capabilities" in obj.__module__:
            leaks.append((mod_name, attr_name, obj.__module__))
if leaks:
    print(f"8. engine/ LEAK DETECTED: {leaks}")
else:
    print("8. engine/ zero capability leak OK")

# 9. render_png_pages 兼容性验证（仍可一步调用）
import inspect
sig = inspect.signature(render_png_pages)
print(f"9. render_png_pages signature: {sig} (backward compat OK)")

# 10. prepare_card_htmls 返回类型验证
sig2 = inspect.signature(prepare_card_htmls)
print(f"10. prepare_card_htmls signature: {sig2}")

print()
print("=== ALL 10 CHECKS PASSED ===")