diff --git a/backend/tests/test_studio_workspace.py b/backend/tests/test_studio_workspace.py
index 39b91b9..994f22f 100644
--- a/backend/tests/test_studio_workspace.py
+++ b/backend/tests/test_studio_workspace.py
@@ -88,17 +88,32 @@ def test_validate_trigger_rules():
               {**beat_ok, "triggers": [{"t": 5.1}, {"text": "无t"}, "x"]},
           ],
           "triggers": [{"t": 0.2}]}  # 挂错位置：顶层 triggers
     errs = validate_timeline(tl)
     assert any("triggers[0].t" in e and "5.1" in e for e in errs), errs      # t 超出 beat 区间
     assert any("triggers[1].t" in e and "缺失" in e for e in errs), errs      # 缺 t
     assert any("triggers[2]" in e for e in errs), errs                        # 非对象
     assert any("挂错" in e or "beats[" in e for e in errs), errs              # 顶层 triggers 报挂错位置
 
 
+def test_boundary_cases():
+    # 边界语义钉住：前 beat t_end == 后 beat t_start 不判重叠（开区间语义）；
+    # trigger t 恰在所属 beat 的 t_start/t_end 边界均合法（闭区间语义）
+    from capabilities.video.services.studio_workspace import validate_timeline
+    tl = {"fps": 30, "duration": 5.0, "canvas": {"w": 1080, "h": 1920},
+          "speech": [], "beats": [
+              {"id": "b1", "t_start": 0.0, "t_end": 2.5, "title": "a", "visual_task": "v",
+               "triggers": [{"text": "甲", "t": 0.0}]},          # t == b1.t_start
+              {"id": "b2", "t_start": 2.5, "t_end": 5.0, "title": "b", "visual_task": "v",
+               "triggers": [{"text": "乙", "t": 2.5},            # t == b2.t_start（相邻边界点）
+                            {"text": "丙", "t": 5.0}]},          # t == b2.t_end
+          ]}
+    assert validate_timeline(tl) == []
+
+
 def test_validate_never_raises_on_garbage():
     from capabilities.video.services.studio_workspace import validate_timeline
     for garbage in (None, [], "tl", 42, {"fps": None, "canvas": []},
                     {"beats": [{"triggers": 7, "t_start": True}]},
                     {"speech": [None], "beats": [None, {"triggers": [{"t": {}}]}]}):
         errs = validate_timeline(garbage)
         assert isinstance(errs, list) and all(isinstance(e, str) for e in errs), garbage
