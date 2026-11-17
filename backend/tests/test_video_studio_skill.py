"""video_studio 入口 skill 测试（Video Studio P2 Task 4）。

FakeLLM 按脚本回放（不调真 LLM）；transcribe/render/review/preview 在
studio_agent 模块命名空间内 monkeypatch——skill 层逻辑（工作区创建、打法装载、
LLM resolve、StudioToolLoop 驱动、SSE 进度透传）真实跑。
"""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import app.services.sse_bus as sse_mod
from capabilities.video.adapters.whisper_transcribe import TranscribeSegment
from capabilities.video.services import studio_agent as sa
from capabilities.video.services import studio_workspace as sw
from capabilities.video.skills.studio_methods import load_method
from capabilities.video.skills.video_studio import VideoStudioInput, VideoStudioSkill

_METHOD_TEXT = (
    "打法（测试夹具）：口播两拍。第一拍开场点题，第二拍收尾。\n"
    "自查纪律：render → review，errors 为空才 finish。"
)
_BRIEF = "节奏收紧，别拖沓"

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
    '<div id="p" style="position:absolute;left:0;top:0;width:10px;height:10px"></div>'
    '<script>window.seekTo=(t)=>{document.getElementById("p").style.left=(t*100)+"px";};'
    "window.seekTo(0);</script>"
)

_PREVIEW_URL = "http://127.0.0.1:54321/"


class FakeLLM:
    """按脚本回放：每次 chat 弹出一条预置回复；记录调用供断言。"""

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


def _make_video(tmp_path, with_audio=True):
    from capabilities.video.adapters.html_to_video import resolve_ffmpeg

    src = tmp_path / "src.mp4"
    cmd = [resolve_ffmpeg(), "-y", "-f", "lavfi", "-i", "testsrc=duration=1.0:size=320x568:rate=8"]
    if with_audio:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-c:a", "aac"]
    cmd += ["-t", "1.0", "-pix_fmt", "yuv420p", str(src)]
    subprocess.run(cmd, check=True, capture_output=True)
    return src


def _install_stubs(monkeypatch, review_result=None):
    """在 studio_agent 命名空间内钉 whisper/渲染/质检/预览。"""

    def fake_transcribe(audio_path, model_size="small", language=None):
        return [TranscribeSegment(text="你好世界", start=0.0, end=1.0, words=[])]

    def fake_render(scene_html, frames_dir, duration, fps, width, height, audio_path, out_video):
        Path(out_video).write_bytes(b"fake mp4")
        return SimpleNamespace(count=8, fps=float(fps), elapsed=0.1)

    review = review_result or {"errors": [], "loudness": {"rms": -20.0, "peak": -12.0}}

    def fake_review(ws_, tl, video):
        return review

    class FakePreviewServer:
        def __init__(self, ws_, port=0):
            pass

        def start(self):
            return _PREVIEW_URL

        def stop(self):
            pass

    monkeypatch.setattr(sa, "transcribe", fake_transcribe)
    monkeypatch.setattr(sa, "probe_duration", lambda p: 1.0)
    monkeypatch.setattr(sa, "render_scene", fake_render)
    monkeypatch.setattr(sa, "review_workspace", fake_review)
    monkeypatch.setattr(sa, "StudioPreviewServer", FakePreviewServer)


def _install_sse_recorder(monkeypatch):
    events = []

    async def fake_publish(workflow_id, event_type, payload, turn_id=None):
        events.append(
            {"workflow_id": workflow_id, "event_type": event_type, "turn_id": turn_id, **payload}
        )

    monkeypatch.setattr(sse_mod.sse_bus, "publish", fake_publish)
    return events


def _action(action, **args):
    return json.dumps({"action": action, "args": args}, ensure_ascii=False)


_SUCCESS_SCRIPT = [
    _action("list_rules"),
    _action("read_rules_file", path="method.md"),
    _action("transcribe"),
    _action("save_timeline", timeline=_TL),
    _action("write_scene_file", path="scene/index.html", content=_SCENE_HTML),
    _action("render"),
    _action("start_preview"),
    _action("review"),
    _action("finish", summary="交付"),
]


