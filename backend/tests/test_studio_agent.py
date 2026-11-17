"""studio_agent 轻量工具循环测试（Video Studio P2 Task 3）。

FakeLLM 按脚本回放动作序列（不调真 LLM）；render/review 默认在
studio_agent 模块命名空间内 monkeypatch，仅场景 a 走真实极小场景端到端
（真实 Playwright 渲染 + 真实质检，做法同 test_studio_render.py）。
"""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

from capabilities.video.adapters.whisper_transcribe import TranscribeSegment
from capabilities.video.services import studio_agent as sa
from capabilities.video.services import studio_workspace as sw
from capabilities.video.services.studio_workspace import create_workspace

_TL = {
    "fps": 8,
    "duration": 1.0,
    "canvas": {"w": 320, "h": 568},
    "speech": [{"text": "你好世界", "start": 0.0, "end": 1.0}],
    "beats": [
        {"id": "b1", "t_start": 0.0, "t_end": 1.0, "title": "开场", "visual_task": "方块右移"}
    ],
}

_SCENE_HTML = (
    '<img src="../.cache/render/frame_000000.png" style="position:absolute;left:0;top:0">'
    '<div id="p" style="position:absolute;left:0;top:0;width:10px;height:10px"></div>'
    '<script>window.seekTo=(t)=>{document.getElementById("p").style.left=(t*100)+"px";};'
    "window.seekTo(0);</script>"
)

_RULES_TEXT = "打法（测试夹具）：口播两拍。第一拍开场点题，第二拍收尾。\n自查纪律：render → review，errors 为空才 finish。"


class FakeLLM:
    """按脚本回放：每次 chat 弹出一条预置回复；记录调用供回喂断言。"""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    async def chat(self, messages, response_format=None):
        self.calls.append(
            {
                "messages": [dict(m) for m in messages],
                "response_format": response_format,
            }
        )
        assert self.script, "FakeLLM 脚本耗尽：循环未按预期终结"
        return self.script.pop(0)


def _make_ws(monkeypatch, tmp_path, film):
    monkeypatch.setattr(sw, "_STUDIO_ROOT", tmp_path)
    ws = create_workspace(film)
    rules = Path(ws["inputs"]) / "rules"
    rules.mkdir(parents=True, exist_ok=True)
    (rules / "method.md").write_text(_RULES_TEXT, encoding="utf-8")
    return ws


def _make_video(tmp_path, with_audio=True, duration=1.0):
    from capabilities.video.adapters.html_to_video import resolve_ffmpeg

    src = tmp_path / "src.mp4"
    cmd = [
        resolve_ffmpeg(),
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=duration={duration}:size=320x568:rate=8",
    ]
    if with_audio:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-c:a", "aac"]
    cmd += ["-t", str(duration), "-pix_fmt", "yuv420p", str(src)]
    subprocess.run(cmd, check=True, capture_output=True)
    return src


def _action(action, **args):
    return json.dumps({"action": action, "args": args}, ensure_ascii=False)


def _feedback_of(llm, turn):
    """第 turn 轮（0 起）动作的回喂 = 第 turn+1 次 chat 的最后一条 user 消息。

    回喂 = JSON 行 + 可选附加系统提示（如重复调用提示），只解析 JSON 行。
    """
    content = llm.calls[turn + 1]["messages"][-1]["content"]
    return json.loads(content.splitlines()[0])


def _recorder():
    events = []

    def emit(phase, payload):
        events.append({"phase": phase, **(payload or {})})

    return emit, events


def _phases(events):
    return [e["phase"] for e in events]


# ── 场景 a：正常路径（真实渲染 + 真实质检） ──────────────────────────────


async def test_normal_path_delivers_final_video(monkeypatch, tmp_path):
    ws = _make_ws(monkeypatch, tmp_path, "agent_a")
    src = _make_video(tmp_path, with_audio=True)

    transcribe_calls = {"n": 0}

    def fake_transcribe(audio_path, model_size="small", language=None):
        transcribe_calls["n"] += 1
        return [TranscribeSegment(text="你好世界", start=0.0, end=1.0, words=[])]

    monkeypatch.setattr(sa, "transcribe", fake_transcribe)
    emit, events = _recorder()
    llm = FakeLLM(
        [
            _action("list_rules"),
            _action("read_rules_file", path="method.md"),
            _action("transcribe"),
            _action("save_timeline", timeline=_TL),
            _action("write_scene_file", path="scene/index.html", content=_SCENE_HTML),
            _action("render"),
            _action("review"),
            _action("finish", summary="交付"),
        ]
    )
    res = await sa.StudioToolLoop(ws, llm, _RULES_TEXT, str(src), emit).run()

    assert res["ok"] is True, res
    assert res["error"] is None
    assert res["turns"] == 8
    assert res["video_path"] == str(Path(ws["out"]) / "final.mp4")
    assert Path(res["video_path"]).is_file()
    assert res["review"]["errors"] == [], res["review"]
    assert "loudness" in res["review"]
    # 协议：response_format 固定 json_object；回喂为稳定 JSON（turn/action/ok/result）
    assert all(c["response_format"] == {"type": "json_object"} for c in llm.calls)
    fb_rules = _feedback_of(llm, 1)
    assert fb_rules["ok"] is True and "打法" in fb_rules["result"]["content"]
    fb_t = _feedback_of(llm, 2)
    assert len(fb_t["result"]["segments"]) == 1 and "duration" in fb_t["result"]
    # 落盘产物
    assert (Path(ws["analysis"]) / "speech.json").is_file()
    assert (Path(ws["analysis"]) / "timeline.json").is_file()
    assert transcribe_calls["n"] == 1
    # 进度事件：每个工具阶段一次 + 终态 deliver
    for ph in (
        "list_rules",
        "read_rules_file",
        "transcribe",
        "save_timeline",
        "write_scene_file",
        "render",
        "review",
        "deliver",
    ):
        assert ph in _phases(events), _phases(events)
    assert events[-1]["phase"] == "deliver"
    assert events[-1]["video_path"] == res["video_path"]
    assert all(e.get("tool") for e in events)


# ── 场景 b：write_scene_file 越界路径被拒 ────────────────────────────────


async def test_write_scene_file_path_escapes_rejected(monkeypatch, tmp_path):
    ws = _make_ws(monkeypatch, tmp_path, "agent_b")
    emit, events = _recorder()
    llm = FakeLLM(
        [
            _action("write_scene_file", path="../x.html", content="x"),
            _action("write_scene_file", path="C:/x.html", content="x"),
            _action("write_scene_file", path="d:/x.html", content="x"),
            _action("write_scene_file", path="assets/x.html", content="x"),
            _action("write_scene_file", path="scene/ok.html", content="<p>ok</p>"),
            _action("finish"),
            _action("fail", reason="演示结束"),
        ]
    )
    res = await sa.StudioToolLoop(ws, llm, "", "whatever.mp4", emit).run()

    assert res["ok"] is False and res["turns"] == 7  # 每次违规都计轮，不崩
    for i, bad in enumerate(["../x.html", "C:/x.html", "d:/x.html", "assets/x.html"]):
        fb = _feedback_of(llm, i)
        assert fb["ok"] is False, (i, fb)
        assert bad in fb["result"]["error"], (i, fb)
    # 合法路径（scene/ 下）可写
    ok_fb = _feedback_of(llm, 4)
    assert ok_fb["ok"] is True and ok_fb["result"]["bytes"] == len("<p>ok</p>".encode("utf-8"))
    assert (Path(ws["scene"]) / "ok.html").read_text(encoding="utf-8") == "<p>ok</p>"
    # assets/ 是 scene 外的合法目录，但 write 只限 scene/ → 不得落盘
    assert not (Path(ws["assets"]) / "x.html").exists()
    assert not (tmp_path / "x.html").exists()
    # 未 render 时 finish 被拒且回喂原因
    fb5 = _feedback_of(llm, 5)
    assert fb5["ok"] is False and "render" in fb5["result"]["error"]
    assert "fail" in _phases(events)


