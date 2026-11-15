"""视频转文字 Skill — 把 whisper 转写以技能形式暴露给聊天 agent。

背景（2026-11-02 用户实测）：agent 想给视频转文字只能走 bash 跑 whisper CLI，
被 dev_tools ALLOWED_BINARIES 白名单拦截（白名单有意不放 python/whisper，
见 ROADMAP_v2_desktop P0-1 安全决策）。本技能走 skill_call 通路，
agent 可直接拿到带时间戳的转写文本，并落盘 .txt/.srt 供 file_read 复读。

子进程隔离（对齐 talking_head_overlay._transcribe_cpu_subprocess 的 10.20
对照实验实锤）：ct2 构造 WhisperModel（即使 device=cpu）会把系统旧 cuDNN
DLL 拉进当前进程，同进程后续 torch CUDA native fastfail 不可捕获 —
转录必须跑独立子进程 + WHISPER_DEVICE=cpu。
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.core.skill_base import Skill
from app.core.skill_registry import register

logger = logging.getLogger(__name__)

# 转写引擎（faster-whisper small）CPU 跑长视频偏慢，给足余量
_TRANSCRIBE_TIMEOUT_S = 900.0
# 内联输出护栏：全文/片段列表超长时截断，完整内容看落盘文件
_TEXT_INLINE_CAP = 6000
_SEGMENTS_INLINE_CAP = 80


class VideoTranscribeInput(BaseModel):
    video_path: str = Field(description="本地视频/音频文件路径（建议绝对路径）")
    language: str = Field(
        default="zh", description="语言代码：zh=中文，auto=自动检测"
    )
    model_size: str = Field(
        default="small", description="模型大小：tiny/base/small/medium"
    )
    with_words: bool = Field(
        default=False, description="是否返回逐词时间戳（payload 会大很多）"
    )


def _run_transcribe_subprocess(
    video_path: str,
    model_size: str = "small",
    language: str | None = "zh",
    timeout: float = _TRANSCRIBE_TIMEOUT_S,
) -> list[dict[str, Any]]:
    """独立子进程跑 whisper_transcribe.transcribe，返回可 JSON 序列化的片段行。

    失败/超时/引擎缺失 → []（转写是可降级能力，不抛异常阻断调用方）。
    """
    import os

    backend_root = Path(__file__).resolve().parents[3]
    code = (
        "import json,sys;sys.path.insert(0, sys.argv[2]);"
        "from capabilities.video.adapters.whisper_transcribe import transcribe;"
        "segs = transcribe(sys.argv[1], sys.argv[3],"
        " sys.argv[4] if sys.argv[4] != '-' else None);"
        "print(json.dumps([{'start': s.start, 'end': s.end, 'text': s.text,"
        " 'words': [{'text': w.text, 'start': w.start, 'end': w.end}"
        " for w in (s.words or [])]} for s in segs], ensure_ascii=False))"
    )
    env = {**os.environ, "WHISPER_DEVICE": "cpu"}
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code, video_path, str(backend_root),
             model_size, language or "-"],
            capture_output=True, timeout=timeout, env=env,
            creationflags=creationflags,
        )
    except subprocess.TimeoutExpired:
        logger.warning("video_transcribe subprocess timed out (%ss)", timeout)
        return []
    if proc.returncode != 0:
        logger.warning(
            "video_transcribe subprocess failed: %s",
            proc.stderr.decode(errors="replace")[-300:],
        )
        return []
    try:
        line = proc.stdout.decode("utf-8", errors="replace").strip()
        rows = json.loads(line.splitlines()[-1]) if line else []
    except (json.JSONDecodeError, IndexError):
        logger.warning("video_transcribe subprocess output unparsable")
        return []
    return rows if isinstance(rows, list) else []


def _fmt_srt_ts(t: float) -> str:
    ms = int(round(max(0.0, float(t)) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _build_srt(segments: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for i, seg in enumerate(segments, 1):
        lines.append(str(i))
        lines.append(f"{_fmt_srt_ts(seg['start'])} --> {_fmt_srt_ts(seg['end'])}")
        lines.append(str(seg["text"]))
        lines.append("")
    return "\n".join(lines)


@register
class VideoTranscribeSkill(Skill):
    node_type = "edit"
    name = "video_transcribe"
    display_name = "视频转文字"
    description = (
        "把本地视频/音频转成文字（whisper 本地推理，带句级时间戳，可选逐词）。"
        "转写文本与 SRT 字幕落盘到视频同目录，可直接引用。"
        "适用于：口播稿提取、字幕制作、剪辑前的内容确认。"
    )
    skill_category = "edit"
    skill_priority = "hot"
    trigger_words = ["转文字", "转写", "提取口播稿", "出字幕", "语音识别"]
    input_schema = VideoTranscribeInput
    prompt_guidance = (
        "video_transcribe：传 video_path（绝对路径）。language 默认 zh，"
        "auto=自动检测。返回全文 text + 句级 segments，并在视频同目录落盘 "
        "<文件名>_transcript.txt 与 <文件名>.srt。长视频 CPU 转写较慢（可能数分钟）。"
    )

    async def execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        params = VideoTranscribeInput.model_validate({
            k: v for k, v in inputs.items() if k in VideoTranscribeInput.model_fields
        })

        video = Path(params.video_path).expanduser()
        if not video.is_file():
            return {
                "ok": False,
                "error": f"视频文件不存在: {video}",
                "hint": "请传本地视频/音频的绝对路径（工作区里的文件用 workspace 给出的绝对路径）。",
            }

        rows = await asyncio.to_thread(
            _run_transcribe_subprocess,
            str(video), params.model_size,
            None if params.language.strip().lower() == "auto" else params.language,
        )
        if not rows:
            return {
                "ok": False,
                "error": (
                    "转写结果为空：视频可能没有音轨，或本机缺少转写引擎"
                    "（stable-ts / faster-whisper / openai-whisper 任一）。"
                ),
            }

        segments = [
            {
                "start": round(float(r.get("start", 0)), 3),
                "end": round(float(r.get("end", 0)), 3),
                "text": str(r.get("text", "")),
                **({"words": r.get("words") or []} if params.with_words else {}),
            }
            for r in rows
        ]
        full_text = "\n".join(seg["text"] for seg in segments if seg["text"])

        # 落盘 .txt/.srt 到视频同目录（失败不阻断，内联结果仍然返回）
        transcript_txt = srt_path = None
        try:
            txt_file = video.parent / f"{video.name}_transcript.txt"
            srt_file = video.with_suffix(".srt")
            txt_file.write_text(full_text + "\n", encoding="utf-8")
            srt_file.write_text(_build_srt(segments), encoding="utf-8")
            transcript_txt, srt_path = str(txt_file), str(srt_file)
        except OSError as e:
            logger.warning("video_transcribe 落盘失败（不阻断）: %s", e)

        inline_text = full_text
        text_truncated = False
        if len(inline_text) > _TEXT_INLINE_CAP:
            inline_text = inline_text[:_TEXT_INLINE_CAP]
            text_truncated = True

        return {
            "ok": True,
            "video_path": str(video),
            "duration": segments[-1]["end"] if segments else 0.0,
            "segment_count": len(segments),
            "text": inline_text,
            "text_truncated": text_truncated,
            "segments": segments[:_SEGMENTS_INLINE_CAP],
            "segments_truncated": len(segments) > _SEGMENTS_INLINE_CAP,
            "transcript_txt": transcript_txt,
            "srt_path": srt_path,
        }