async def test_success_path_e2e(monkeypatch, tmp_path):
    monkeypatch.setattr(sw, "_STUDIO_ROOT", tmp_path)
    _install_stubs(monkeypatch)
    events = _install_sse_recorder(monkeypatch)
    llm = FakeLLM(_SUCCESS_SCRIPT)

    params = {
        "video_path": str(_make_video(tmp_path)),
        "method": {"text": _METHOD_TEXT},
        "brief": _BRIEF,
        "llm": llm,
        "_user_id": "u1",
        "_workflow_id": "wf-studio-test",
        "_turn_id": "turn-1",
    }
    chunks = [c async for c in VideoStudioSkill().execute(params)]
    assert len(chunks) == 1, chunks
    chunk = chunks[0]

    # ① 交付载荷
    assert chunk["success"] is True
    assert chunk["video_path"].endswith("final.mp4")
    assert Path(chunk["video_path"]).is_file()
    assert Path(chunk["workspace"]).is_dir()
    assert chunk["review_summary"]["error_count"] == 0
    assert "loudness" in chunk["review_summary"]
    assert chunk["preview_url"] == _PREVIEW_URL
    # 打法与 brief 都落入 rules/（agent 经 list_rules/read_rules_file 可读）
    rules = Path(chunk["workspace"]) / "inputs" / "rules"
    assert (rules / "method.md").read_text(encoding="utf-8") == _METHOD_TEXT
    assert (rules / "brief.md").read_text(encoding="utf-8") == _BRIEF
    # FakeLLM 全程走 JSON 协议
    assert all(c["response_format"] == {"type": "json_object"} for c in llm.calls)

    # ② 进度事件：skill/video_studio + 阶段集合 + preview_url 透传
    assert events, "未发出任何进度事件"
    assert all(e["workflow_id"] == "wf-studio-test" for e in events)
    assert all(e["event_type"] == "video_pipeline_progress" for e in events)
    assert all(e["skill"] == "video_studio" for e in events)
    phases = [e["phase"] for e in events]
    for ph in ("prepare", "method", "plan", "scene", "render", "review", "deliver"):
        assert ph in phases, phases
    assert phases[0] == "prepare" and phases[-1] == "deliver"
    assert all("total" in e and "percent" in e for e in events)
    with_preview = [e for e in events if "preview_url" in e]
    assert with_preview, "preview_url 未透传到任何进度事件"
    assert all(e["preview_url"] == _PREVIEW_URL for e in with_preview)


async def test_fail_path_yields_error_and_fail_phase(monkeypatch, tmp_path):
    monkeypatch.setattr(sw, "_STUDIO_ROOT", tmp_path)
    _install_stubs(monkeypatch)
    events = _install_sse_recorder(monkeypatch)
    llm = FakeLLM([_action("fail", reason="视频源无法解析")])

    chunks = [
        c
        async for c in VideoStudioSkill().execute(
            {
                "video_path": str(_make_video(tmp_path)),
                "method": {"text": _METHOD_TEXT},
                "llm": llm,
                "_workflow_id": "wf-fail",
            }
        )
    ]

    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk["success"] is False
    assert "视频源无法解析" in chunk["error"]
    phases = [e["phase"] for e in events]
    assert "fail" in phases and "deliver" not in phases, phases


async def test_missing_video_yields_failure_without_llm(monkeypatch, tmp_path):
    monkeypatch.setattr(sw, "_STUDIO_ROOT", tmp_path)
    chunks = [
        c
        async for c in VideoStudioSkill().execute(
            {
                "video_path": str(tmp_path / "nope.mp4"),
            }
        )
    ]
    assert len(chunks) == 1 and chunks[0]["success"] is False
    assert "not found" in chunks[0]["error"]


async def test_bad_method_ref_yields_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(sw, "_STUDIO_ROOT", tmp_path)
    chunks = [
        c
        async for c in VideoStudioSkill().execute(
            {
                "video_path": str(_make_video(tmp_path)),
                "method": {"builtin": "no_such_method"},
                "llm": FakeLLM([]),
                "_workflow_id": "wf-bad-method",
            }
        )
    ]
    assert chunks[0]["success"] is False
    assert "no_such_method" in chunks[0]["error"]


async def test_input_schema_defaults():
    parsed = VideoStudioInput(video_path="x.mp4")
    assert parsed.method == {"builtin": "general_talk"}
    assert parsed.brief == ""
    # text 装载路径走 Task 2 的 load_method
    loaded = load_method({"text": _METHOD_TEXT})
    assert loaded == {"name": "inline", "text": _METHOD_TEXT, "source": "text"}


async def test_skill_registered_in_registry():
    from app.core.skill_registry import SkillRegistry, ensure_builtin_skills_registered

    ensure_builtin_skills_registered()
    cls = SkillRegistry.instance().get("edit", "video_studio")
    assert cls is VideoStudioSkill
    meta = cls.metadata()
    assert meta["display_name"] == "AI 口播工作室"
    assert meta["node_type"] == "edit"