# ── 场景 c：非法 JSON / 缺字段 / 未知 action 计轮不崩 ────────────────────


async def test_bad_json_and_unknown_action_count_turns(monkeypatch, tmp_path):
    ws = _make_ws(monkeypatch, tmp_path, "agent_c")
    emit, events = _recorder()
    llm = FakeLLM(
        [
            "not json",
            json.dumps({"nope": 1}, ensure_ascii=False),
            _action("noop"),
            _action("fail", reason="协议演示结束"),
        ]
    )
    res = await sa.StudioToolLoop(ws, llm, "", "whatever.mp4", emit).run()

    assert res["ok"] is False and res["turns"] == 4
    assert "JSON" in _feedback_of(llm, 0)["result"]["error"]
    assert "action" in _feedback_of(llm, 1)["result"]["error"]
    assert "未知" in _feedback_of(llm, 2)["result"]["error"]
    assert "fail" in _phases(events)


# ── 场景 d：finish 前置条件（未 render / review 有错 → 拒绝） ────────────


async def test_finish_requires_render_and_clean_review(monkeypatch, tmp_path):
    ws = _make_ws(monkeypatch, tmp_path, "agent_d")
    render_calls = []

    def fake_render(scene_html, frames_dir, duration, fps, width, height, audio_path, out_video):
        render_calls.append(duration)
        Path(out_video).write_bytes(b"fake mp4")
        return SimpleNamespace(count=8, fps=float(fps), elapsed=0.1)

    review_results = [
        {"errors": ["缺帧: frame_000007"], "loudness": {"rms": -20.0, "peak": -12.0}},
        {"errors": [], "loudness": {"rms": -20.0, "peak": -12.0}},
    ]

    def fake_review(ws_, tl, video):
        return review_results.pop(0)

    monkeypatch.setattr(sa, "render_scene", fake_render)
    monkeypatch.setattr(sa, "review_workspace", fake_review)
    emit, events = _recorder()
    llm = FakeLLM(
        [
            _action("finish"),  # 拒：未 render
            _action("render"),  # 拒：未 save_timeline
            _action("save_timeline", timeline=_TL),
            _action("write_scene_file", path="scene/index.html", content=_SCENE_HTML),
            _action("render"),  # 第 1 次
            _action("review"),  # errors 非空
            _action("finish"),  # 拒：review 有错
            _action("render"),  # 第 2 次（修复轮）
            _action("review"),  # errors 空
            _action("finish", summary="ok"),
        ]
    )
    res = await sa.StudioToolLoop(ws, llm, "", "whatever.mp4", emit).run()

    assert res["ok"] is True, res
    assert res["turns"] == 10
    assert res["video_path"].endswith("final.mp4") and Path(res["video_path"]).is_file()
    assert res["review"]["errors"] == []
    assert "render" in _feedback_of(llm, 0)["result"]["error"]
    assert "错误" in _feedback_of(llm, 6)["result"]["error"]
    assert len(render_calls) == 2
    assert "deliver" in _phases(events)


# ── 场景 e：render→review→fix 超预算 → 如实失败并带最近 review 错误 ──────


async def test_render_review_fix_loop_budget(monkeypatch, tmp_path):
    ws = _make_ws(monkeypatch, tmp_path, "agent_e")
    render_calls = []

    def fake_render(scene_html, frames_dir, duration, fps, width, height, audio_path, out_video):
        render_calls.append(duration)
        Path(out_video).write_bytes(b"fake mp4")
        return SimpleNamespace(count=8, fps=float(fps), elapsed=0.1)

    def fake_review(ws_, tl, video):
        return {"errors": ["时长偏差超限: timeline.duration=1.0, 实测=2.001s"], "loudness": {}}

    monkeypatch.setattr(sa, "render_scene", fake_render)
    monkeypatch.setattr(sa, "review_workspace", fake_review)
    emit, events = _recorder()
    llm = FakeLLM(
        [
            _action("save_timeline", timeline=_TL),
            _action("write_scene_file", path="scene/index.html", content=_SCENE_HTML),
            _action("render"),
            _action("review"),
            _action("render"),
            _action("review"),
            _action("render"),
            _action("review"),
            _action("render"),  # 第 4 次请求 → 超预算终止
        ]
    )
    res = await sa.StudioToolLoop(ws, llm, "", "whatever.mp4", emit).run()

    assert res["ok"] is False
    assert res["turns"] == 9
    assert len(render_calls) == 3  # 渲染尝试 ≤ 3 次硬顶
    assert "预算超限" in res["error"]
    assert "时长偏差" in res["error"]  # error 含最近 review 错误摘要
    assert res["review"] is not None and res["review"]["errors"]
    assert "fail" in _phases(events)


def _install_stubs(monkeypatch, review_queue=None):
    """在 studio_agent 命名空间内钉 render/review；review_queue 依序回放。"""
    renders, reviews = [], []

    def fake_render(scene_html, frames_dir, duration, fps, width, height, audio_path, out_video):
        renders.append(duration)
        Path(out_video).write_bytes(b"fake mp4")
        return SimpleNamespace(count=8, fps=float(fps), elapsed=0.1)

    def fake_review(ws_, tl, video):
        r = review_queue.pop(0) if review_queue else {"errors": [], "loudness": {}}
        reviews.append(r)
        return r

    monkeypatch.setattr(sa, "render_scene", fake_render)
    monkeypatch.setattr(sa, "review_workspace", fake_review)
    return renders, reviews


# ── 审查修复 1：finish 产物新鲜度（改场景/时间轴不重渲 → 拒；review 必须晚于最近 render） ──


async def test_finish_rejected_when_scene_modified_after_render(monkeypatch, tmp_path):
    ws = _make_ws(monkeypatch, tmp_path, "agent_fresh1")
    renders, reviews = _install_stubs(
        monkeypatch, review_queue=[{"errors": [], "loudness": {}}, {"errors": [], "loudness": {}}]
    )
    emit, events = _recorder()
    llm = FakeLLM(
        [
            _action("save_timeline", timeline=_TL),
            _action("write_scene_file", path="scene/index.html", content=_SCENE_HTML),
            _action("render"),
            _action("review"),
            _action(
                "write_scene_file", path="scene/index.html", content=_SCENE_HTML + "<!--touched-->"
            ),  # 改场景但不重渲
            _action("finish"),  # 拒：成片已过期
            _action("render"),
            _action("review"),
            _action("finish", summary="ok"),  # 重渲+重检后恢复
        ]
    )
    res = await sa.StudioToolLoop(ws, llm, "", "whatever.mp4", emit).run()

    fb5 = _feedback_of(llm, 5)
    assert fb5["ok"] is False, "改场景后未重渲的 finish 被放行（过期产物漏洞）"
    assert "过期" in fb5["result"]["error"] and "render" in fb5["result"]["error"]
    assert res["ok"] is True, res
    assert res["turns"] == 9
    assert len(renders) == 2 and len(reviews) == 2
    assert res["review"]["errors"] == []
    assert "deliver" in _phases(events)


