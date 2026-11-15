diff --git a/backend/capabilities/video/services/studio_review.py b/backend/capabilities/video/services/studio_review.py
index e8ae7d2..cd28378 100644
--- a/backend/capabilities/video/services/studio_review.py
+++ b/backend/capabilities/video/services/studio_review.py
@@ -215,21 +215,21 @@ def completeness_check(frames_dir: Path, expected: int) -> list[str]:
             errs.append(f"0 字节帧: {p.name}（编号 {i}）")
     return errs
 
 
 def review_workspace(ws: dict, timeline: dict, video_path: str) -> dict:
     """质检汇总（防御式，绝不抛异常）：{"contact_sheets", "loudness", "errors"}。
 
     errors 为中文错误列表（嵌字段路径/实测值，供创作 agent 自读自修）：
       - 每 beat 的 t_start/t_end 抽帧 contact sheet 落 ws["review"]/beat_<id>_<t>.png
       - 响度 loudness_report(video_path)
-      - 帧完整性 completeness_check(frames, expected=round(duration*fps))
+      - 帧完整性 completeness_check(frames, expected=math.ceil(duration*fps))
       - 时长偏差：probe_duration vs timeline.duration，> 1/fps + 0.05 报错
       - 静音轨回归钉：无声轨或音轨时长≈0 报错（Task 2 补欠账）
     """
     errors: list[str] = []
     result: dict = {"contact_sheets": [], "loudness": {}, "errors": errors}
     try:
         _review_inner(ws, timeline, video_path, result, errors)
     except Exception as exc:  # 自查通道崩溃 = 自查失效，一切异常都进 errors
         logger.exception("review_workspace unexpected failure")
         errors.append(f"质检流程异常: {exc}")
@@ -278,23 +278,25 @@ def _review_inner(
                 if not (_is_num(ts) and _is_num(te)):
                     errors.append(f"beats[{i}].t_start/t_end 应为数字，跳过抽帧")
                     continue
                 safe_id = re.sub(r"[^0-9A-Za-z_-]+", "_", bid)
                 out = review_dir / f"beat_{safe_id}_{float(ts):.2f}.png"
                 result["contact_sheets"].append(
                     str(contact_sheet(frames_dir, [ts, te], float(fps), out)))
             except Exception as exc:
                 errors.append(f"beats[{i}] contact sheet 失败: {exc}")
 
-    # 2) 帧完整性（expected = round(duration*fps)）
+    # 2) 帧完整性（expected = ceil(duration*fps)，与渲染侧 studio_render 的
+    #    n_frames 公式一致：ceil 保证覆盖到最后一帧；round 在非整除时长
+    #    （如 7.35s×30fps）会比渲染少 1 帧，误报「多余帧」打断自查闭环）
     if fps_ok and _is_num(duration) and duration > 0:
-        expected = round(duration * fps)
+        expected = math.ceil(duration * fps)
         if expected <= 0:
             errors.append(f"期望帧数 ≤ 0（duration={duration}×fps={fps}），帧完整性检查跳过")
         else:
             errors.extend(completeness_check(frames_dir, expected))
     else:
         errors.append(
             f"timeline duration/fps 无效（duration={duration!r}, fps={fps!r}），帧完整性检查跳过")
 
     # 3) 响度
     try:
diff --git a/backend/tests/test_studio_review.py b/backend/tests/test_studio_review.py
index ac873f9..01d19d9 100644
--- a/backend/tests/test_studio_review.py
+++ b/backend/tests/test_studio_review.py
@@ -77,10 +77,34 @@ def test_review_workspace_duration_deviation(tmp_path):
     ws = {"frames": fd, "review": tmp_path / "review"}
     res = review_workspace(ws, _TL, str(src))  # _TL.duration=1.0，实测≈2.0s
     assert any("时长偏差" in e for e in res["errors"]), res["errors"]
 
 
 def test_review_workspace_defensive_garbage():
     """防御式：ws/timeline/video 全是垃圾也不抛异常，错误进 errors。"""
     from capabilities.video.services.studio_review import review_workspace
     res = review_workspace({}, {}, "nonexistent.mp4")
     assert isinstance(res["errors"], list) and res["errors"]
+
+
+def test_non_integer_duration_frames(tmp_path):
+    """非整除时长（7.35s×30fps）帧数契约：ceil 为权威公式。
+
+    渲染侧 studio_render 的 n_frames = ceil(duration*fps) = 221（覆盖到
+    最后一帧）；review 的 expected 曾用 round = 220（银行家舍入），会把
+    渲染正常的 frame_00220 误报「多余帧」，打断 P2 自查闭环。此测试钉住
+    统一后的 ceil 契约（ceil 与 round 在该时长上确有分歧）。
+    """
+    import math
+    from capabilities.video.services.studio_review import completeness_check
+    assert math.ceil(7.35 * 30) == 221 and round(7.35 * 30) == 220
+    fd = _make_frames(tmp_path, 221)
+    # 契约正向：ceil 帧数齐全 → 无错误（整除时长测试从未覆盖的 seam）
+    assert completeness_check(fd, 221) == []
+    # 钉住 ceil 权威：少一帧（只剩 round 的 220 帧）按缺号报错，不放行
+    (fd / "frame_000220.png").unlink()
+    errs = completeness_check(fd, 221)
+    assert any("缺帧" in e and "00220" in e for e in errs), errs
+    # 反向证据：旧 round 契约（expected=220）会把渲染正常的第 221 帧报成多余
+    fd2 = _make_frames(tmp_path / "full", 221)
+    errs = completeness_check(fd2, 220)
+    assert any("多余帧" in e and "00220" in e for e in errs), errs
