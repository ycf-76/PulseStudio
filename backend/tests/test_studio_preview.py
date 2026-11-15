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