async def test_finish_rejected_when_timeline_modified_after_render(monkeypatch, tmp_path):
    ws = _make_ws(monkeypatch, tmp_path, "agent_fresh2")
    renders, reviews = _install_stubs(
        monkeypatch, review_queue=[{"errors": [], "loudness": {}}, {"errors": [], "loudness": {}}]
    )
    llm = FakeLLM(
        [
            _action("save_timeline", timeline=_TL),
            _action("write_scene_file", path="scene/index.html", content=_SCENE_HTML),
            _action("render"),
            _action("review"),
            _action("save_timeline", timeline=_TL),  # 改时间轴但不重渲
            _action("finish"),  # 拒：成片已过期
            _action("render"),
            _action("review"),
            _action("finish", summary="ok"),
        ]
    )
    res = await sa.StudioToolLoop(ws, llm, "", "whatever.mp4", _recorder()[0]).run()

    fb5 = _feedback_of(llm, 5)
    assert fb5["ok"] is False and "过期" in fb5["result"]["error"]
    assert res["ok"] is True and res["turns"] == 9
    assert len(renders) == 2 and len(reviews) == 2


async def test_finish_rejected_when_review_older_than_last_render(monkeypatch, tmp_path):
    """旁支：render→review(clean)→再次 render→finish 不得用旧 review 放行。"""
    ws = _make_ws(monkeypatch, tmp_path, "agent_fresh3")
    renders, reviews = _install_stubs(
        monkeypatch, review_queue=[{"errors": [], "loudness": {}}, {"errors": [], "loudness": {}}]
    )
    llm = FakeLLM(
        [
            _action("save_timeline", timeline=_TL),
            _action("write_scene_file", path="scene/index.html", content=_SCENE_HTML),
            _action("render"),
            _action("review"),  # clean，但随后又 render
            _action("render"),  # 重渲未重检
            _action("finish"),  # 拒：最近 review 早于最近成功 render
            _action("review"),  # 重检 clean
            _action("finish", summary="ok"),
        ]
    )
    res = await sa.StudioToolLoop(ws, llm, "", "whatever.mp4", _recorder()[0]).run()

    fb5 = _feedback_of(llm, 5)
    assert fb5["ok"] is False, "旧 review 被用来放行重渲后的成片"
    assert "review" in fb5["result"]["error"]
    assert res["ok"] is True and res["turns"] == 8
    assert len(renders) == 2 and len(reviews) == 2


# ── 审查修复 2：file:/// 逃逸与 args 非 dict（钉住既有防线） ──────────────


async def test_file_scheme_paths_rejected(monkeypatch, tmp_path):
    ws = _make_ws(monkeypatch, tmp_path, "agent_file")
    llm = FakeLLM(
        [
            _action("write_scene_file", path="file:///d:/x.html", content="x"),
            _action("read_rules_file", path="file:///c:/secret.md"),
            _action("fail", reason="file 结束"),
        ]
    )
    res = await sa.StudioToolLoop(ws, llm, "", "whatever.mp4", _recorder()[0]).run()

    assert res["ok"] is False and res["turns"] == 3
    fb0 = _feedback_of(llm, 0)
    assert fb0["ok"] is False and "file:///d:/x.html" in fb0["result"]["error"]
    assert not (Path(ws["scene"]) / "x.html").exists()
    fb1 = _feedback_of(llm, 1)
    assert fb1["ok"] is False and "file:///c:/secret.md" in fb1["result"]["error"]


async def test_args_not_dict_counts_turn_and_feeds_back(monkeypatch, tmp_path):
    ws = _make_ws(monkeypatch, tmp_path, "agent_args")
    llm = FakeLLM(
        [
            json.dumps({"action": "write_scene_file", "args": "oops"}, ensure_ascii=False),
            _action("fail", reason="args 结束"),
        ]
    )
    res = await sa.StudioToolLoop(ws, llm, "", "whatever.mp4", _recorder()[0]).run()

    assert res["ok"] is False and res["turns"] == 2
    fb0 = _feedback_of(llm, 0)
    assert fb0["ok"] is False
    assert "args" in fb0["result"]["error"] and "str" in fb0["result"]["error"]


# ── 审查修复 3：回喂按 UTF-8 字节截断（不破坏 JSON 格式） ─────────────────


def test_feedback_respects_utf8_byte_budget(tmp_path):
    loop = sa.StudioToolLoop(
        {"out": str(tmp_path), "scene": str(tmp_path)}, FakeLLM([]), "", "v.mp4", None
    )
    giant = "错" * 3000  # 单条错误 3000 个中文字符 ≈ 9KB UTF-8
    raw = loop._feedback("review", True, {"errors": [giant], "loudness": {}})

    assert len(raw.encode("utf-8")) <= 4096, f"回喂超字节预算：{len(raw.encode('utf-8'))}B"
    obj = json.loads(raw)  # 截断不破坏回喂格式
    assert obj["ok"] is True and obj["turn"] == 0
    assert obj["result"]["truncated"] is True
    assert obj["result"]["preview"]


# ── 额外：read_rules_file 路径逃逸 ───────────────────────────────────────


async def test_read_rules_file_escape_rejected(monkeypatch, tmp_path):
    ws = _make_ws(monkeypatch, tmp_path, "agent_r")
    llm = FakeLLM(
        [
            _action("read_rules_file", path="../../secret.md"),
            _action("read_rules_file", path="..\\secret.md"),
            _action("read_rules_file", path="C:/secret.md"),
            _action("fail", reason="r 结束"),
        ]
    )
    res = await sa.StudioToolLoop(ws, llm, "", "whatever.mp4", _recorder()[0]).run()

    assert res["ok"] is False and res["turns"] == 4
    for i, bad in enumerate(["../../secret.md", "..\\secret.md", "C:/secret.md"]):
        fb = _feedback_of(llm, i)
        assert fb["ok"] is False and bad in fb["result"]["error"], (i, fb)


# ── 额外：transcribe 缓存只执行一次 ──────────────────────────────────────


async def test_transcribe_cached_single_call(monkeypatch, tmp_path):
    ws = _make_ws(monkeypatch, tmp_path, "agent_s")
    calls = {"n": 0}

    def fake_transcribe(audio_path, model_size="small", language=None):
        calls["n"] += 1
        return [TranscribeSegment(text="hi", start=0.0, end=1.5, words=[])]

    monkeypatch.setattr(sa, "transcribe", fake_transcribe)
    monkeypatch.setattr(sa, "probe_duration", lambda p: 1.5)
    llm = FakeLLM(
        [
            _action("transcribe"),
            _action("transcribe"),
            _action("fail", reason="s 结束"),
        ]
    )
    res = await sa.StudioToolLoop(ws, llm, "", "whatever.mp4", _recorder()[0]).run()

    assert res["turns"] == 3 and calls["n"] == 1
    assert (Path(ws["analysis"]) / "speech.json").is_file()
    fb0 = _feedback_of(llm, 0)
    assert fb0["result"]["cached"] is False and fb0["result"]["duration"] == 1.5
    # 第 2 次同参 transcribe：幂等占位回喂，完整结果不重复注入上下文
    fb1 = _feedback_of(llm, 1)
    assert fb1["result"]["repeated"] is True and "cached" not in fb1["result"]


