"""Whisper transcription adapter — word-level timestamp extraction.

设计依据：
  - ossclip 用 whisper-cpp（本地 C++ 推理）
  - stable-ts 的 VAD + suppress_silence 时间戳修正（隐患 #1/#3 对策）
  - 零件可缺：没装 stable-ts/faster-whisper → 返回空列表，不阻断流程

核心功能：
  - 从视频/音频提取逐词时间戳
  - 支持 stable-ts（推荐）和 faster-whisper（备选）
  - CUDA 可用性检查（隐患 #1 对策）
  - 降级策略：GPU → CPU → 空列表
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# P2 验收实证：无引导时 whisper 会把普通话口播转成繁体并出同音错字
# （「入夏」→「入蝦」）。initial_prompt 引导解码走向简体普通话词分布，
# 三条引擎链（stable-ts / faster-whisper / openai-whisper）均注入。
_INITIAL_PROMPT = "以下是普通话口播内容，请用简体中文转写。"


@dataclass(frozen=True)
class TranscribeWord:
    """单个词的时间戳。"""
    text: str
    start: float
    end: float
    probability: float = 1.0


@dataclass(frozen=True)
class TranscribeSegment:
    """一个语音片段（句子级）。"""
    text: str
    start: float
    end: float
    words: list[TranscribeWord] = field(default_factory=list)


def check_cuda_available() -> dict[str, Any]:
    """检查 CUDA 环境，返回诊断信息 — 隐患 #1 对策。

    启动时调用，提前暴露问题而非运行时崩溃。
    """
    try:
        import torch
        info: dict[str, Any] = {
            "cuda_available": torch.cuda.is_available(),
            "cuda_version": torch.version.cuda if torch.cuda.is_available() else None,
            "cudnn_version": torch.backends.cudnn.version() if torch.cuda.is_available() else None,
            "gpu_name": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        }
        if not info["cuda_available"]:
            info["fallback"] = "CPU only mode (4x slower)"
        return info
    except ImportError:
        return {"cuda_available": False, "fallback": "torch not installed"}


def _try_stable_ts(
    audio_path: str,
    model_size: str = "small",
    language: str | None = None,
) -> list[TranscribeSegment] | None:
    """尝试用 stable-ts 转录 — 隐患 #1/#3 对策。

    stable-ts 的核心优势：
      - Silero VAD 预处理：去掉静音段，减少幻觉词
      - suppress_silence=True：时间戳向有声区域收缩
      - 时间戳精度从 100-200ms 降到 40-60ms
    """
    try:
        import stable_whisper
    except ImportError:
        logger.debug("stable-ts not installed, skipping")
        return None

    try:
        model = stable_whisper.load_model(model_size)
        kwargs: dict[str, Any] = {
            "vad": True,
            "suppress_silence": True,
            "vad_threshold": 0.35,
        }
        if language:
            kwargs["language"] = language
        kwargs["initial_prompt"] = _INITIAL_PROMPT

        result = model.transcribe(audio_path, **kwargs)

        segments = []
        for seg in result.segments:
            words = []
            for word in seg.words:
                words.append(TranscribeWord(
                    text=word.word.strip(),
                    start=word.start,
                    end=word.end,
                    probability=getattr(word, "probability", 1.0),
                ))
            segments.append(TranscribeSegment(
                text=seg.text.strip(),
                start=seg.start,
                end=seg.end,
                words=words,
            ))
        logger.info(f"stable-ts: transcribed {len(segments)} segments, {sum(len(s.words) for s in segments)} words")
        return segments

    except Exception as e:
        logger.warning(f"stable-ts transcription failed: {e}")
        return None


def resolve_model_path(model_size: str) -> str:
    """模型解析链：FASTER_WHISPER_MODEL_PATH 环境变量 → ModelScope 缓存 → 原样（HF）。

    HF 直连不可达的网络环境下，faster-whisper 模型可从 ModelScope 预下载：
      modelscope download --model angelala00/faster-whisper-small
    """
    import os

    env_path = os.environ.get("FASTER_WHISPER_MODEL_PATH")
    if env_path and Path(env_path).is_dir():
        return env_path

    # ModelScope 缓存（snapshot_download 布局）
    ms_root = Path.home() / ".cache" / "modelscope" / "models" / f"angelala00--faster-whisper-{model_size}"
    if ms_root.is_dir():
        snapshots = sorted(ms_root.glob("snapshots/*"))
        for snap in snapshots:
            if (snap / "model.bin").is_file():
                return str(snap)

    return model_size


def _try_faster_whisper(
    audio_path: str,
    model_size: str = "small",
    language: str | None = None,
) -> list[TranscribeSegment] | None:
    """尝试用 faster-whisper 转录（备选方案）。"""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        logger.debug("faster-whisper not installed, skipping")
        return None

    try:
        # WHISPER_DEVICE=cpu 逃生门：ct2 与系统旧 cuDNN DLL 冲突时
        # native fastfail（0xC0000409）不可捕获，进程直接死
        # （2026-10-29 10.20 真机实证 cudnnGetLibConfig Error 127）
        import os

        forced = os.environ.get("WHISPER_DEVICE", "").strip().lower()
        cuda_info = check_cuda_available()
        device = (
            forced
            if forced in ("cpu", "cuda")
            else ("cuda" if cuda_info.get("cuda_available") else "cpu")
        )
        compute_type = "float16" if device == "cuda" else "int8"

        model = WhisperModel(
            resolve_model_path(model_size), device=device, compute_type=compute_type
        )
        kwargs: dict[str, Any] = {
            "word_timestamps": True,
            "vad_filter": True,
            "vad_parameters": {"min_silence_duration_ms": 500},
        }
        if language:
            kwargs["language"] = language
        kwargs["initial_prompt"] = _INITIAL_PROMPT

        segments_iter, info = model.transcribe(audio_path, **kwargs)

        segments = []
        for seg in segments_iter:
            words = []
            if seg.words:
                for word in seg.words:
                    words.append(TranscribeWord(
                        text=word.word.strip(),
                        start=word.start,
                        end=word.end,
                        probability=getattr(word, "probability", 1.0),
                    ))
            segments.append(TranscribeSegment(
                text=seg.text.strip(),
                start=seg.start,
                end=seg.end,
                words=words,
            ))
        logger.info(f"faster-whisper: transcribed {len(segments)} segments on {device}")
        return segments

    except Exception as e:
        logger.warning(f"faster-whisper transcription failed: {e}")
        return None


def _try_openai_whisper(
    audio_path: str,
    model_size: str = "small",
    language: str | None = None,
) -> list[TranscribeSegment] | None:
    """尝试用 openai-whisper 转录（第三备选，走 PyTorch hub 非 HuggingFace）。"""
    try:
        import whisper
    except ImportError:
        logger.debug("openai-whisper not installed, skipping")
        return None

    try:
        model = whisper.load_model(model_size)
        kwargs: dict[str, Any] = {"word_timestamps": True}
        if language:
            kwargs["language"] = language
        kwargs["initial_prompt"] = _INITIAL_PROMPT

        result = model.transcribe(audio_path, **kwargs)

        segments = []
        for seg in result.get("segments", []):
            words = []
            for word in seg.get("words", []):
                words.append(TranscribeWord(
                    text=word.get("word", "").strip(),
                    start=word.get("start", 0),
                    end=word.get("end", 0),
                    probability=word.get("probability", 1.0),
                ))
            segments.append(TranscribeSegment(
                text=seg.get("text", "").strip(),
                start=seg.get("start", 0),
                end=seg.get("end", 0),
                words=words,
            ))
        logger.info(f"openai-whisper: transcribed {len(segments)} segments")
        return segments

    except Exception as e:
        logger.warning(f"openai-whisper transcription failed: {e}")
        return None


def has_audio_stream(video_path: str) -> bool:
    """检查媒体文件是否含音频流 — 无音频时转录必然为空，提前显式暴露。"""
    try:
        import av
        with av.open(video_path) as container:
            return len(container.streams.audio) > 0
    except Exception as e:
        logger.warning(f"audio stream probe failed for {video_path}: {e}")
        return True  # 探测失败不阻断，交给下游引擎自己报错


def transcribe(
    audio_path: str,
    model_size: str = "small",
    language: str | None = None,
) -> list[TranscribeSegment]:
    """转录音频/视频，返回逐词时间戳。

    降级策略（零件可缺）：
      1. stable-ts（推荐，VAD + 时间戳修正）
      2. faster-whisper（备选）
      3. openai-whisper（第三备选，走 PyTorch hub）
      4. 返回空列表（不阻断流程，静音检测仍可用）

    Args:
        audio_path: 音频/视频文件路径
        model_size: 模型大小（tiny/base/small/medium/large）
        language: 语言代码（None=自动检测，"zh"=中文）

    Returns:
        转录片段列表（含逐词时间戳）
    """
    if not Path(audio_path).is_file():
        logger.warning(f"audio file not found: {audio_path}")
        return []

    if not has_audio_stream(audio_path):
        logger.warning(
            f"NO AUDIO STREAM in {audio_path} — 转录跳过。"
            f"请确认视频含音轨（无声视频无法做口播剪辑）。"
        )
        return []

    result = _try_stable_ts(audio_path, model_size, language)
    if result is not None:
        return result

    result = _try_faster_whisper(audio_path, model_size, language)
    if result is not None:
        return result

    result = _try_openai_whisper(audio_path, model_size, language)
    if result is not None:
        return result

    logger.info("no transcription engine available, returning empty segments")
    return []


def extract_words(segments: list[TranscribeSegment]) -> list[TranscribeWord]:
    """从片段列表中提取所有词的时间戳。"""
    words = []
    for seg in segments:
        words.extend(seg.words)
    return words