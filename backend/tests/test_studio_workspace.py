def test_workspace_and_timeline_contract(tmp_path):
    from capabilities.video.services.studio_workspace import (
        create_workspace, validate_timeline, save_timeline, load_timeline)
    ws = create_workspace("ws_test1")
    assert ws["scene"].is_dir() and ws["review"].is_dir()
    ok = {"fps": 30, "duration": 5.0, "canvas": {"w": 1080, "h": 1920},
          "speech": [{"start": 0.0, "end": 1.0, "text": "你好", "words": []}],
          "beats": [{"id": "b1", "type": "claim", "t_start": 0.0, "t_end": 5.0,
                     "title": "开场", "visual_task": "建立悬念", "assets": [],
                     "triggers": [{"text": "你好", "t": 0.2}]}]}
    assert validate_timeline(ok) == []
    bad = {**ok, "duration": -1, "beats": [{"id": "b1", "t_start": 5.0, "t_end": 4.0, "title": "", "visual_task": ""}]}
    errs = validate_timeline(bad)
    assert any("duration" in e for e in errs) and any("t_end" in e for e in errs)
    p = save_timeline(ws, ok); assert p.is_file()
    assert load_timeline(ws)["duration"] == 5.0


def test_create_workspace_dirs_and_idempotent():
    from capabilities.video.services.studio_workspace import create_workspace
    ws = create_workspace("ws_test1")
    assert set(ws.keys()) == {"inputs", "analysis", "scene", "assets", "frames",
                              "review", "out", ".cache"}
    for name, p in ws.items():
        if name == "frames":
            # .cache/render 渲染帧目录惰性创建：渲染器自建，run 结束清理
            assert not p.exists(), name
        else:
            assert p.is_dir(), name
    ws2 = create_workspace("ws_test1")  # 幂等：目录已存在不报错
    assert ws2 == ws


def test_validate_missing_required_keys():
    from capabilities.video.services.studio_workspace import validate_timeline
    errs = validate_timeline({})
    assert isinstance(errs, list) and errs
    for field in ("fps", "duration", "canvas", "speech", "beats"):
        assert any(field in e for e in errs), (field, errs)


def test_validate_type_errors_and_bool():
    from capabilities.video.services.studio_workspace import validate_timeline
    tl = {"fps": True, "duration": "5", "canvas": {"w": "1080", "h": -1920},
          "speech": "no", "beats": {"b1": {}}}
    errs = validate_timeline(tl)
    for field in ("fps", "duration", "canvas.w", "canvas.h", "speech", "beats"):
        assert any(field in e for e in errs), (field, errs)


def test_validate_rejects_nonfinite_numbers():
    # json.loads 默认放行 NaN/Infinity；NaN>0 为 False 会溜过 >0 检查，必须显式拒绝
    from capabilities.video.services.studio_workspace import validate_timeline
    tl = {"fps": float("nan"), "duration": float("inf"),
          "canvas": {"w": 1080, "h": 1920}, "speech": [], "beats": []}
    errs = validate_timeline(tl)
    assert any("fps" in e for e in errs), errs
    assert any("duration" in e for e in errs), errs


def test_validate_speech_item_errors():
    from capabilities.video.services.studio_workspace import validate_timeline
    tl = {"fps": 30, "duration": 5.0, "canvas": {"w": 1080, "h": 1920},
          "speech": [{"start": 1.0, "end": 1.0}, 42],
          "beats": [{"id": "b1", "t_start": 0.0, "t_end": 5.0, "title": "t", "visual_task": "v"}]}
    errs = validate_timeline(tl)
    assert any("end" in e and "start" in e for e in errs), errs
    assert any("text" in e for e in errs), errs
    assert any("speech[1]" in e for e in errs), errs


def test_validate_beats_item_and_order_errors():
    from capabilities.video.services.studio_workspace import validate_timeline
    tl = {"fps": 30, "duration": 5.0, "canvas": {"w": 1080, "h": 1920},
          "speech": [], "beats": [
              {"id": "b1", "t_start": 0.0, "t_end": 3.0, "title": "a", "visual_task": "v"},
              {"id": "b2", "t_start": 2.0, "t_end": 4.0, "title": "b", "visual_task": "v"},
              {"id": 3, "t_start": "x", "t_end": None, "visual_task": "v"},
          ]}
    errs = validate_timeline(tl)
    assert any("t_start" in e and "重叠" in e for e in errs), errs  # b2 与 b1 重叠
    assert any("beats[2].id" in e for e in errs), errs
    assert any("beats[2].t_start" in e for e in errs), errs
    assert any("beats[2].t_end" in e for e in errs), errs
    assert any("beats[2].title" in e for e in errs), errs


def test_validate_trigger_rules():
    from capabilities.video.services.studio_workspace import validate_timeline
    beat_ok = {"id": "b1", "t_start": 0.0, "t_end": 5.0, "title": "t", "visual_task": "v"}
    tl = {"fps": 30, "duration": 5.0, "canvas": {"w": 1080, "h": 1920},
          "speech": [], "beats": [
              {**beat_ok, "triggers": [{"t": 5.1}, {"text": "无t"}, "x"]},
          ],
          "triggers": [{"t": 0.2}]}  # 挂错位置：顶层 triggers
    errs = validate_timeline(tl)
    assert any("triggers[0].t" in e and "5.1" in e for e in errs), errs      # t 超出 beat 区间
    assert any("triggers[1].t" in e and "缺失" in e for e in errs), errs      # 缺 t
    assert any("triggers[2]" in e for e in errs), errs                        # 非对象
    assert any("挂错" in e or "beats[" in e for e in errs), errs              # 顶层 triggers 报挂错位置


def test_boundary_cases():
    # 边界语义钉住：前 beat t_end == 后 beat t_start 不判重叠（开区间语义）；
    # trigger t 恰在所属 beat 的 t_start/t_end 边界均合法（闭区间语义）
    from capabilities.video.services.studio_workspace import validate_timeline
    tl = {"fps": 30, "duration": 5.0, "canvas": {"w": 1080, "h": 1920},
          "speech": [], "beats": [
              {"id": "b1", "t_start": 0.0, "t_end": 2.5, "title": "a", "visual_task": "v",
               "triggers": [{"text": "甲", "t": 0.0}]},          # t == b1.t_start
              {"id": "b2", "t_start": 2.5, "t_end": 5.0, "title": "b", "visual_task": "v",
               "triggers": [{"text": "乙", "t": 2.5},            # t == b2.t_start（相邻边界点）
                            {"text": "丙", "t": 5.0}]},          # t == b2.t_end
          ]}
    assert validate_timeline(tl) == []


def test_validate_never_raises_on_garbage():
    from capabilities.video.services.studio_workspace import validate_timeline
    for garbage in (None, [], "tl", 42, {"fps": None, "canvas": []},
                    {"beats": [{"triggers": 7, "t_start": True}]},
                    {"speech": [None], "beats": [None, {"triggers": [{"t": {}}]}]}):
        errs = validate_timeline(garbage)
        assert isinstance(errs, list) and all(isinstance(e, str) for e in errs), garbage
