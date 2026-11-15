# Task 3: 工作区与 timeline 契约（studio_workspace）

**Files:**
- Create: `backend/capabilities/video/services/studio_workspace.py`
- Test: `backend/tests/test_studio_workspace.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `def create_workspace(film_id: str) -> dict[str, Path]` — 建 `outputs/studio/<film_id>/{inputs,analysis,scene,assets,frames,review,out}`，返回各路径 dict（键 = 目录名）
  - `def validate_timeline(tl: dict) -> list[str]` — 返回错误列表（空=通过）：必填 `fps>0/duration>0/canvas{w,h}`；`speech[]` 每项含 start/end/text 且 end>start；`beats[]` 每项含 `id/t_start/t_end/title/visual_task` 且 `t_end>t_start`、beats 按 t_start 升序不重叠；`triggers[].t` 落在其所属 beat 的 [t_start,t_end] 内（trigger 挂在 beat 的 triggers 字段下）
  - `def save_timeline(ws: dict, tl: dict) -> Path` / `def load_timeline(ws: dict) -> dict` — timeline.json 落 ws["analysis"] 目录，save 用 utf-8 ensure_ascii=False

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_studio_workspace.py -v`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: Implement** 三个函数 + 纯校验

错误信息带字段名（duration/t_end/…）便于创作 agent 自读自修。workspace 根为仓库 backend/outputs/studio（对齐既有 `_OUTPUT_DIR = Path(__file__)…parents[3]/"outputs"` 惯例）。create_workspace 幂等（目录已存在不报错，exist_ok=True）。

- [ ] **Step 4: Run test to verify it passes**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_studio_workspace.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/capabilities/video/services/studio_workspace.py backend/tests/test_studio_workspace.py
git commit -m "feat(studio): 工作区脚手架与 timeline.json 校验契约"
```