# ── 额外：start_preview 幂等 + preview_url 透传 ──────────────────────────


async def test_start_preview_idempotent(monkeypatch, tmp_path):
    ws = _make_ws(monkeypatch, tmp_path, "agent_p")
    starts = {"n": 0}

    class FakePreviewServer:
        def __init__(self, ws_, port=0):
            assert "scene" in ws_

        def start(self):
            starts["n"] += 1
            return "http://127.0.0.1:54321/"

        def stop(self):
            pass

    monkeypatch.setattr(sa, "StudioPreviewServer", FakePreviewServer)
    emit, events = _recorder()
    llm = FakeLLM(
        [
            _action("start_preview"),
            _action("start_preview"),
            _action("fail", reason="p 结束"),
        ]
    )
    res = await sa.StudioToolLoop(ws, llm, "", "whatever.mp4", emit).run()

    assert res["turns"] == 3 and starts["n"] == 1
    fb0 = _feedback_of(llm, 0)
    assert fb0["result"]["preview_url"].endswith(":54321/")
    # 第 2 次同参调用：幂等占位回喂（完整结果不重复注入），服务仍只启动一次
    fb1 = _feedback_of(llm, 1)
    assert fb1["result"]["repeated"] is True and "preview_url" not in fb1["result"]
    preview_events = [e for e in events if e["phase"] == "start_preview"]
    assert len(preview_events) == 2 and all("preview_url" in e for e in preview_events)


# ── 额外：prepare_assets 白名单校验 + SFX 文件名清单（不做风格筛选） ─────


async def test_prepare_assets_sfx_list_and_bad_kind(monkeypatch, tmp_path):
    ws = _make_ws(monkeypatch, tmp_path, "agent_pa")
    llm = FakeLLM(
        [
            _action("prepare_assets", kind="sfx_list"),
            _action("prepare_assets", kind="bogus"),
            _action("fail", reason="pa 结束"),
        ]
    )
    res = await sa.StudioToolLoop(ws, llm, "", "whatever.mp4", _recorder()[0]).run()

    assert res["turns"] == 3
    fb0 = _feedback_of(llm, 0)
    assert fb0["ok"] is True and fb0["result"]["count"] >= 6
    assert "pop.wav" in fb0["result"]["files"]
    fb1 = _feedback_of(llm, 1)
    assert fb1["ok"] is False and "kind" in fb1["result"]["error"]


# ── 额外：max_turns 硬顶 ─────────────────────────────────────────────────


async def test_max_turns_budget_terminates(monkeypatch, tmp_path):
    ws = _make_ws(monkeypatch, tmp_path, "agent_m")
    llm = FakeLLM(["bad1", "bad2"])
    emit, events = _recorder()
    res = await sa.StudioToolLoop(ws, llm, "", "v.mp4", emit, max_turns=2).run()

    assert res["ok"] is False and res["turns"] == 2
    assert len(llm.calls) == 2
    assert "轮数超限" in res["error"]
    assert "fail" in _phases(events)


# ── LLM 信封兼容：真 DeepSeekAdapter.chat 返回 {"content": str, ...} dict，非 str ──


class EnvelopeLLM(FakeLLM):
    """模拟真实 adapter 返回形态：chat → {"content": <正文>, ...} dict 信封。"""

    async def chat(self, messages, response_format=None):
        self.calls.append(
            {
                "messages": [dict(m) for m in messages],
                "response_format": response_format,
            }
        )
        assert self.script, "EnvelopeLLM 脚本耗尽"
        return {"content": self.script.pop(0), "reasoning_content": None, "token_usage": 42}


async def test_llm_dict_envelope_adapter_supported(monkeypatch, tmp_path):
    """真 LLM 适配层返回 dict 信封（content 字段）时循环照常工作（Task 6 验收发现）。"""
    ws = _make_ws(monkeypatch, tmp_path, "agent_env")
    llm = EnvelopeLLM(
        [
            _action("list_rules"),
            _action("fail", reason="信封测试结束"),
        ]
    )
    res = await sa.StudioToolLoop(ws, llm, _RULES_TEXT, "whatever.mp4", _recorder()[0]).run()

    assert res["ok"] is False and res["turns"] == 2
    assert "信封测试结束" in res["error"]
    fb = _feedback_of(llm, 0)  # list_rules 被真实执行且回喂 ok
    assert fb["ok"] is True and fb["result"]["count"] >= 1


# ── 红线：system prompt 零风格内容 ───────────────────────────────────────


def test_system_prompt_has_no_style_constants():
    import re

    prompt = sa._SYSTEM_PROMPT
    assert not re.search(r"#[0-9a-fA-F]{6}\b", prompt), "system prompt 含十六进制色值"
    for banned in ("font-family", "rgba(", "rgb(", "animation", "transition", "ease"):
        assert banned not in prompt, banned


# ── 集成：transcribe 工具内嵌同音字纠错 pass（P2 验收回灌） ──────────────


async def test_transcribe_tool_applies_homophone_fix(monkeypatch, tmp_path):
    """transcribe 工具结果带 corrections 字段；speech.json 的词级 token 已被
    纠错（时间戳不动）；纠错在工具内部消耗一次 LLM 调用（脚本需补一段响应，
    不计循环轮数），对协议透明。"""
    from capabilities.video.adapters.whisper_transcribe import TranscribeWord

    ws = _make_ws(monkeypatch, tmp_path, "agent_fix")
    src = _make_video(tmp_path, with_audio=True)

    def fake_transcribe(audio_path, model_size="small", language=None):
        return [
            TranscribeSegment(
                text="入虾之后",
                start=0.0,
                end=4.0,
                words=[
                    TranscribeWord(text="入", start=0.0, end=1.0),
                    TranscribeWord(text="虾", start=1.0, end=2.0),
                    TranscribeWord(text="之", start=2.0, end=3.0),
                    TranscribeWord(text="后", start=3.0, end=4.0),
                ],
            )
        ]

    monkeypatch.setattr(sa, "transcribe", fake_transcribe)
    monkeypatch.setattr(sa, "probe_duration", lambda p: 4.0)
    emit, events = _recorder()
    # timeline 的 speech 须覆盖转录末段（save_timeline 覆盖完整性校验）
    tl = dict(_TL)
    tl["speech"] = [{"text": "入夏之后", "start": 0.0, "end": 4.0}]
    llm = FakeLLM(
        [
            _action("list_rules"),
            _action("read_rules_file", path="method.md"),
            _action("transcribe"),
            # 纠错 pass 在 transcribe 工具内部消费（chat 不经循环、不计轮）
            json.dumps({"replacements": [{"wrong": "入虾", "right": "入夏"}]}, ensure_ascii=False),
            _action("save_timeline", timeline=tl),
            _action("write_scene_file", path="scene/index.html", content=_SCENE_HTML),
            _action("render"),
            _action("review"),
            _action("finish", summary="交付"),
        ]
    )
    res = await sa.StudioToolLoop(ws, llm, _RULES_TEXT, str(src), emit).run()

    assert res["ok"] is True, res
    assert res["turns"] == 8  # 工具内部的纠错调用不计循环轮数
    # transcribe 回喂带 corrections（扫描定位，规避手工索引脆弱性）
    fbs = []
    for c in llm.calls:
        try:
            fb = json.loads(c["messages"][-1]["content"])
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(fb, dict) and fb.get("action") == "transcribe" and fb.get("ok") is True:
            fbs.append(fb)
    assert len(fbs) == 1
    assert fbs[0]["result"]["corrections"] == ["入虾→入夏"]
    # speech.json：token 与 segment.text 均已纠错，时间戳不动
    speech = json.loads((Path(ws["analysis"]) / "speech.json").read_text(encoding="utf-8"))
    assert speech["corrections"] == ["入虾→入夏"]
    assert speech["segments"][0]["text"] == "入夏之后"
    words = speech["segments"][0]["words"]
    assert [w["text"] for w in words] == ["入", "夏", "之", "后"]
    assert [w["start"] for w in words] == [0.0, 1.0, 2.0, 3.0]
    assert [w["end"] for w in words] == [1.0, 2.0, 3.0, 4.0]


