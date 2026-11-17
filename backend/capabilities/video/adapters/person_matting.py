"""人物抠像 adapter — RVM (Robust Video Matting)。

设计依据（docs 10.20 方案）：
  - 模型：RVM mobilenetv3 TorchScript（Apache-2.0, PeterL1n/RobustVideoMatting）
    视频原生时序稳定（循环隐状态），发丝边缘优于单帧方案
  - 权重：models/rvm/rvm_mobilenetv3_fp32.torchscript（15.5MB）

降级契约（最重要，对齐 overlay_review 先例）：
  torch 未安装 / 权重缺失 / 无 GPU / 读取失败 / 推理异常 → matte_person
  返回 None，调用方回退矩形 PIP。**抠像是增值步骤，绝不阻断主管线。**

产物：rgba PNG 帧序列（%06d.png，straight alpha），ffmpeg image2 demuxer
+ overlay 直接消费；结果 meta.json 落盘供跨会话缓存复用。
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import numpy as np
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_WEIGHTS = _ROOT / "models" / "rvm" / "rvm_mobilenetv3_fp32.torchscript"


class MatteResult(BaseModel):
    """抠像产物：rgba 帧序列目录 + 复现合成所需元信息。

    命名：%06d.png（无 frame_ 前缀，frame_dir 下 0 起始）。pattern 字段供
    scene 引用与 review 断链检查使用；旧 meta.json 缓存缺该字段时用默认值。
    """

    frame_dir: Path
    count: int = Field(ge=0)
    fps: float = Field(gt=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    pattern: str = "%06d.png"


def _load_model() -> Any:
    """加载 RVM TorchScript → CUDA eval。失败一律抛异常（上层统一降级）。

    CPU 推理不提供（10.20 方案明确：逐帧太慢，无 GPU 直接走降级链）。
    """
    import torch

    if not _WEIGHTS.is_file():
        raise FileNotFoundError(f"RVM weights missing: {_WEIGHTS}")
    if not torch.cuda.is_available():
        raise RuntimeError("RVM requires CUDA; CPU inference out of scope")
    # 项目路径含中文 → torch C++ 层 fopen 对非 ASCII 绝对路径报 errno 2，
    # 必须 Python 层读字节后从 BytesIO 加载（10.20 真机实证）
    import io

    model = torch.jit.load(io.BytesIO(_WEIGHTS.read_bytes()), map_location="cuda")
    model.eval().cuda()
    return model


def _probe_fps(video_path: str) -> float | None:
    """PyAV 取帧率（对齐 _probe_resolution 的 PyAV 优先惯例）。"""
    try:
        import av

        with av.open(video_path) as container:
            for stream in container.streams:
                if stream.type == "video":
                    rate = stream.average_rate
                    if rate and float(rate) > 0:
                        return float(rate)
    except Exception:
        pass
    return None


def _probe_image_size(image_path: str) -> tuple[int, int]:
    """图片分辨率 (w, h) — deck_images 主画面画布取首张配图。失败抛异常。

    Windows 中文路径：cv2.imread 会静默失败 → np.fromfile + imdecode。
    """
    import cv2

    data = np.fromfile(image_path, dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"cannot read image: {image_path}")
    return (img.shape[1], img.shape[0])


def _write_frame(fgr_rgb: np.ndarray, pha: np.ndarray, path: Path) -> Path:
    """单帧落盘：fgr(RGB uint8) + pha(float 0-1) → straight-alpha RGBA PNG。

    不做预乘：ffmpeg overlay 直接消费 straight alpha。
    Windows 中文路径：cv2.imwrite 会静默失败 → imencode + write_bytes。
    """
    import cv2

    h, w = pha.shape
    if fgr_rgb.shape[:2] != (h, w):
        fgr_rgb = cv2.resize(fgr_rgb, (w, h), interpolation=cv2.INTER_LINEAR)
    alpha = np.clip(pha * 255.0, 0, 255).astype(np.uint8)
    rgba = np.dstack([fgr_rgb[..., ::-1], alpha])  # RGB→BGR + A
    ok, buf = cv2.imencode(".png", rgba)
    if not ok:
        raise RuntimeError(f"imencode failed: {path}")
    path.write_bytes(buf.tobytes())
    return path


def _matte_sync(
    video_path: str,
    model: Any,
    frame_dir: Path,
) -> tuple[int, int, int]:
    """同步重活（读帧 + RVM 推理 + PNG 逐帧落盘，流式不积内存）。

    返回 (count, width, height)；打不开视频返回 (0, 0, 0)。
    读帧走 PyAV（Windows 中文路径 cv2.VideoCapture 会静默失败 —
    对齐 _probe_resolution 的 av 惯例）；rec 隐状态跨帧传递（RVM 时序
    稳定的关键）；downsample_ratio 按长边 ~512 自适应（RVM 官方实践）。
    """
    import av
    import torch

    count, w, h = 0, 0, 0
    rec: list = [None, None, None, None]
    ratio = None
    try:
        with av.open(video_path) as container:
            stream = next(s for s in container.streams if s.type == "video")
            for frame in container.decode(stream):
                bgr = frame.to_ndarray(format="bgr24")
                if ratio is None:
                    h, w = bgr.shape[:2]
                    ratio = max(0.1, min(0.7, 512.0 / max(h, w)))
                rgb = bgr[..., ::-1]
                src = (
                    torch.from_numpy(rgb.copy())
                    .float()
                    .div_(255.0)
                    .permute(2, 0, 1)
                    .unsqueeze(0)
                    .cuda()
                )
                with torch.no_grad():
                    fgr, pha, *rec = model(src, *rec, ratio)
                fgr_rgb = fgr[0].mul(255).clamp_(0, 255).byte().permute(1, 2, 0).cpu().numpy()
                pha_np = pha[0, 0].float().cpu().numpy()
                _write_frame(fgr_rgb, pha_np, frame_dir / f"{count:06d}.png")
                count += 1
    except Exception:
        if count == 0:
            return (0, 0, 0)
        raise
    return (count, w, h)


def _try_cache(out_dir: Path) -> MatteResult | None:
    """缓存命中：meta.json 有效 + 最后一帧存在 → 直接复用。"""
    meta_path = out_dir / "meta.json"
    if not meta_path.is_file():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        frame_dir = out_dir / "frames"
        if (frame_dir / f"{int(meta['count']) - 1:06d}.png").is_file():
            return MatteResult(frame_dir=frame_dir, **meta)
    except Exception as e:
        logger.warning(f"[matting] 缓存 meta 损坏，重抠：{e}")
    return None


async def matte_person(video_path: str, out_dir: Path) -> MatteResult | None:
    """视频 → 人物 rgba PNG 帧序列。

    降级契约：任何失败返回 None（不抛、不半成品阻断）；
    out_dir/meta.json + frames/ 即缓存（调用方传持久目录可跨会话复用）。
    """
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        cached = _try_cache(out_dir)
        if cached is not None:
            logger.info(f"[matting] 缓存命中：{out_dir.name}（{cached.count} 帧）")
            return cached

        model = _load_model()
        fps = _probe_fps(video_path)
        if not fps:
            logger.warning("[matting] 无法探测帧率，降级 None")
            return None

        frame_dir = out_dir / "frames"
        frame_dir.mkdir(parents=True, exist_ok=True)
        count, w, h = await asyncio.to_thread(
            _matte_sync,
            video_path,
            model,
            frame_dir,
        )
        if count <= 0:
            logger.warning("[matting] 0 帧产出，降级 None")
            return None

        result = MatteResult(
            frame_dir=frame_dir,
            count=count,
            fps=fps,
            width=w,
            height=h,
        )
        (out_dir / "meta.json").write_text(
            json.dumps(result.model_dump(mode="json", exclude={"frame_dir"})),
            encoding="utf-8",
        )
        logger.info(f"[matting] 完成：{count} 帧 @ {fps:.1f}fps ({w}x{h})")
        return result
    except Exception as e:
        logger.warning(f"[matting] 抠像失败（降级 None）：{e}")
        return None
