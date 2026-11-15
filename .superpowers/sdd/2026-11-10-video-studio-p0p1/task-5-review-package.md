diff --git a/backend/capabilities/video/services/studio_preview.py b/backend/capabilities/video/services/studio_preview.py
new file mode 100644
index 0000000..8d920d1
--- /dev/null
+++ b/backend/capabilities/video/services/studio_preview.py
@@ -0,0 +1,60 @@
+"""场景本地预览静态服务（Studio Preview Server）。
+
+职责：把 workspace 的 scene 目录以静态文件方式托管在 127.0.0.1 上，
+供 P0 spike 起预览给用户实时看场景画面。
+  - 服务只做静态托管；场景页实时预览的播放循环（rAF 调 seekTo）属场景代码职责
+  - 仅标准库 http.server/functools/threading，bind 127.0.0.1 不暴露局域网
+  - 无风格常量（颜色/字体/动效参数不在此模块）
+"""
+
+from __future__ import annotations
+
+import functools
+import threading
+from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
+from pathlib import Path
+
+
+class StudioPreviewServer:
+    """把 ws["scene"] 目录静态托管在 http://127.0.0.1:<port>/ 上。
+
+    port=0 时由系统分配实际端口，start() 从 server.server_address 读回。
+    """
+
+    def __init__(self, ws: dict, port: int = 0):
+        if "scene" not in ws:
+            raise ValueError("workspace 缺少 scene 键，无法启动预览服务")
+        scene = Path(ws["scene"])
+        if not scene.is_dir():
+            raise ValueError(f"场景目录不存在：{scene}")
+        # Windows 中文路径：给 http.server 的 directory 传 str 绝对路径
+        self._directory = str(scene.resolve())
+        self._port = port
+        self._httpd = None
+        self._thread = None
+        self._base = None
+
+    def start(self) -> str:
+        """启动静态服务，返回 http://127.0.0.1:<port>/。"""
+        handler = functools.partial(SimpleHTTPRequestHandler, directory=self._directory)
+        self._httpd = ThreadingHTTPServer(("127.0.0.1", self._port), handler)
+        self._httpd.daemon_threads = True
+        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
+        self._thread.start()
+        self._base = f"http://127.0.0.1:{self._httpd.server_address[1]}"
+        return self._base + "/"
+
+    def stop(self) -> None:
+        """停服务（幂等：未 start 或已 stop 时调用不炸）。"""
+        if self._httpd is None:
+            return
+        self._httpd.shutdown()
+        self._httpd.server_close()
+        self._thread.join(timeout=5)
+        self._httpd = None
+        self._thread = None
+        self._base = None
+
+    def url_for(self, rel: str) -> str:
+        """把 scene 目录下的相对路径转成预览 URL。"""
+        return f"{self._base}/{rel.lstrip('/')}"
diff --git a/backend/tests/test_studio_preview.py b/backend/tests/test_studio_preview.py
new file mode 100644
index 0000000..9939090
--- /dev/null
+++ b/backend/tests/test_studio_preview.py
@@ -0,0 +1,10 @@
+def test_preview_server_serves_scene(tmp_path):
+    from capabilities.video.services.studio_preview import StudioPreviewServer
+    (tmp_path / "index.html").write_text("<html><body>hi</body></html>", encoding="utf-8")
+    ws = {"scene": tmp_path}
+    srv = StudioPreviewServer(ws); url = srv.start()
+    import urllib.request
+    body = urllib.request.urlopen(url).read().decode("utf-8")
+    assert "hi" in body
+    assert srv.url_for("assets/x.png").startswith(url.rstrip("/"))
+    srv.stop()