# ── prepare_assets(frames) 底部烧录元素探测接线（36.9s 实跑回灌） ────────


async def test_prepare_assets_frames_reports_bottom_elements(monkeypatch, tmp_path):
    """frames 分支成功后并入 bottom_elements（probe 以 monkeypatch 注入，
    稳做法）；suspected=True 时 hint 追加避让纪律并 emit 前端提醒。"""
    from capabilities.video.adapters.studio_assets import FrameSeqInfo

    ws = _make_ws(monkeypatch, tmp_path, "agent_bottom")
    info = FrameSeqInfo(
        frame_dir=tmp_path / "frames",
        count=3,
        fps=24.0,
        width=320,
        height=568,
        pattern="frame_%06d.jpg",
    )
    monkeypatch.setattr(sa, "extract_frames", lambda video_path, out_dir, fps=24.0: info)
    probe = {
        "suspected": True,
        "error": None,
        "ratio_stats": {"min": 2.1, "mean": 3.4},
        "sampled": ["frame_000000.jpg"],
    }
    monkeypatch.setattr(sa, "probe_bottom_elements", lambda d, sample=5: dict(probe))
    emit, events = _recorder()
    loop = sa.StudioToolLoop(
        ws, FakeLLM([_action("finish", summary="s")]), _RULES_TEXT, str(tmp_path / "v.mp4"), emit
    )

    ok, result, _extra = await loop._t_prepare_assets({"kind": "frames"})

    assert ok is True
    assert result["bottom_elements"]["suspected"] is True
    assert "底部 18%" in result["hint"]  # hint 追加避让纪律
    warns = [e for e in events if "烧录" in json.dumps(e, ensure_ascii=False)]
    assert warns, f"expected emit warning event, events={events}"
    assert warns[0]["tool"] == "prepare_assets"


async def test_prepare_assets_frames_clean_bottom_no_emit(monkeypatch, tmp_path):
    """suspected=False（含 probe error 降级）→ 不 emit 提醒，字段照常并入。"""
    from capabilities.video.adapters.studio_assets import FrameSeqInfo

    ws = _make_ws(monkeypatch, tmp_path, "agent_bottom_clean")
    info = FrameSeqInfo(
        frame_dir=tmp_path / "frames",
        count=2,
        fps=24.0,
        width=320,
        height=568,
        pattern="frame_%06d.jpg",
    )
    monkeypatch.setattr(sa, "extract_frames", lambda video_path, out_dir, fps=24.0: info)
    monkeypatch.setattr(
        sa,
        "probe_bottom_elements",
        lambda d, sample=5: {
            "suspected": False,
            "error": None,
            "ratio_stats": {"min": 0.8, "mean": 1.0},
            "sampled": ["frame_000000.jpg"],
        },
    )
    emit, events = _recorder()
    loop = sa.StudioToolLoop(
        ws, FakeLLM([_action("finish", summary="s")]), _RULES_TEXT, str(tmp_path / "v.mp4"), emit
    )

    ok, result, _extra = await loop._t_prepare_assets({"kind": "frames"})

    assert ok is True
    assert result["bottom_elements"]["suspected"] is False
    assert not [e for e in events if "烧录" in json.dumps(e, ensure_ascii=False)]


# ── generate_image：万相文生图工具（图案素材注入 StudioToolLoop） ─────────


class FakeImageAdapter:
    """打桩 WanxAdapter：记录调用，按脚本返回图片字节或抛异常。"""

    def __init__(self, images=None, exc=None):
        self.images = [b"PNGDATA"] if images is None else images
        self.exc = exc
        self.calls = []

    async def generate(self, prompt, size="1024x1024", count=1):
        self.calls.append({"prompt": prompt, "size": size, "count": count})
        if self.exc:
            raise self.exc
        return self.images


async def test_generate_image_writes_asset_and_returns_pattern(monkeypatch, tmp_path):
    ws = _make_ws(monkeypatch, tmp_path, "agent_g1")
    adapter = FakeImageAdapter()
    monkeypatch.setattr(sa, "_get_image_adapter", lambda: adapter)
    loop = sa.StudioToolLoop(ws, FakeLLM([]), "", "v.mp4", None)

    ok, res, _extra = await loop._t_generate_image(
        {"prompt": "牛油果切面插画，扁平风格", "name": "avocado-cut", "ratio": "16:9"}
    )

    assert ok is True, res
    out = Path(ws["assets"]) / "gen" / "avocado-cut.png"
    assert out.is_file() and out.read_bytes() == b"PNGDATA"
    assert res["example"] == "../assets/gen/avocado-cut.png"
    assert res["bytes"] == len(b"PNGDATA")
    assert adapter.calls[0]["size"] == "1280*720"  # 16:9 → 横版尺寸
    assert adapter.calls[0]["count"] == 1


async def test_generate_image_validates_args(monkeypatch, tmp_path):
    ws = _make_ws(monkeypatch, tmp_path, "agent_g2")
    adapter = FakeImageAdapter()
    monkeypatch.setattr(sa, "_get_image_adapter", lambda: adapter)
    loop = sa.StudioToolLoop(ws, FakeLLM([]), "", "v.mp4", None)

    bad_args = (
        {"name": "x"},  # 缺 prompt
        {"prompt": "p"},  # 缺 name
        {"prompt": "p", "name": "Bad Name"},  # 大写/空格
        {"prompt": "p", "name": "../evil"},  # 路径逃逸
        {"prompt": "p", "name": "ok-name", "ratio": "4:3"},  # ratio 非法
    )
    for args in bad_args:
        ok, res, _extra = await loop._t_generate_image(args)
        assert ok is False and "error" in res, args
    assert adapter.calls == []  # 全部被拒：未触达生图适配器
    assert not (Path(ws["assets"]) / "gen").exists()  # 未落盘


async def test_generate_image_adapter_failure_reported(monkeypatch, tmp_path):
    ws = _make_ws(monkeypatch, tmp_path, "agent_g3")
    monkeypatch.setattr(
        sa, "_get_image_adapter", lambda: FakeImageAdapter(exc=RuntimeError("quota exceeded"))
    )
    loop = sa.StudioToolLoop(ws, FakeLLM([]), "", "v.mp4", None)

    ok, res, _extra = await loop._t_generate_image({"prompt": "p", "name": "a"})

    assert ok is False and "quota exceeded" in res["error"]
    assert not list((Path(ws["assets"]) / "gen").glob("*"))


