562d9fb feat(studio): 工作区脚手架与 timeline.json 校验契约
diff --git a/backend/capabilities/video/services/studio_workspace.py b/backend/capabilities/video/services/studio_workspace.py
new file mode 100644
index 0000000..6de4770
--- /dev/null
+++ b/backend/capabilities/video/services/studio_workspace.py
@@ -0,0 +1,187 @@
+"""Studio 工作区脚手架与 timeline.json 校验契约。
+
+职责（纯结构/时间合法性，无风格常量——颜色/字体/动效参数不在此模块）：
+  - create_workspace：outputs/studio/<film_id>/ 下 7 个子目录（幂等）
+  - validate_timeline：防御式校验，任何输入都不抛异常，返回中文错误列表
+    （错误信息内嵌字段路径，供创作 agent 自读自修）
+  - save_timeline / load_timeline：timeline.json 落 ws["analysis"]，utf-8 + ensure_ascii=False
+"""
+
+from __future__ import annotations
+
+import json
+import math
+from pathlib import Path
+
+_STUDIO_ROOT = Path(__file__).resolve().parent.parent.parent.parent / "outputs" / "studio"
+
+_SUBDIRS = ("inputs", "analysis", "scene", "assets", "frames", "review", "out")
+
+
+def create_workspace(film_id: str) -> dict[str, Path]:
+    """建 outputs/studio/<film_id>/{inputs,analysis,scene,assets,frames,review,out}。
+
+    幂等（exist_ok=True），返回各路径 dict（键 = 目录名）。
+    """
+    root = _STUDIO_ROOT / film_id
+    ws = {name: root / name for name in _SUBDIRS}
+    for p in ws.values():
+        p.mkdir(parents=True, exist_ok=True)
+    return ws
+
+
+def _is_num(v: object) -> bool:
+    """数字且非 bool、非 NaN/Inf（bool 是 int 子类；json.loads 默认放行 NaN/Infinity，
+    NaN>0 恒 False 会溜过 >0 检查。int 不走 isfinite——超大 int 转 float 会 OverflowError）。"""
+    if isinstance(v, bool) or not isinstance(v, (int, float)):
+        return False
+    if isinstance(v, float):
+        return math.isfinite(v)
+    return True
+
+
+def _type_err(field: str, v: object) -> str:
+    """数字字段类型错的消息：非有限 float 单独点名，避免「应为数字，实际为 float」误导自修。"""
+    if isinstance(v, float) and not math.isfinite(v):
+        return f"{field} 必须为有限数字（NaN/Inf 不可用）"
+    return f"{field} 应为数字，实际为 {type(v).__name__}"
+
+
+def validate_timeline(tl: dict) -> list[str]:
+    """校验 timeline 结构与时间合法性，返回错误列表（空 = 通过）。
+
+    防御式：tl 可能缺键、类型错、triggers 挂错位置——所有分支返回错误字符串，
+    绝不抛异常。规则：
+      必填 fps>0 / duration>0 / canvas{w,h}
+      speech[] 每项含 start/end/text 且 end>start
+      beats[] 每项含 id/t_start/t_end/title/visual_task 且 t_end>t_start，
+              beats 按 t_start 升序不重叠
+      triggers[].t 落在其所属 beat 的 [t_start,t_end] 内（trigger 挂在 beat 的 triggers 字段下）
+    """
+    if not isinstance(tl, dict):
+        return [f"timeline 应为对象(dict)，实际为 {type(tl).__name__}"]
+    errs: list[str] = []
+
+    if "fps" not in tl:
+        errs.append("缺少 fps")
+    elif not _is_num(tl["fps"]):
+        errs.append(_type_err("fps", tl["fps"]))
+    elif tl["fps"] <= 0:
+        errs.append(f"fps 必须 > 0，实际为 {tl['fps']}")
+
+    if "duration" not in tl:
+        errs.append("缺少 duration")
+    elif not _is_num(tl["duration"]):
+        errs.append(_type_err("duration", tl["duration"]))
+    elif tl["duration"] <= 0:
+        errs.append(f"duration 必须 > 0，实际为 {tl['duration']}")
+
+    if "canvas" not in tl:
+        errs.append("缺少 canvas")
+    elif not isinstance(tl["canvas"], dict):
+        errs.append(f"canvas 应为对象(dict)，实际为 {type(tl['canvas']).__name__}")
+    else:
+        canvas = tl["canvas"]
+        for dim in ("w", "h"):
+            if dim not in canvas:
+                errs.append(f"canvas.{dim} 缺失")
+            elif not _is_num(canvas[dim]):
+                errs.append(_type_err(f"canvas.{dim}", canvas[dim]))
+            elif canvas[dim] <= 0:
+                errs.append(f"canvas.{dim} 必须 > 0，实际为 {canvas[dim]}")
+
+    if "speech" not in tl:
+        errs.append("缺少 speech")
+    elif not isinstance(tl["speech"], list):
+        errs.append(f"speech 应为列表，实际为 {type(tl['speech']).__name__}")
+    else:
+        for i, seg in enumerate(tl["speech"]):
+            tag = f"speech[{i}]"
+            if not isinstance(seg, dict):
+                errs.append(f"{tag} 应为对象(dict)，实际为 {type(seg).__name__}")
+                continue
+            for field in ("start", "end", "text"):
+                if field not in seg:
+                    errs.append(f"{tag}.{field} 缺失")
+            for tfield in ("start", "end"):
+                if tfield in seg and not _is_num(seg[tfield]):
+                    errs.append(_type_err(f"{tag}.{tfield}", seg[tfield]))
+            if _is_num(seg.get("start")) and _is_num(seg.get("end")) and seg["end"] <= seg["start"]:
+                errs.append(f"{tag}.end 必须 > start（实际 end={seg['end']}, start={seg['start']}）")
+            if "text" in seg and not isinstance(seg["text"], str):
+                errs.append(f"{tag}.text 应为字符串，实际为 {type(seg['text']).__name__}")
+
+    if "beats" not in tl:
+        errs.append("缺少 beats")
+    elif not isinstance(tl["beats"], list):
+        errs.append(f"beats 应为列表，实际为 {type(tl['beats']).__name__}")
+    else:
+        prev: dict | None = None
+        for i, beat in enumerate(tl["beats"]):
+            tag = f"beats[{i}]"
+            if not isinstance(beat, dict):
+                errs.append(f"{tag} 应为对象(dict)，实际为 {type(beat).__name__}")
+                prev = None
+                continue
+            for field in ("id", "t_start", "t_end", "title", "visual_task"):
+                if field not in beat:
+                    errs.append(f"{tag}.{field} 缺失")
+            for sfield in ("id", "title", "visual_task"):
+                if sfield in beat and not isinstance(beat[sfield], str):
+                    errs.append(f"{tag}.{sfield} 应为字符串，实际为 {type(beat[sfield]).__name__}")
+            for tfield in ("t_start", "t_end"):
+                if tfield in beat and not _is_num(beat[tfield]):
+                    errs.append(_type_err(f"{tag}.{tfield}", beat[tfield]))
+            if _is_num(beat.get("t_start")) and _is_num(beat.get("t_end")) and beat["t_end"] <= beat["t_start"]:
+                errs.append(f"{tag}.t_end 必须 > t_start（实际 t_end={beat['t_end']}, t_start={beat['t_start']}）")
+            if (prev is not None and _is_num(prev.get("t_end")) and _is_num(beat.get("t_start"))
+                    and beat["t_start"] < prev["t_end"]):
+                errs.append(f"{tag}.t_start 与上一 beat 重叠（须按 t_start 升序且不重叠，上一 beat t_end={prev['t_end']}）")
+            _check_triggers(errs, i, beat)
+            prev = beat if _is_num(beat.get("t_start")) else None
+
+    if "triggers" in tl:
+        errs.append("顶层 triggers 挂错位置：trigger 应挂在各 beat 的 triggers 字段下")
+
+    return errs
+
+
+def _check_triggers(errs: list[str], beat_i: int, beat: dict) -> None:
+    """校验单个 beat 的 triggers（挂在 beat.triggers 下），追加错误到 errs。"""
+    if "triggers" not in beat:
+        return
+    tag = f"beats[{beat_i}].triggers"
+    triggers = beat["triggers"]
+    if not isinstance(triggers, list):
+        errs.append(f"{tag} 应为列表，实际为 {type(triggers).__name__}")
+        return
+    t_start = beat.get("t_start")
+    t_end = beat.get("t_end")
+    span_ok = _is_num(t_start) and _is_num(t_end)
+    for j, trig in enumerate(triggers):
+        ttag = f"{tag}[{j}]"
+        if not isinstance(trig, dict):
+            errs.append(f"{ttag} 应为对象(dict)，实际为 {type(trig).__name__}")
+            continue
+        if "t" not in trig:
+            errs.append(f"{ttag}.t 缺失")
+        elif not _is_num(trig["t"]):
+            errs.append(_type_err(f"{ttag}.t", trig["t"]))
+        elif span_ok and not (t_start <= trig["t"] <= t_end):
+            errs.append(f"{ttag}.t={trig['t']} 超出所属 beat [t_start,t_end]=[{t_start},{t_end}]")
+
+
+def _timeline_path(ws: dict) -> Path:
+    return Path(ws["analysis"]) / "timeline.json"
+
+
+def save_timeline(ws: dict, tl: dict) -> Path:
+    """timeline.json 落 ws["analysis"] 目录（utf-8 + ensure_ascii=False），返回文件路径。"""
+    p = _timeline_path(ws)
+    p.write_text(json.dumps(tl, ensure_ascii=False, indent=2), encoding="utf-8")
+    return p
+
+
+def load_timeline(ws: dict) -> dict:
+    """读回 ws["analysis"]/timeline.json。"""
+    return json.loads(_timeline_path(ws).read_text(encoding="utf-8"))
diff --git a/backend/tests/test_studio_workspace.py b/backend/tests/test_studio_workspace.py
new file mode 100644
index 0000000..39b91b9
--- /dev/null
+++ b/backend/tests/test_studio_workspace.py
@@ -0,0 +1,104 @@
+def test_workspace_and_timeline_contract(tmp_path):
+    from capabilities.video.services.studio_workspace import (
+        create_workspace, validate_timeline, save_timeline, load_timeline)
+    ws = create_workspace("ws_test1")
+    assert ws["scene"].is_dir() and ws["review"].is_dir()
+    ok = {"fps": 30, "duration": 5.0, "canvas": {"w": 1080, "h": 1920},
+          "speech": [{"start": 0.0, "end": 1.0, "text": "你好", "words": []}],
+          "beats": [{"id": "b1", "type": "claim", "t_start": 0.0, "t_end": 5.0,
+                     "title": "开场", "visual_task": "建立悬念", "assets": [],
+                     "triggers": [{"text": "你好", "t": 0.2}]}]}
+    assert validate_timeline(ok) == []
+    bad = {**ok, "duration": -1, "beats": [{"id": "b1", "t_start": 5.0, "t_end": 4.0, "title": "", "visual_task": ""}]}
+    errs = validate_timeline(bad)
+    assert any("duration" in e for e in errs) and any("t_end" in e for e in errs)
+    p = save_timeline(ws, ok); assert p.is_file()
+    assert load_timeline(ws)["duration"] == 5.0
+
+
+def test_create_workspace_dirs_and_idempotent():
+    from capabilities.video.services.studio_workspace import create_workspace
+    ws = create_workspace("ws_test1")
+    assert set(ws.keys()) == {"inputs", "analysis", "scene", "assets", "frames", "review", "out"}
+    for name, p in ws.items():
+        assert p.is_dir(), name
+    ws2 = create_workspace("ws_test1")  # 幂等：目录已存在不报错
+    assert ws2 == ws
+
+
+def test_validate_missing_required_keys():
+    from capabilities.video.services.studio_workspace import validate_timeline
+    errs = validate_timeline({})
+    assert isinstance(errs, list) and errs
+    for field in ("fps", "duration", "canvas", "speech", "beats"):
+        assert any(field in e for e in errs), (field, errs)
+
+
+def test_validate_type_errors_and_bool():
+    from capabilities.video.services.studio_workspace import validate_timeline
+    tl = {"fps": True, "duration": "5", "canvas": {"w": "1080", "h": -1920},
+          "speech": "no", "beats": {"b1": {}}}
+    errs = validate_timeline(tl)
+    for field in ("fps", "duration", "canvas.w", "canvas.h", "speech", "beats"):
+        assert any(field in e for e in errs), (field, errs)
+
+
+def test_validate_rejects_nonfinite_numbers():
+    # json.loads 默认放行 NaN/Infinity；NaN>0 为 False 会溜过 >0 检查，必须显式拒绝
+    from capabilities.video.services.studio_workspace import validate_timeline
+    tl = {"fps": float("nan"), "duration": float("inf"),
+          "canvas": {"w": 1080, "h": 1920}, "speech": [], "beats": []}
+    errs = validate_timeline(tl)
+    assert any("fps" in e for e in errs), errs
+    assert any("duration" in e for e in errs), errs
+
+
+def test_validate_speech_item_errors():
+    from capabilities.video.services.studio_workspace import validate_timeline
+    tl = {"fps": 30, "duration": 5.0, "canvas": {"w": 1080, "h": 1920},
+          "speech": [{"start": 1.0, "end": 1.0}, 42],
+          "beats": [{"id": "b1", "t_start": 0.0, "t_end": 5.0, "title": "t", "visual_task": "v"}]}
+    errs = validate_timeline(tl)
+    assert any("end" in e and "start" in e for e in errs), errs
+    assert any("text" in e for e in errs), errs
+    assert any("speech[1]" in e for e in errs), errs
+
+
+def test_validate_beats_item_and_order_errors():
+    from capabilities.video.services.studio_workspace import validate_timeline
+    tl = {"fps": 30, "duration": 5.0, "canvas": {"w": 1080, "h": 1920},
+          "speech": [], "beats": [
+              {"id": "b1", "t_start": 0.0, "t_end": 3.0, "title": "a", "visual_task": "v"},
+              {"id": "b2", "t_start": 2.0, "t_end": 4.0, "title": "b", "visual_task": "v"},
+              {"id": 3, "t_start": "x", "t_end": None, "visual_task": "v"},
+          ]}
+    errs = validate_timeline(tl)
+    assert any("t_start" in e and "重叠" in e for e in errs), errs  # b2 与 b1 重叠
+    assert any("beats[2].id" in e for e in errs), errs
+    assert any("beats[2].t_start" in e for e in errs), errs
+    assert any("beats[2].t_end" in e for e in errs), errs
+    assert any("beats[2].title" in e for e in errs), errs
+
+
+def test_validate_trigger_rules():
+    from capabilities.video.services.studio_workspace import validate_timeline
+    beat_ok = {"id": "b1", "t_start": 0.0, "t_end": 5.0, "title": "t", "visual_task": "v"}
+    tl = {"fps": 30, "duration": 5.0, "canvas": {"w": 1080, "h": 1920},
+          "speech": [], "beats": [
+              {**beat_ok, "triggers": [{"t": 5.1}, {"text": "无t"}, "x"]},
+          ],
+          "triggers": [{"t": 0.2}]}  # 挂错位置：顶层 triggers
+    errs = validate_timeline(tl)
+    assert any("triggers[0].t" in e and "5.1" in e for e in errs), errs      # t 超出 beat 区间
+    assert any("triggers[1].t" in e and "缺失" in e for e in errs), errs      # 缺 t
+    assert any("triggers[2]" in e for e in errs), errs                        # 非对象
+    assert any("挂错" in e or "beats[" in e for e in errs), errs              # 顶层 triggers 报挂错位置
+
+
+def test_validate_never_raises_on_garbage():
+    from capabilities.video.services.studio_workspace import validate_timeline
+    for garbage in (None, [], "tl", 42, {"fps": None, "canvas": []},
+                    {"beats": [{"triggers": 7, "t_start": True}]},
+                    {"speech": [None], "beats": [None, {"triggers": [{"t": {}}]}]}):
+        errs = validate_timeline(garbage)
+        assert isinstance(errs, list) and all(isinstance(e, str) for e in errs), garbage
