"""Smart cut Skill — auto-remove silence, filler words, repeats from talking-head video.

设计依据：
  - ossclip analyze/produce 分离：首次调用只出规划，用户确认后才执行
  - fillercut 的两级填充词检测 + cutlist 参数（GAP_CUT/PAD/FILLER_PAUSE）
  - video-subtitle-cutter 的 ffmpeg 重编码 + concat 拼接
  - erm 的音频 afade 平滑拼接思想
  - 隐患 #5 对策：PTS 归零 + avoid_negative_ts make_zero
  - 隐患 #6 对策：视频 3 帧 fade + 音频 afade 双淡入淡出
  - 隐患 #13 对策：tempfile.TemporaryDirectory 上下文管理器

闸门机制（对齐 auto_short_video 的 refine_shots 模式）：
  - refine_candidates=None → 只返回删减规划，不执行剪辑
  - refine_candidates=[...] → 视为"用户已确认"，执行剪辑
  - refine_candidates=[] → 报错（空确认无意义）
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import shutil
import subprocess
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.core.skill_base import Skill
from app.core.skill_registry import register

logger = logging.getLogger(__name__)

_OUTPUT_DIR = Path(__file__).resolve().parent.parent.parent.parent / "outputs"

VIDEO_FADE_FRAMES = 3


class SmartCutInput(BaseModel):
    """智能剪辑输入参数。"""
    video_path: str = Field(description="视频文件路径")

    detect_silence: bool = Field(default=True, description="检测静音/长停顿")
    detect_filler: bool = Field(default=True, description="检测填充词/结巴")
    detect_repeat: bool = Field(default=True, description="检测重复/口误")
    detect_fluff: bool = Field(default=False, description="LLM 检测废话/离题（需 LLM 调用）")

    silence_threshold_db: float = Field(default=-35, description="静音阈值 dB，对齐 cutlist")
    silence_min_duration: float = Field(default=0.6, description="最短静音时长秒，对齐 cutlist GAP_CUT")
    filler_pause: float = Field(default=0.35, description="指示词类填充词两侧最短停顿秒，对齐 cutlist FILLER_PAUSE")
    pad_seconds: float = Field(default=0.25, description="切口两侧缓冲秒，对齐 cutlist PAD")
    audio_fade: float = Field(default=0.08, description="音频淡入淡出时长秒")

    refine_candidates: list[dict[str, Any]] | None = Field(
        default=None,
        description="用户确认/修改后的删减列表（闸门确认信号）。None=首次调用走规划。",
    )
    force_final: bool = Field(default=False, description="跳过预览直接出片")
    chain_with_overlay: bool = Field(default=False, description="串联模式：剪辑后自动调用 talking_head_overlay")


class SmartCutPlan(BaseModel):
    """删减规划 — 闸门产物。对齐 ossclip analyze 输出。"""
    video_id: str
    original_duration: float
    candidates: list[dict[str, Any]]
    kept_segments: list[list[float]]
    kept_duration: float
    saved_seconds: float
    needs_confirmation: bool = True


@asynccontextmanager
async def managed_temp_dir(prefix="smart_cut_"):
    """临时目录上下文管理器 — 隐患 #13 对策。

    Skill 结束时（无论成功/失败/异常）自动清理所有临时文件。
    对齐 Python 官方文档 tempfile.TemporaryDirectory 推荐用法。
    """
    tmp_dir = tempfile.mkdtemp(prefix=prefix)
    try:
        yield Path(tmp_dir)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        logger.debug(f"Cleaned up temp dir: {tmp_dir}")


async def _run_ffmpeg(cmd: list[str], cwd: str | None = None) -> str:
    """异步执行 ffmpeg 命令（对齐 video_compose.py 的 _run 模式）。

    cmd[0] 为字面量 "ffmpeg" 时先走 resolve_ffmpeg 解析链（PATH →
    FFMPEG_PATH → imageio-ffmpeg）：本机 ffmpeg 不在 PATH，裸执行会
    WinError 2（2026-10-29 10.20 真机验收实证）。
    """
    if cmd and cmd[0] == "ffmpeg":
        from capabilities.video.adapters.html_to_video import resolve_ffmpeg
        exe = resolve_ffmpeg()
        if exe:
            cmd = [exe, *cmd[1:]]
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout, stderr = await process.communicate()
        returncode = process.returncode
    except NotImplementedError:
        result = await asyncio.to_thread(
            subprocess.run, cmd, capture_output=True, cwd=cwd,
        )
        stdout, stderr, returncode = result.stdout, result.stderr, result.returncode

    if returncode != 0:
        tail = "\n".join((stderr.decode(errors="replace") or "").splitlines()[-12:])
        raise RuntimeError(f"ffmpeg 执行失败：\n{tail}")
    return stdout.decode(errors="replace")


@register
class SmartCutSkill(Skill):
    """智能剪辑：自动删减口播中的结巴/填充词/错误/空白，只保留核心内容。

    闸门机制（对齐 ossclip analyze/produce + auto_short_video refine_shots）：
      - 首次调用：只出删减规划（needs_confirmation=True），不执行剪辑
      - 用户确认后：带 refine_candidates 回调，执行剪辑
    """

    node_type = "edit"
    name = "smart_cut"
    display_name = "智能剪辑"
    description = "自动删减口播中的结巴/填充词/错误/空白，只保留核心内容"
    skill_category = "edit"
    skill_priority = "hot"
    trigger_words = ["智能剪辑", "删减空白", "去掉结巴", "精简视频", "去掉嗯啊", "剪口播"]
    input_schema = SmartCutInput

    async def execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        parsed = SmartCutInput(
            **{k: v for k, v in inputs.items() if k in SmartCutInput.model_fields}
        )
        user_id = inputs.get("_user_id", "default")
        video_path = parsed.video_path

        if not Path(video_path).is_file():
            return {"error": "file_not_found", "hint": f"视频文件不存在：{video_path}"}

        from capabilities.video.adapters.silence_detect import (
            has_audio_track,
            probe_duration,
        )

        if not await has_audio_track(video_path):
            return {"error": "no_audio_track", "hint": "视频无音轨，无法执行智能剪辑"}

        total_duration = await probe_duration(video_path)
        if total_duration <= 0:
            return {"error": "invalid_video", "hint": "无法获取视频时长"}

        if parsed.refine_candidates is not None:
            return await self._execute_cut(parsed, video_path, total_duration, user_id)
        else:
            return await self._generate_plan(parsed, video_path, total_duration, user_id)

    async def _generate_plan(
        self,
        parsed: SmartCutInput,
        video_path: str,
        total_duration: float,
        user_id: str,
    ) -> dict[str, Any]:
        """生成删减规划 — 对齐 ossclip analyze 阶段。"""
        from capabilities.video.services.cut_analyzer import (
            CutCandidate,
            CutType,
            compute_kept_segments,
            detect_filler_candidates,
            detect_repeat_candidates,
            detect_silence_candidates,
            merge_candidates,
            WordTimestamp,
        )

        all_candidates: list[CutCandidate] = []

        if parsed.detect_silence:
            from capabilities.video.adapters.silence_detect import (
                detect_silence_adaptive,
            )

            # 10.28：自适应档位（默认 -35dB 对高底噪素材恒 0 检出）；
            # 用户显式改过 threshold_db 时走旧单档
            if parsed.silence_threshold_db != -35 or (
                parsed.silence_min_duration != 0.6
            ):
                from capabilities.video.adapters.silence_detect import (
                    detect_silence,
                )

                silence_segs = await detect_silence(
                    video_path,
                    threshold_db=parsed.silence_threshold_db,
                    min_duration=parsed.silence_min_duration,
                )
            else:
                silence_segs, _tier = await detect_silence_adaptive(video_path)
            silence_cands = detect_silence_candidates(
                [(s.start, s.end, s.duration) for s in silence_segs]
            )
            all_candidates.extend(silence_cands)
            logger.info(f"silence detection: {len(silence_cands)} candidates")

        if parsed.detect_filler:
            words = await self._transcribe_words(video_path)
            if words:
                filler_cands = detect_filler_candidates(words, parsed.filler_pause)
                all_candidates.extend(filler_cands)
                logger.info(f"filler detection: {len(filler_cands)} candidates")

        if parsed.detect_repeat:
            words = await self._transcribe_words(video_path)
            if words:
                repeat_cands = detect_repeat_candidates(words)
                all_candidates.extend(repeat_cands)
                logger.info(f"repeat detection: {len(repeat_cands)} candidates")

        if parsed.detect_fluff:
            fluff_cands, fluff_raw = await self._detect_fluff(video_path, total_duration)
            all_candidates.extend(fluff_cands)
            logger.info(f"fluff detection: {len(fluff_cands)} candidates")

        merged = merge_candidates(all_candidates, total_duration)
        kept = compute_kept_segments(merged, total_duration)
        kept_duration = sum(e - s for s, e in kept)
        saved = total_duration - kept_duration

        candidates_data = [
            {
                "start": c.start,
                "end": c.end,
                "cut_type": c.cut_type.value,
                "confidence": c.confidence,
                "reason": c.reason,
                "text": c.text,
                "removable": c.removable,
            }
            for c in merged
        ]

        plan = SmartCutPlan(
            video_id=Path(video_path).stem,
            original_duration=total_duration,
            candidates=candidates_data,
            kept_segments=[[s, e] for s, e in kept],
            kept_duration=round(kept_duration, 2),
            saved_seconds=round(saved, 2),
            needs_confirmation=True,
        )

        return {
            "mode": "plan",
            "plan": plan.model_dump(),
            "needs_confirmation": True,
            "confirm_hint": (
                "删减规划已生成，视频尚未剪辑。请把规划展示给用户并等待确认或修改，"
                "再把最终 candidates 通过 refine_candidates 参数回传。"
                "未经用户确认不要自行剪辑。"
            ),
        }

    async def _execute_cut(
        self,
        parsed: SmartCutInput,
        video_path: str,
        total_duration: float,
        user_id: str,
    ) -> dict[str, Any]:
        """执行剪辑 — 对齐 ossclip produce 阶段。

        拼接策略（对齐业界实践 + 隐患对策）：
          - 视频：每段 3 帧 fade + concat demuxer（隐患 #6）
          - 音频：每段首尾 afade 消除咔嗒声（对齐 erm）
          - PTS 归零：-fflags +genpts + -avoid_negative_ts make_zero（隐患 #5）
          - 重编码：libx264 + aac（对齐 video-subtitle-cutter）
        """
        from capabilities.video.services.cut_analyzer import (
            CutCandidate,
            CutType,
            compute_kept_segments,
            merge_candidates,
        )

        confirmed_data = parsed.refine_candidates
        if not confirmed_data:
            return {"error": "empty_confirm", "hint": "refine_candidates 为空，无删减可执行"}

        confirmed = [
            CutCandidate(
                start=c["start"],
                end=c["end"],
                cut_type=CutType(c["cut_type"]),
                confidence=c["confidence"],
                reason=c["reason"],
                text=c.get("text", ""),
                removable=c.get("removable", True),
            )
            for c in confirmed_data
        ]

        merged = merge_candidates(confirmed, total_duration)
        kept = compute_kept_segments(merged, total_duration)

        if not kept:
            return {"error": "all_cut", "hint": "所有片段被标记删除，请取消部分勾选"}

        kept_duration = sum(e - s for s, e in kept)
        if kept_duration < 1.0:
            return {"error": "all_cut", "hint": f"保留时长仅 {kept_duration:.1f}s，请减少删减"}

        output_dir = _OUTPUT_DIR / user_id / "smart_cut"
        output_dir.mkdir(parents=True, exist_ok=True)
        # 产物隔离：同名源视频被不同会话按不同剪法处理时，不能落到同一个文件上互相覆盖。
        # 指纹取实际保留区间，同一剪法重跑仍复用同一文件名。
        _cut_key = hashlib.md5(
            "|".join(f"{s:.2f}-{e:.2f}" for s, e in kept).encode("utf-8")
        ).hexdigest()[:8]
        output_path = output_dir / f"cut_{Path(video_path).stem}_{_cut_key}.mp4"

        async with managed_temp_dir() as tmp_dir:
            segment_files = await self._extract_segments(
                video_path, kept, parsed.pad_seconds, parsed.audio_fade, tmp_dir
            )
            await self._concat_segments(segment_files, output_path, tmp_dir)

        if not output_path.is_file():
            return {"error": "ffmpeg_failed", "hint": "剪辑输出文件未生成"}

        # 10.27/10.28：报告口径对齐 pad 补偿（与 _extract_segments 同公式，
        # 含 per-gap 收缩）——cut_duration = 实际预期成片时长
        segs_sorted = sorted(kept)
        expected = 0.0
        for i, (s, e) in enumerate(segs_sorted):
            gap_l = (s - segs_sorted[i - 1][1]) / 2 if i > 0 else parsed.pad_seconds
            gap_r = (segs_sorted[i + 1][0] - e) / 2 if i + 1 < len(segs_sorted) else parsed.pad_seconds
            p = max(0.0, min(parsed.pad_seconds, gap_l, gap_r))
            expected += min(e + p, total_duration) - max(0, s - p)
        result = {
            "mode": "result",
            "output_path": str(output_path),
            "original_duration": total_duration,
            "cut_duration": round(expected, 2),
            "saved_seconds": round(total_duration - expected, 2),
            "cuts_applied": len(merged),
            "needs_confirmation": False,
        }

        if parsed.chain_with_overlay:
            result["kept_segments"] = [list(seg) for seg in kept]
            result["chain_next"] = "talking_head_overlay"

        return result

    async def _extract_segments(
        self,
        video_path: str,
        kept_segments: list[tuple[float, float]],
        pad: float,
        audio_fade: float,
        tmp_dir: Path,
    ) -> list[Path]:
        """逐段提取（含音视频双淡入淡出）。

        隐患 #5 对策：-fflags +genpts 归零 PTS
        隐患 #6 对策：视频 3 帧 fade + 音频 afade
        """
        segment_files: list[Path] = []

        # 10.28 live10 实证：删段 0.24s < pad 之和 0.5s → 相邻保留段
        # 尾头交叠 → concat 回填总长（54.09 ≈ 没剪）。per-gap 收缩：
        # 每段 pad_eff = min(pad, 左 gap/2, 右 gap/2)
        segs_sorted = sorted(kept_segments)
        eff_pads: list[float] = []
        for i, (s, e) in enumerate(segs_sorted):
            gap_l = (s - segs_sorted[i - 1][1]) / 2 if i > 0 else pad
            gap_r = (segs_sorted[i + 1][0] - e) / 2 if i + 1 < len(segs_sorted) else pad
            eff_pads.append(max(0.0, min(pad, gap_l, gap_r)))

        for i, (start, end) in enumerate(segs_sorted):
            p = eff_pads[i]
            s = max(0, start - p)
            # 10.27 live9 实证修复：pad 补偿 = (end+pad) - s_eff。
            # 旧式 end-start+2*pad 在首段 clamp 到 0 时仍加满 2*pad
            # （多算 0.25s），尾部段超界由 ffmpeg -t 截断无害
            duration = (end + p) - s
            seg_path = tmp_dir / f"seg_{i:03d}.mp4"

            video_fade_dur = VIDEO_FADE_FRAMES / 30.0
            vfade_in = f"fade=t=in:st=0:d={video_fade_dur:.4f}"
            vfade_out = f"fade=t=out:st={max(0, duration - video_fade_dur):.4f}:d={video_fade_dur:.4f}"

            afade_in = f"afade=t=in:st=0:d={audio_fade}"
            afade_out = f"afade=t=out:st={max(0, duration - audio_fade):.4f}:d={audio_fade}"

            cmd = [
                "ffmpeg",
                "-ss", f"{s:.3f}",
                "-i", video_path,
                "-t", f"{duration:.3f}",
                "-vf", f"{vfade_in},{vfade_out}",
                "-af", f"{afade_in},{afade_out}",
                "-c:v", "libx264",
                "-preset", "fast",
                "-crf", "23",
                "-c:a", "aac",
                "-pix_fmt", "yuv420p",
                "-fflags", "+genpts",
                "-y", str(seg_path),
            ]
            await _run_ffmpeg(cmd)
            segment_files.append(seg_path)

        return segment_files

    async def _concat_segments(
        self,
        segment_files: list[Path],
        output_path: Path,
        tmp_dir: Path,
    ) -> None:
        """concat demuxer 拼接 — 隐患 #5 对策：avoid_negative_ts make_zero。"""
        concat_file = tmp_dir / "concat.txt"
        concat_file.write_text(
            "\n".join(f"file '{f}'" for f in segment_files),
            encoding="utf-8",
        )

        cmd = [
            "ffmpeg", "-f", "concat", "-safe", "0",
            "-i", str(concat_file),
            "-avoid_negative_ts", "make_zero",
            "-c", "copy",
            "-y", str(output_path),
        ]
        await _run_ffmpeg(cmd)

    async def _transcribe_words(self, video_path: str) -> list:
        """转录获取逐词时间戳。

        零件可缺：WhisperX/stable-ts 没装 → 返回空列表，填充词/重复检测自动跳过。
        隐患 #1 对策：启动时做 CUDA 可用性检查。
        使用 whisper_transcribe adapter（stable-ts → faster-whisper 降级链）。
        """
        try:
            from capabilities.video.adapters.whisper_transcribe import (
                extract_words,
                transcribe,
            )
            from capabilities.video.services.cut_analyzer import WordTimestamp

            segments = transcribe(video_path, model_size="small", language="zh")
            if not segments:
                return []

            raw_words = extract_words(segments)
            words = [
                WordTimestamp(
                    text=w.text,
                    start=w.start,
                    end=w.end,
                    probability=w.probability,
                )
                for w in raw_words
            ]
            logger.info(f"transcribed {len(words)} words from {video_path}")
            return words

        except Exception as e:
            logger.warning(f"transcription failed, skipping word-level detection: {e}")
            return []

    async def _detect_fluff(
        self,
        video_path: str,
        total_duration: float,
    ) -> tuple[list, list]:
        """LLM 语义分析检测废话/离题 — 隐患 #9/#10 对策。

        零件可缺：LLM 不可用 → 返回空列表。
        """
        try:
            from capabilities.video.adapters.whisper_transcribe import transcribe
            from capabilities.video.services.cut_analyzer import detect_fluff_candidates

            segments = transcribe(video_path, model_size="small", language="zh")
            if not segments:
                return [], []

            seg_dicts = [
                {"index": i, "start": s.start, "end": s.end, "text": s.text}
                for i, s in enumerate(segments)
            ]

            # 10.29 live11 实证修复：原 from app.engine.llm_client import
            # get_llm——该模块不存在，fluff 检测从写下起从未跑通（与
            # has_audio_track 同病）。对齐 talking_head_overlay._resolve_llm
            from app.adapters.deepseek import DeepSeekAdapter
            from app.config import Settings
            llm = DeepSeekAdapter(api_key=Settings().deepseek_api_key)

            return await detect_fluff_candidates(seg_dicts, llm)

        except Exception as e:
            logger.warning(f"LLM fluff detection failed: {e}")
            return [], []