async def test_generate_image_action_in_loop(monkeypatch, tmp_path):
    """generate_image 走完整工具循环：回喂 ok 且带 example 路径，素材落盘。"""
    ws = _make_ws(monkeypatch, tmp_path, "agent_g4")
    monkeypatch.setattr(sa, "_get_image_adapter", lambda: FakeImageAdapter())
    llm = FakeLLM(
        [
            _action("generate_image", prompt="插图", name="illus"),
            _action("fail", reason="g4 结束"),
        ]
    )
    res = await sa.StudioToolLoop(ws, llm, "", "whatever.mp4", _recorder()[0]).run()

    assert res["turns"] == 2
    fb = _feedback_of(llm, 0)
    assert fb["ok"] is True
    assert fb["result"]["example"] == "../assets/gen/illus.png"
    assert (Path(ws["assets"]) / "gen" / "illus.png").is_file()


# ── mix_sfx：定点音效混音工具（重点词低频音效注入成片音轨） ───────────────


def _make_sfx_dir(tmp_path):
    """生成临时 SFX 库（真实短 wav，无需 ffmpeg 造 wav 本体）。"""
    import math
    import struct
    import wave

    d = tmp_path / "sfx"
    d.mkdir(parents=True)
    for name in ("impact", "ding"):
        with wave.open(str(d / f"{name}.wav"), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(44100)
            frames = b"".join(
                struct.pack("<h", int(12000 * math.sin(2 * math.pi * 220 * i / 44100)))
                for i in range(44100 // 4)
            )  # 0.25s 正弦
            w.writeframes(frames)
    return d


def _probe_audio_duration(path):
    """imageio-ffmpeg 无 ffprobe，用 ffmpeg -i 的 stderr Duration 行解析。"""
    import re as _re

    from capabilities.video.adapters.html_to_video import resolve_ffmpeg

    out = subprocess.run([resolve_ffmpeg(), "-i", str(path)], capture_output=True)
    m = _re.search(rb"Duration: (\d+):(\d+):(\d+\.\d+)", out.stderr or b"")
    if not m:
        return None
    h, mn, s = m.groups()
    return int(h) * 3600 + int(mn) * 60 + float(s)


async def test_mix_sfx_writes_track_with_delayed_hits(monkeypatch, tmp_path):
    """合法 events → 口播轨 + 定点音效混成 audio_mixed.m4a，时长≈原片。"""
    ws = _make_ws(monkeypatch, tmp_path, "agent_sfx1")
    monkeypatch.setattr(sa, "_SFX_DIR", _make_sfx_dir(tmp_path))
    src = _make_video(tmp_path, with_audio=True, duration=1.0)
    loop = sa.StudioToolLoop(ws, FakeLLM([]), "", str(src), None)

    ok, res, _extra = await loop._t_mix_sfx(
        {
            "events": [
                {"file": "impact.wav", "t": 0.4},
                {"file": "ding.wav", "t": 0.8, "gain": 0.5},
            ]
        }
    )

    assert ok is True, res
    track = Path(ws["out"]) / "audio_mixed.m4a"
    assert track.is_file() and track.stat().st_size > 0
    assert res["path"] == str(track)
    assert res["events"] == 2
    dur = _probe_audio_duration(track)
    assert dur is not None and 0.9 <= dur <= 1.4  # 以口播轨时长为准


async def test_mix_sfx_validates_args(monkeypatch, tmp_path):
    """非法/幻觉输入全拒：不触 ffmpeg、不落盘。"""
    ws = _make_ws(monkeypatch, tmp_path, "agent_sfx2")
    monkeypatch.setattr(sa, "_SFX_DIR", _make_sfx_dir(tmp_path))
    loop = sa.StudioToolLoop(ws, FakeLLM([]), "", "v.mp4", None)

    bad_args = (
        {"events": []},  # 空
        {"events": "impact.wav"},  # 非列表
        {"events": [{"file": "boom.wav", "t": 0.3}]},  # 幻觉文件名
        {"events": [{"file": "../impact.wav", "t": 0.3}]},  # 路径逃逸
        {"events": [{"file": "impact.wav", "t": -0.1}]},  # 负时间
        {"events": [{"file": "impact.wav", "t": 99999}]},  # 越界
        {"events": [{"file": "impact.wav", "t": 0.3, "gain": 0}]},  # gain 非法
        {"events": [{"file": "impact.wav", "t": 0.3, "gain": 1.5}]},
        {"events": [{"file": "impact.wav", "t": 0.3, "gain": "loud"}]},
        {"events": ["impact.wav"]},  # 非字典项
        {},  # 缺 events
    )
    for args in bad_args:
        ok, res, _extra = await loop._t_mix_sfx(args)
        assert ok is False and "error" in res, args
    assert not (Path(ws["out"]) / "audio_mixed.m4a").exists()  # 未落盘


async def test_render_accepts_mixed_audio(monkeypatch, tmp_path):
    """render 扩展 audio="mixed"：混音轨存在时传入其路径，缺轨/乱值被拒。"""
    ws = _make_ws(monkeypatch, tmp_path, "agent_sfx3")
    monkeypatch.setattr(sa, "_SFX_DIR", _make_sfx_dir(tmp_path))
    src = _make_video(tmp_path, with_audio=True, duration=1.0)
    loop = sa.StudioToolLoop(ws, FakeLLM([]), "", str(src), None)
    loop._timeline = dict(_TL)
    (Path(ws["scene"]) / "index.html").write_text(_SCENE_HTML, encoding="utf-8")

    captured = []

    def fake_render(scene_html, frames_dir, duration, fps, width, height, audio_path, out_video):
        captured.append(audio_path)
        return SimpleNamespace(
            frames_dir=frames_dir, count=1, fps=fps, elapsed=0.0, video_path=out_video
        )

    monkeypatch.setattr(sa, "render_scene", fake_render)

    # mixed 但轨未混 → 拒
    ok, res, _extra = await loop._t_render({"audio": "mixed"})
    assert ok is False and "audio_mixed" in res["error"]
    assert captured == []

    # 先混音 → mixed 用混音轨；original 用原片；乱值被拒
    ok, res, _extra = await loop._t_mix_sfx({"events": [{"file": "impact.wav", "t": 0.3}]})
    assert ok is True, res

    ok, _res, _extra = await loop._t_render({"audio": "mixed"})
    assert ok is True, res
    assert captured[-1].endswith("audio_mixed.m4a")

    ok, _res, _extra = await loop._t_render({"audio": "original"})
    assert ok is True
    assert captured[-1] == str(src)

    ok, res, _extra = await loop._t_render({"audio": "bogus"})
    assert ok is False and "audio" in res["error"]


# ── 重复调用拦截：同签名工具累计重复时回喂提示并止损 ─────────────────────


async def test_repeat_call_gets_nudge_then_loop_stops(monkeypatch, tmp_path):
    """同签名累计：第 2 次回喂带推进提示，累计第 4 次循环如实终止。"""
    ws = _make_ws(monkeypatch, tmp_path, "agent_rep1")
    llm = FakeLLM(
        [
            _action("prepare_assets", kind="frames"),
            _action("prepare_assets", kind="frames"),
            _action("prepare_assets", kind="frames"),
            _action("prepare_assets", kind="frames"),
            _action("prepare_assets", kind="frames"),
            _action("fail", reason="rep 结束"),
        ]
    )
    res = await sa.StudioToolLoop(ws, llm, "", "whatever.mp4", _recorder()[0]).run()

    assert res["ok"] is False
    assert "累计重复调用" in res["error"]
    # 第 2 次（feedback 索引 1）的回喂附加文本已带推进提示
    raw2 = llm.calls[2]["messages"][-1]["content"]
    assert "相同的参数第 2 次" in raw2 and "禁止再次重复调用" in raw2
    assert res["turns"] == 4


async def test_interleaved_cycle_also_stops(monkeypatch, tmp_path):
    """循环式重复（A-B-A-B-A-B-A 交错）同样拦截：A 累计 4 次即终止。"""
    ws = _make_ws(monkeypatch, tmp_path, "agent_rep3")
    llm = FakeLLM(
        [
            _action("prepare_assets", kind="frames"),
            _action("prepare_assets", kind="sfx_list"),
            _action("prepare_assets", kind="frames"),
            _action("prepare_assets", kind="sfx_list"),
            _action("prepare_assets", kind="frames"),
            _action("prepare_assets", kind="sfx_list"),
            _action("prepare_assets", kind="frames"),
            _action("fail", reason="rep3 结束"),
        ]
    )
    res = await sa.StudioToolLoop(ws, llm, "", "whatever.mp4", _recorder()[0]).run()

    assert res["ok"] is False
    assert "累计重复调用" in res["error"]
    assert res["turns"] == 7  # frames 第 4 次出现时终止


async def test_alternating_calls_not_flagged(monkeypatch, tmp_path):
    """各工具仅出现 1-2 次的正常交替：只提示不终止，走到主动收尾。"""
    ws = _make_ws(monkeypatch, tmp_path, "agent_rep2")
    llm = FakeLLM(
        [
            _action("prepare_assets", kind="frames"),
            _action("prepare_assets", kind="sfx_list"),
            _action("prepare_assets", kind="frames"),  # 总计第 2 次：提示但不终止
            _action("fail", reason="rep2 结束"),
        ]
    )
    res = await sa.StudioToolLoop(ws, llm, "", "whatever.mp4", _recorder()[0]).run()

    assert res["turns"] == 4
    assert res["error"] == "agent 主动终止：rep2 结束"  # 未触发重复止损


# ── timeline 硬校验：required_canvas + 转录覆盖完整性 ────────────────────


async def test_save_timeline_enforces_required_canvas(monkeypatch, tmp_path):
    """required_canvas 设置后：画幅不符直接拒绝，符合则放行。"""
    ws = _make_ws(monkeypatch, tmp_path, "agent_cv1")
    src = _make_video(tmp_path, with_audio=True, duration=1.0)
    loop = sa.StudioToolLoop(ws, FakeLLM([]), "", str(src), None, required_canvas=(1920, 1080))
    loop._timeline = None
    loop._speech_cache = None

    tl = dict(_TL)
    tl["canvas"] = {"w": 720, "h": 1280}
    ok, res, _extra = await loop._t_save_timeline({"timeline": tl})
    assert ok is False
    assert "1920x1080" in res["error"] and "720x1280" in res["error"]

    tl["canvas"] = {"w": 1920, "h": 1080}
    ok, _res, _extra = await loop._t_save_timeline({"timeline": tl})
    assert ok is True


async def test_save_timeline_rejects_partial_speech(monkeypatch, tmp_path):
    """timeline.speech 未覆盖转录末段（字幕偷懒截断）→ 拒绝并列出缺失段。"""
    ws = _make_ws(monkeypatch, tmp_path, "agent_cv2")
    src = _make_video(tmp_path, with_audio=True, duration=1.0)
    loop = sa.StudioToolLoop(ws, FakeLLM([]), "", str(src), None)
    loop._speech_cache = {
        "segments": [
            {"text": "第一句", "start": 0.0, "end": 4.0, "words": []},
            {"text": "后面还有很长的一段话", "start": 4.5, "end": 14.4, "words": []},
        ],
    }

    tl = dict(_TL)
    ok, res, _extra = await loop._t_save_timeline({"timeline": tl})
    if ok is True:
        # _TL 的 speech 恰好覆盖 14.4s 时本测试应改断言；先探测实际值
        assert False, f"_TL speech 覆盖到 {tl['speech'][-1]['end']}，未触发校验"
    assert "未覆盖全部转录段" in res["error"]
    assert "后面还有很长的一段话" in res["error"]

    # 覆盖到转录末段（容差 5%）→ 放行
    tl["speech"] = [
        {"text": "第一句", "start": 0.0, "end": 4.0},
        {"text": "后面还有很长的一段话", "start": 4.5, "end": 14.4},
    ]
    ok, _res, _extra = await loop._t_save_timeline({"timeline": tl})
    assert ok is True


async def test_save_timeline_rejects_fabricated_speech(monkeypatch, tmp_path):
    """timeline.speech 文本与转录原文不符（编造台词）→ 拒绝并附原文对照。"""
    ws = _make_ws(monkeypatch, tmp_path, "agent_fab1")
    src = _make_video(tmp_path, with_audio=True, duration=1.0)
    loop = sa.StudioToolLoop(ws, FakeLLM([]), "", str(src), None)
    loop._speech_cache = {
        "segments": [
            {"text": "我一直觉得上进不是一种情绪", "start": 0.0, "end": 4.0, "words": []},
            {"text": "第二条能叫醒一个人的从来不是大道理", "start": 4.5, "end": 14.4, "words": []},
        ]
    }

    tl = dict(_TL)
    tl["speech"] = [
        {"text": "我一直觉得上进不是一种情绪", "start": 0.0, "end": 4.0},
        {"text": "把目标拆小一点把节奏放慢一点", "start": 4.5, "end": 14.4},  # 编造
    ]
    ok, res, _extra = await loop._t_save_timeline({"timeline": tl})
    assert ok is False
    assert "与转录原文不符" in res["error"]
    assert "第二条能叫醒一个人" in res["error"]  # 附原文对照

    # 忠实编排放行：原文原样 / 标点差异 / 拆段（子串）均合法
    tl["speech"] = [
        {"text": "我一直觉得，上进不是一种情绪。", "start": 0.0, "end": 4.0},
        {"text": "第二条，能叫醒一个人的从来不是大道理", "start": 4.5, "end": 9.0},
        {"text": "能叫醒一个人的从来不是大道理", "start": 9.0, "end": 14.4},
    ]
    ok, _res, _extra = await loop._t_save_timeline({"timeline": tl})
    assert ok is True


# ── timeline 硬校验：required_fx 视觉强度申报 ────────────────────────────


_FX_OK = {
    "tears": [
        {"t": 18.0, "duration": 0.4, "dx_px": 60, "rot_deg": 3.0},
        {"t": 27.0, "duration": 0.4, "dx_px": 70, "rot_deg": -3.5},
    ],
    "shakes": [
        {"t": 3.0, "duration": 0.2, "amplitude_px": 3.0},
        {"t": 11.5, "duration": 0.2, "amplitude_px": 3.5},
    ],
}


async def test_save_timeline_requires_fx_declaration(monkeypatch, tmp_path):
    """required_fx 设置后：未申报 effects / 强度不足 → 拒绝，合规 → 放行。

    实测病灶：agent 实现的撕裂位移仅 ±16px / 1.1° 且 0.1s 内展开完，
    肉眼只见金线一闪、画面"压根没有撕裂"。视觉效果改为 timeline 强制
    申报参数（dx ≥ 40px、|rot| ≥ 2.5°），由确定性校验把关强度。
    """
    ws = _make_ws(monkeypatch, tmp_path, "agent_fx1")
    src = _make_video(tmp_path, with_audio=True, duration=1.0)
    loop = sa.StudioToolLoop(ws, FakeLLM([]), "", str(src), None, required_fx=True)
    loop._timeline = None
    loop._speech_cache = None

    tl = dict(_TL)

    # 1) 完全没申报 effects → 拒绝并提示 tears/shakes 申报格式
    ok, res, _extra = await loop._t_save_timeline({"timeline": tl})
    assert ok is False
    assert "effects" in res["error"] and "tears" in res["error"]

    # 2) 撕裂位移 16px（< 40px 下限）→ 拒绝并提示下限
    tl["effects"] = {
        "tears": [
            {"t": 18.0, "duration": 0.4, "dx_px": 16, "rot_deg": 3.0},
            dict(_FX_OK["tears"][1]),
        ],
        "shakes": [dict(s) for s in _FX_OK["shakes"]],
    }
    ok, res, _extra = await loop._t_save_timeline({"timeline": tl})
    assert ok is False
    assert "40" in res["error"]

    # 3) 旋转 1.1°（< 2.5° 下限）→ 拒绝并提示下限
    tl["effects"] = {
        "tears": [
            {"t": 18.0, "duration": 0.4, "dx_px": 60, "rot_deg": 1.1},
            dict(_FX_OK["tears"][1]),
        ],
        "shakes": [dict(s) for s in _FX_OK["shakes"]],
    }
    ok, res, _extra = await loop._t_save_timeline({"timeline": tl})
    assert ok is False
    assert "2.5" in res["error"]

    # 4) 只申报 1 次撕裂（少于 2 次）→ 拒绝
    tl["effects"] = {
        "tears": [dict(_FX_OK["tears"][0])],
        "shakes": [dict(s) for s in _FX_OK["shakes"]],
    }
    ok, res, _extra = await loop._t_save_timeline({"timeline": tl})
    assert ok is False

    # 5) 震动幅度 6px（超出 2–4px 上限，违反 brief）→ 拒绝
    tl["effects"] = {
        "tears": [dict(t) for t in _FX_OK["tears"]],
        "shakes": [
            {"t": 3.0, "duration": 0.2, "amplitude_px": 6.0},
            {"t": 11.5, "duration": 0.2, "amplitude_px": 3.5},
        ],
    }
    ok, res, _extra = await loop._t_save_timeline({"timeline": tl})
    assert ok is False

    # 6) 合规申报 → 放行
    tl["effects"] = {k: [dict(e) for e in v] for k, v in _FX_OK.items()}
    ok, _res, _extra = await loop._t_save_timeline({"timeline": tl})
    assert ok is True


# ── 预算优化：幂等重复调用占位回喂 + transcribe 词级回喂瘦身 ──────────────


async def test_paramless_tool_args_jitter_still_stops(monkeypatch, tmp_path):
    """无参幂等工具（transcribe）带幻觉参数抖动调用：签名忽略 args，累计 4 次止损。

    实测病灶：LLM 每次给 transcribe 塞不同的幻觉参数（lang/model/foo），
    签名不同 → 拦截器失效 → 60 轮全烧在重复转录上。
    """
    ws = _make_ws(monkeypatch, tmp_path, "agent_jit")

    async def fake_fix(segments, llm, emit, **kw):
        return segments, [], []

    monkeypatch.setattr(
        sa,
        "transcribe",
        lambda p, model_size="small", language=None: [
            TranscribeSegment(text="hi", start=0.0, end=1.5, words=[])
        ],
    )
    monkeypatch.setattr(sa, "probe_duration", lambda p: 1.5)
    monkeypatch.setattr(sa, "fix_homophones", fake_fix)
    llm = FakeLLM(
        [
            _action("transcribe", lang="zh"),
            _action("transcribe", model="small"),
            _action("transcribe"),
            _action("transcribe", foo=1),
        ]
    )
    res = await sa.StudioToolLoop(ws, llm, "", "whatever.mp4", _recorder()[0]).run()

    assert res["ok"] is False
    assert "累计重复调用" in res["error"]
    assert res["turns"] == 4


async def test_repeat_call_feedback_omits_full_result(monkeypatch, tmp_path):
    """ok=True 的幂等重复调用第 2 次起占位回喂，完整结果不重复进上下文。"""
    ws = _make_ws(monkeypatch, tmp_path, "agent_dedup")
    monkeypatch.setattr(sa, "probe_duration", lambda p: 1.5)
    monkeypatch.setattr(
        sa,
        "transcribe",
        lambda p, model_size="small", language=None: [
            TranscribeSegment(text="hi", start=0.0, end=1.5, words=[])
        ],
    )
    llm = FakeLLM(
        [
            _action("transcribe"),
            _action("transcribe"),
            _action("prepare_assets", kind="frames"),  # 凑一轮，保证第 2 次 feedback 可断言
            _action("fail", reason="d 结束"),
        ]
    )
    await sa.StudioToolLoop(ws, llm, "", "whatever.mp4", _recorder()[0]).run()
    fb1 = _feedback_of(llm, 0)  # transcribe 首次：全量回喂
    fb2 = _feedback_of(llm, 1)  # transcribe 第二次：占位回喂
    assert fb1["result"].get("duration") == 1.5  # 首次全量回喂
    assert fb2["result"].get("repeated") is True  # 第二次占位
    assert "duration" not in fb2["result"]  # 无全量字段


async def test_transcribe_feedback_compact_words(monkeypatch, tmp_path):
    """transcribe 回喂：词级时间戳压缩为「词@秒」字符串流；落盘保持完整版。"""
    ws = _make_ws(monkeypatch, tmp_path, "agent_cw")

    def fake_transcribe(audio_path, model_size="small", language=None):
        return [
            TranscribeSegment(
                text="你好世界",
                start=0.0,
                end=2.0,
                words=[
                    SimpleNamespace(text="你好", start=0.0, end=0.8),
                    SimpleNamespace(text="世界", start=0.9, end=2.0),
                ],
            )
        ]

    monkeypatch.setattr(sa, "transcribe", fake_transcribe)
    monkeypatch.setattr(sa, "probe_duration", lambda p: 2.0)
    llm = FakeLLM([_action("transcribe"), _action("fail", reason="c 结束")])
    await sa.StudioToolLoop(ws, llm, "", "v.mp4", _recorder()[0]).run()
    seg = _feedback_of(llm, 1)["result"]["segments"][0]
    assert isinstance(seg["words"], str)
    assert "你好@0.00" in seg["words"] and "世界@0.90" in seg["words"]
    disk = json.loads((Path(ws["analysis"]) / "speech.json").read_text(encoding="utf-8"))
    assert disk["segments"][0]["words"][0]["end"] == 0.8  # 落盘仍是完整版
