"""五期-D 专项验证：media-service（onnx/ffmpeg 隔离）。"""

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
print("五期-D 验证：media-service（onnx/ffmpeg 隔离）")
print("=" * 60)

# ─── 1. capability_service 注册 ───
print("\n1. capability-service 工具注册")
from capability_service.main import CAPABILITY_TOOLS

check("tts_piper 已注册", "tts_piper" in CAPABILITY_TOOLS)
check("video_compose 已注册", "video_compose" in CAPABILITY_TOOLS)
check("工具总数 = 6", len(CAPABILITY_TOOLS) == 6, f"actual={len(CAPABILITY_TOOLS)}")

# ─── 2. handler 签名 ───
print("\n2. handler 签名")
import inspect

tts_handler = CAPABILITY_TOOLS["tts_piper"]["handler"]
vc_handler = CAPABILITY_TOOLS["video_compose"]["handler"]
check("tts_piper handler 是协程函数", inspect.iscoroutinefunction(tts_handler))
check("video_compose handler 是协程函数", inspect.iscoroutinefunction(vc_handler))

tts_sig = inspect.signature(tts_handler)
vc_sig = inspect.signature(vc_handler)
check("tts_piper 签名含 text", "text" in tts_sig.parameters)
check("tts_piper 签名含 output_path", "output_path" in tts_sig.parameters)
check("video_compose 签名含 storyboard_json", "storyboard_json" in vc_sig.parameters)

# ─── 3. is_async 标记 ───
print("\n3. is_async 标记")
check("tts_piper is_async=True", CAPABILITY_TOOLS["tts_piper"].get("is_async") is True)
check("video_compose is_async=True", CAPABILITY_TOOLS["video_compose"].get("is_async") is True)

# ─── 4. media_capability 访问层 ───
print("\n4. media_capability 访问层")
from app.services import media_capability

check("tts_piper 函数存在", hasattr(media_capability, "tts_piper"))
check("video_compose 函数存在", hasattr(media_capability, "video_compose"))
check("_service_url 函数存在", hasattr(media_capability, "_service_url"))

# ─── 5. _service_url 走 capability_loader ───
print("\n5. _service_url 走 capability_loader")
from app.core.capability_loader import get_capability_service_url

check(
    "_service_url() == get_capability_service_url('media')",
    media_capability._service_url() == get_capability_service_url("media"),
)
check("默认 inprocess → 空串", media_capability._service_url() == "")

# ─── 6. Settings.media_service_url ───
print("\n6. Settings.media_service_url")
from app.config import get_settings

s = get_settings()
check("字段存在", hasattr(s, "media_service_url"))
check("默认为空", s.media_service_url == "")

# ─── 7. capability.toml [media] runtime ───
print("\n7. capability.toml [media] runtime 字段")
from app.core.capability_loader import get_capability_runtime

check("media runtime 存在", get_capability_runtime("media") is not None)
check("media runtime = inprocess", get_capability_runtime("media") == "inprocess")

# ─── 8. _SERVICE_URL_FIELDS 映射 ───
print("\n8. _SERVICE_URL_FIELDS 映射")
from app.core.capability_loader import _SERVICE_URL_FIELDS

check("media → media_service_url", _SERVICE_URL_FIELDS.get("media") == "media_service_url")

# ─── 9. 进程内模式：tts_piper 走 synthesize_piper ───
print("\n9. 进程内模式 import 链")
import ast
import textwrap

src = textwrap.dedent(inspect.getsource(media_capability.tts_piper))
tree = ast.parse(src)
local_imports = [
    n.module
    for n in ast.walk(tree)
    if isinstance(n, ast.ImportFrom) and n.module and "piper" in n.module
]
check("tts_piper 进程内 import synthesize_piper", len(local_imports) > 0, f"found={local_imports}")

src2 = textwrap.dedent(inspect.getsource(media_capability.video_compose))
tree2 = ast.parse(src2)
vc_imports = [
    n.module
    for n in ast.walk(tree2)
    if isinstance(n, ast.ImportFrom) and n.module and "video_compose" in n.module
]
check("video_compose 进程内 import compose_video", len(vc_imports) > 0, f"found={vc_imports}")

# ─── 10. 零泄漏：media_capability 不 import get_settings ───
print("\n10. 零泄漏检查")
mc_src = inspect.getsource(media_capability)
check("media_capability 不含 get_settings", "get_settings" not in mc_src)

# ─── 结果 ───
print("\n" + "=" * 60)
if failed == 0:
    print(f"全部通过 ({passed} 项)")
else:
    print(f"失败 {failed} 项，通过 {passed} 项")
    sys.exit(1)
