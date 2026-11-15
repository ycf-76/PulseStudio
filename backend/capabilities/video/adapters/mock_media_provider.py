"""Mock Media Provider：阶段 0 零成本占位实现。

设计依据：docs/VIDEO_ARCHITECTURE_ALIGNMENT_MIGRATION_PLAN.md §6 阶段 0
（"分镜图返回占位图，视频返回占位片段，全链路可点通，零成本"）

行为：
  - generate_storyboard：本地规则拆镜（不调 LLM），输出与真实 prompt 契约一致的
    分镜 JSON（含 shot_id），保证阶段 1 替换真实 LLM 时调用方零改动；
  - generate_shot_image：PIL 生成带镜头序号的占位 PNG（分辨率跟随 ratio）；
  - generate_shot_video：ffmpeg lavfi 生成 3-5s 纯色占位 MP4（颜色由 shot_id
    哈希决定，保证同一镜头重生成视觉一致、不同镜头可区分）；
  - compose（handler 层）：占位片段走【真实】compose_video_concat——阶段 0 就
    验证 ffmpeg 拼接路径（xfade/concat、音轨归一化），而不是把拼接也 mock 掉；
  - 失败率可调（settings.video_mock_failure_rate），联调重试/降级路径用。

契约一致性：同一 job_input 在 mock 与真实 provider 下的状态机路径一致
（成功走 succeeded + result_asset_id；失败抛异常走 failed），CI 可加契约用例。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import random
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

logger = logging.getLogger(__name__)

_RATIO_SIZE = {
    "9:16": (720, 1280),
    "16:9": (1280, 720),
    "1:1": (1024, 1024),
}

_MOCK_MOTIONS = ["缓慢推进", "横向跟移", "微俯缓推", "低机位跟拍", "匀速横移", "拉远", "环绕"]
_MOCK_CAPTIONS = ["开场引入", "场景展开", "细节特写", "人物互动", "氛围营造", "情绪推进", "收束回味"]


def _shot_color(shot_id: str) -> tuple[int, int, int]:
    """shot_id → 稳定 HSV 色相 → RGB（同镜重生成颜色一致）。"""
    h = int(hashlib.md5(shot_id.encode()).hexdigest()[:8], 16) % 360
    # HSV → RGB（S=0.55, V=0.85，柔和占位色）
    import colorsys

    r, g, b = colorsys.hsv_to_rgb(h / 360.0, 0.55, 0.85)
    return (int(r * 255), int(g * 255), int(b * 255))


class MockMediaProvider:
    """零成本占位 Provider（阶段 0 默认）。"""

    name = "mock"

    def __init__(self) -> None:
        self.fallback_note: str | None = None

    # ── 成本预估（阶段 4 启用；mock 恒 0）─────────────────

    async def estimate(self, job_type: str, job_input: dict[str, Any]) -> dict[str, float]:
        return {"credits": 0.0}

    # ── LLM 能力：规则分镜（不调模型）─────────────────────

    async def generate_storyboard(
        self,
        prompt: str,
        *,
        shot_count: int,
        shot_duration: float,
        style: str,
        ratio: str,
    ) -> dict[str, Any]:
        from app.db.models import generate_ulid

        latency = self._latency()
        if await self._maybe_fail("storyboard"):
            raise RuntimeError("Mock 分镜编排失败（联调失败率触发）")

        shots = []
        for i in range(max(1, shot_count)):
            shots.append({
                "shot_id": generate_ulid(),
                "shot_index": i + 1,
                "caption": f"第{i + 1}镜：{_MOCK_CAPTIONS[i % len(_MOCK_CAPTIONS)]}",
                "image_prompt": (
                    f"{prompt}, shot {i + 1} of {shot_count}, "
                    f"{_MOCK_CAPTIONS[i % len(_MOCK_CAPTIONS)]}, "
                    f"cinematic, high quality, detailed, varied composition"
                ),
                "motion_prompt": _MOCK_MOTIONS[i % len(_MOCK_MOTIONS)],
                "reference_images": [1],
                "duration": shot_duration,
                "transition": "cut",
            })
        return {
            "title": prompt[:30] or "未命名视频",
            "total_shots": len(shots),
            "shots": shots,
            "mock_latency": latency,
        }

    # ── 图片能力：PIL 占位分镜图 ──────────────────────────

    async def generate_shot_image(self, job_input: dict[str, Any]) -> bytes:
        shot = job_input.get("shot", {})
        shot_id = shot.get("shot_id") or shot.get("shot_index", 0)
        image_prompt = shot.get("image_prompt", "")
        ratio = job_input.get("ratio", "9:16")
        w, h = _RATIO_SIZE.get(ratio, (720, 1280))

        await asyncio.sleep(self._latency())
        if await self._maybe_fail("shot_image"):
            raise RuntimeError("Mock 分镜图生成失败（联调失败率触发）")

        img = Image.new("RGB", (w, h), _shot_color(str(shot_id)))
        draw = ImageDraw.Draw(img)
        # 角标：镜头序号 + 比例 + MOCK 水印
        draw.rectangle([0, 0, w, 96], fill=(0, 0, 0))
        draw.text((24, 28), f"MOCK shot {shot.get('shot_index', '?')}  {ratio}", fill=(255, 255, 255))
        draw.rectangle([0, h - 64, w, h], fill=(0, 0, 0))
        # 提示词截断显示（避免溢出画幅）
        draw.text((24, h - 48), image_prompt[:60], fill=(220, 220, 220))

        import io

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    # ── 视频能力：ffmpeg 占位片段 ─────────────────────────

    async def generate_shot_video(self, job_input: dict[str, Any]) -> bytes:
        shot = job_input.get("shot", {})
        shot_id = str(shot.get("shot_id") or shot.get("shot_index", 0))
        duration = float(job_input.get("duration") or 5.0)
        ratio = job_input.get("ratio", "9:16")
        w, h = _RATIO_SIZE.get(ratio, (720, 1280))
        color = _shot_color(shot_id)
        hex_color = "%02x%02x%02x" % color

        await asyncio.sleep(self._latency())
        if await self._maybe_fail("shot_video"):
            raise RuntimeError("Mock 视频生成失败（联调失败率触发）")

        with tempfile.TemporaryDirectory(prefix="mock_vclip_") as td:
            out = Path(td) / "clip.mp4"
            cmd = [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i",
                f"color=c=0x{hex_color}:s={w}x{h}:d={duration:.2f}:r=30",
                "-vf", f"drawtext=text='MOCK shot {shot.get('shot_index', '?')}':"
                       f"fontcolor=white:fontsize=48:x=(w-text_w)/2:y=(h-text_h)/2",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an",
                str(out),
            ]
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
                )
                _, stderr = await proc.communicate()
                returncode = proc.returncode
            except NotImplementedError:
                result = await asyncio.to_thread(
                    subprocess.run, cmd, capture_output=True,
                )
                stderr, returncode = result.stderr, result.returncode
            if returncode != 0 or not out.is_file():
                raise RuntimeError(
                    f"Mock 视频生成失败（ffmpeg）：{stderr.decode(errors='replace')[-200:]}"
                )
            return out.read_bytes()

    # ── 自检 ──────────────────────────────────────────────

    async def check(self) -> dict[str, bool]:
        import shutil

        return {
            "configured": True,
            "ffmpeg": shutil.which("ffmpeg") is not None,
        }

    # ── 内部：延迟与失败率 ────────────────────────────────

    def _latency(self) -> float:
        from app.config import get_settings

        return getattr(get_settings(), "video_mock_latency_sec", 2.0)

    async def _maybe_fail(self, job_type: str) -> bool:
        from app.config import get_settings

        rate = float(getattr(get_settings(), "video_mock_failure_rate", 0.0) or 0.0)
        if rate <= 0:
            return False
        return random.random() < rate