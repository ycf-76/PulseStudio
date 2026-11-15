"""video_transcribe 技能测试 — 转写能力以 Skill 形式暴露给聊天 agent。

背景（2026-11-02 用户实测）：agent 想给视频转文字只能走 bash 跑 whisper CLI，
被 dev_tools ALLOWED_BINARIES 白名单拦截；新增 skill_call 通路。
子进程隔离对齐 talking_head_overlay 的 10.20 实锤（ct2 拉旧 cuDNN DLL 进程炸 torch CUDA）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from capabilities.video.skills import video_transcribe as vt


def _fake_rows() -> list[dict]:
    return [
        {
            "start": 0.0, "end": 1.5, "text": "大家好",
            "words": [{"text": "大家好", "start": 0.0, "end": 1.5}],
        },
        {
            "start": 1.6, "end": 3.2, "text": "今天聊聊剪辑",
            "words": [
                {"text": "今天", "start": 1.6, "end": 2.2},
                {"text": "聊聊", "start": 2.3, "end": 2.8},
                {"text": "剪辑", "start": 2.8, "end": 3.2},
            ],
        },
    ]


@pytest.fixture
def fake_video(tmp_path: Path, monkeypatch) -> Path:
    video = tmp_path / "口播素材.mp4"
    video.write_bytes(b"fake")
    monkeypatch.setattr(vt, "_run_transcribe_subprocess", lambda *a, **k: _fake_rows())
    return video


async def test_video_not_found(tmp_path: Path):
    skill = vt.VideoTranscribeSkill()
    result = await skill.execute({"video_path": str(tmp_path / "不存在.mp4")})
    assert result["ok"] is False
    assert "不存在" in result["error"]


async def test_transcribe_success(fake_video: Path):
    skill = vt.VideoTranscribeSkill()
    result = await skill.execute({"video_path": str(fake_video)})

    assert result["ok"] is True
    assert result["segment_count"] == 2
    assert result["duration"] == 3.2
    assert "大家好" in result["text"] and "今天聊聊剪辑" in result["text"]
    # 默认不带逐词时间戳（payload 控制）
    assert all("words" not in seg for seg in result["segments"])
    # 转写文件落在视频同目录，agent 可用 file_read 直接读
    txt = Path(result["transcript_txt"])
    srt = Path(result["srt_path"])
    assert txt.is_file() and srt.is_file()
    assert "大家好" in txt.read_text(encoding="utf-8")
    srt_text = srt.read_text(encoding="utf-8")
    assert "00:00:00,000 --> 00:00:01,500" in srt_text
    assert "今天聊聊剪辑" in srt_text


async def test_with_words(fake_video: Path):
    skill = vt.VideoTranscribeSkill()
    result = await skill.execute({"video_path": str(fake_video), "with_words": True})
    assert result["ok"] is True
    assert result["segments"][1]["words"][0]["text"] == "今天"


async def test_empty_segments(fake_video: Path, monkeypatch):
    monkeypatch.setattr(vt, "_run_transcribe_subprocess", lambda *a, **k: [])
    skill = vt.VideoTranscribeSkill()
    result = await skill.execute({"video_path": str(fake_video)})
    assert result["ok"] is False
    assert "音轨" in result["error"] or "引擎" in result["error"]


async def test_language_auto_passes_none(fake_video: Path, monkeypatch):
    captured: dict = {}

    def _spy(video_path, model_size="small", language="zh", timeout=900.0):
        captured["language"] = language
        return _fake_rows()

    monkeypatch.setattr(vt, "_run_transcribe_subprocess", _spy)
    skill = vt.VideoTranscribeSkill()
    result = await skill.execute({"video_path": str(fake_video), "language": "auto"})
    assert result["ok"] is True
    assert captured["language"] is None


def test_srt_timestamp_format():
    assert vt._fmt_srt_ts(0.0) == "00:00:00,000"
    assert vt._fmt_srt_ts(3661.5) == "01:01:01,500"
