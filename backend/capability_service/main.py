"""能力服务（三期 + 五期-C）：独立进程承载重能力，实现工具协议三方法 + 进度事件回推。

启动（backend 目录下）：
    .venv/Scripts/python.exe -m uvicorn capability_service.main:app --port 8100

端点（契约带②的 HTTP 形态）：
    GET  /health               → {"ok": true}
    GET  /tools                → {"tools": [...]}
    POST /tools/{name}/call    → body {"arguments": {...}}，返回 {"result": {...}}
    POST /internal/events      → 服务进程把进度事件回推给主进程（五期-C）

已注册能力：
  - grid_detect / grid_split（numpy/Pillow 重依赖）
  - render_htmls（Playwright/Chromium 重依赖，五期-A browser-service）
同机部署共享文件系统，传路径即可；跨机部署时在工具实现内改传 base64。
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

from app.services.grid_detect import detect_grid, split_grid
from app.services.grid_types import GridDetection

app = FastAPI(title="capability-service", version="1.0.0")


# ---------- 进度事件回调（五期-C：长任务进度通道） ----------

_progress_callback: Callable[[dict[str, Any]], Any] | None = None


def set_progress_callback(callback: Callable[[dict[str, Any]], Any] | None) -> None:
    """设置进度事件回调。服务内工具执行时调用此回调把进度推给主进程。

    主进程启动 capability-service 子进程时，通过启动参数或环境变量
    指定主进程的 /internal/events 端点地址，服务侧用 httpx 回推。
    独立部署时由运维配置；进程内测试时可直接注入。
    """
    global _progress_callback
    _progress_callback = callback


async def emit_progress(event: dict[str, Any]) -> None:
    """服务内工具发射进度事件。

    event 结构：
        {
            "workflow_id": str,       # 所属工作流
            "event_type": str,        # SSE 事件名（如 "multi_image_video_progress"）
            "payload": dict,          # 事件载荷
            "turn_id": str | None,    # 回合 ID（可选）
        }
    """
    if _progress_callback is None:
        logger.debug(f"[progress] no callback, event dropped: {event.get('event_type')}")
        return
    try:
        result = _progress_callback(event)
        if asyncio.iscoroutine(result):
            await result
    except Exception as e:
        logger.warning(f"[progress] callback failed: {e}")


# ---------- grid 工具（同步 CPU 函数统一 to_thread） ----------

def _tool_grid_detect(image_path: str) -> dict[str, Any]:
    det = detect_grid(image_path)
    return det.to_dict()


def _tool_grid_split(image_path: str, out_dir: str, detection: dict[str, Any]) -> dict[str, Any]:
    det = GridDetection.from_dict(detection)
    tiles = split_grid(image_path, out_dir, det)
    return {"ok": True, "tiles": [str(t) for t in tiles]}


# ---------- browser 工具（五期-A：Playwright 隔离进程） ----------

async def _tool_render_htmls(pages_json: str, output_dir: str = "") -> dict[str, Any]:
    """HTML 列表 → PNG 列表（Playwright 渲染）。

    Args:
        pages_json: JSON 字符串，[{"html": ..., "width": int, "height": int, "has_images": bool}, ...]
        output_dir: 输出目录，非空时写文件返回路径；为空时返回 base64
    """
    from app.cards.export import render_htmls_to_pngs

    pages = json.loads(pages_json)
    pngs = await render_htmls_to_pngs(pages)

    if output_dir:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        paths = []
        for i, png in enumerate(pngs):
            p = out / f"card_{i:03d}.png"
            p.write_bytes(png)
            paths.append(str(p))
        return {"ok": True, "paths": paths, "count": len(pngs)}

    b64s = [base64.b64encode(png).decode() for png in pngs]
    return {"ok": True, "pngs_b64": b64s, "count": len(pngs)}


# ---------- media 工具（五期-D：onnx/ffmpeg 隔离进程） ----------

async def _tool_tts_piper(
    text: str,
    output_path: str,
    binary: str = "",
    model_path: str = "",
) -> dict[str, Any]:
    """piper 离线 TTS 推理（onnx 模型，分钟级长任务）。"""
    from app.skills.produce.tts_piper import synthesize_piper

    result = await synthesize_piper(
        text=text,
        output_path=output_path,
        binary=binary or None,
        model_path=model_path or None,
    )
    return {"ok": True, "output_path": result}


async def _tool_video_compose(storyboard_json: str, output_path: str = "", pad_mode: str = "auto", sub_font: str = "") -> dict[str, Any]:
    """ffmpeg 视频合成（分钟级长任务）。

    Args:
        storyboard_json: JSON 字符串，分镜 storyboard 结构
        output_path: 输出文件路径，为空时自动生成
        pad_mode: 片段不足时长时的填充模式
        sub_font: 字幕字体
    """
    from capabilities.video.adapters.video_compose import compose_video

    storyboard = json.loads(storyboard_json)
    result = await compose_video(
        storyboard=storyboard,
        output_path=output_path or None,
        pad_mode=pad_mode,
        sub_font=sub_font or None,
    )
    return result


# ---------- spider 工具（五期-E：Scrapling+Playwright 隔离进程） ----------

async def _tool_spider_sync(
    account_id: str,
    user_id: str,
    platform: str,
    force: bool = False,
) -> dict[str, Any]:
    """平台数据同步爬虫（Scrapling+Playwright 子进程）。

    Args:
        account_id: 平台账号 ID
        user_id: 用户 ID
        platform: 平台名（xiaohongshu/douyin/bilibili）
        force: 是否强制全量同步
    """
    from capabilities.scrapling.spiders.runner import start_sync

    await start_sync(account_id, user_id, platform, force=force)
    return {"ok": True, "platform": platform, "account_id": account_id}


CAPABILITY_TOOLS: dict[str, dict[str, Any]] = {
    "grid_detect": {
        "handler": _tool_grid_detect,
        "description": "检测图片是否为拼图网格（numpy/Pillow 隔离进程）",
    },
    "grid_split": {
        "handler": _tool_grid_split,
        "description": "按检测结果把拼图切成单图，返回切片路径列表",
    },
    "render_htmls": {
        "handler": _tool_render_htmls,
        "description": "HTML 列表 → PNG 列表（Playwright/Chromium 隔离进程，五期-A browser-service）",
        "is_async": True,
    },
    "tts_piper": {
        "handler": _tool_tts_piper,
        "description": "piper 离线 TTS 推理（onnx 模型，五期-D media-service）",
        "is_async": True,
    },
    "video_compose": {
        "handler": _tool_video_compose,
        "description": "ffmpeg 视频合成（分镜→成片，五期-D media-service）",
        "is_async": True,
    },
    "spider_sync": {
        "handler": _tool_spider_sync,
        "description": "平台数据同步爬虫（Scrapling+Playwright 隔离进程，五期-E spider-service）",
        "is_async": True,
    },
}


class CallRequest(BaseModel):
    arguments: dict[str, Any] = {}


# ---------- 协议三方法 ----------

@app.get("/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "service": "capability-service", "tools": len(CAPABILITY_TOOLS)}


@app.get("/tools")
async def list_tools() -> dict[str, Any]:
    return {
        "tools": [
            {"name": name, "description": meta["description"]}
            for name, meta in CAPABILITY_TOOLS.items()
        ]
    }


@app.post("/tools/{name}/call")
async def call_tool(name: str, req: CallRequest) -> dict[str, Any]:
    meta = CAPABILITY_TOOLS.get(name)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"unknown tool: {name}")
    handler: Callable[..., dict[str, Any]] = meta["handler"]
    is_async = meta.get("is_async", False)
    try:
        if is_async:
            result = await handler(**req.arguments)
        else:
            result = await asyncio.to_thread(handler, **req.arguments)
    except TypeError as e:
        return {"result": {"ok": False, "error": f"invalid arguments for {name}: {e}"}}
    except Exception as e:
        return {"result": {"ok": False, "error": str(e)}}
    return {"result": result}


# ---------- 五期-C：进度事件回推端点 ----------

class ProgressEventRequest(BaseModel):
    """服务进程回推给主进程的进度事件。"""
    workflow_id: str
    event_type: str
    payload: dict[str, Any] = {}
    turn_id: str | None = None


@app.post("/internal/events")
async def receive_progress_event(req: ProgressEventRequest) -> dict[str, Any]:
    """主进程接收服务进程回推的进度事件，转发进 SSE 总线。

    此端点由主进程 API 服务暴露，capability-service 通过 httpx 调用。
    事件 schema 与 sse_bus.publish() 完全对齐，前端零改动。
    """
    from app.services.sse_bus import sse_bus

    event_id = await sse_bus.publish(
        workflow_id=req.workflow_id,
        event_type=req.event_type,
        payload=req.payload,
        turn_id=req.turn_id,
    )
    return {"ok": True, "event_id": event_id}