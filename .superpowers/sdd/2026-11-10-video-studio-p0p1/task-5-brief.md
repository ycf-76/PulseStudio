# Task 5: 本地预览服务（studio_preview）

**Files:**
- Create: `backend/capabilities/video/services/studio_preview.py`
- Test: `backend/tests/test_studio_preview.py`

**Interfaces:**
- Consumes: studio_workspace 的 ws dict（键 inputs/analysis/scene/assets/frames/review/out）
- Produces:
  - `class StudioPreviewServer` — `__init__(ws: dict, port: int = 0)`；`def start(self) -> str`（返回 `http://127.0.0.1:<port>/`，port=0 时系统分配实际端口并从 server_address 读回）；`def stop(self) -> None`；`def url_for(self, rel: str) -> str`
  - 用 `http.server.ThreadingHTTPServer` + `functools.partial(SimpleHTTPRequestHandler, directory=str(ws["scene"]))`，daemon 线程跑 serve_forever；`/` 默认 index.html
  - 场景页实时预览的播放循环属场景代码职责（rAF 调 seekTo），服务只做静态托管

- [ ] **Step 1: Write the failing test**

```python
def test_preview_server_serves_scene(tmp_path):
    from capabilities.video.services.studio_preview import StudioPreviewServer
    (tmp_path / "index.html").write_text("<html><body>hi</body></html>", encoding="utf-8")
    ws = {"scene": tmp_path}
    srv = StudioPreviewServer(ws); url = srv.start()
    import urllib.request
    body = urllib.request.urlopen(url).read().decode("utf-8")
    assert "hi" in body
    assert srv.url_for("assets/x.png").startswith(url.rstrip("/"))
    srv.stop()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_studio_preview.py -v`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: Implement** StudioPreviewServer

start() 后从 `server.server_address` 读实际端口；stop 用 shutdown()+server_close()（幂等：未 start 时调用不炸）。目录不存在/ws 缺 scene 键 → ValueError 带中文错误信息。

- [ ] **Step 4: Run test to verify it passes**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_studio_preview.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/capabilities/video/services/studio_preview.py backend/tests/test_studio_preview.py
git commit -m "feat(studio): 场景本地预览静态服务"
```
