"""媒体文件下载器：基于 yt-dlp Python API。

只负责"下载视频文件落盘"，元数据爬取仍走各平台 Spider。
平台白名单路由：bilibili 等支持平台走 yt-dlp；
xiaohongshu / douyin 明确拒绝（无 extractor 或风控不稳，保持现有 Spider 路线）。

设计文档: docs/FEATURE_yt-dlp媒体下载接入.md
参考: yt-dlp README「Embedding yt-dlp」；yt_dlp/cookies.py Netscape 格式
"""

from __future__ import annotations

import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 平台白名单：key 与 spider platform 字段一致，value 为提示信息用站点名
YTDLP_PLATFORM_WHITELIST: dict[str, str] = {
    "bilibili": "bilibili",
    "youtube": "youtube",
}

# 默认输出根目录（相对 backend/），可用 MEDIA_DOWNLOAD_DIR 覆盖
DEFAULT_MEDIA_DIR = "outputs/media"


class MediaDownloadError(Exception):
    """下载失败（yt-dlp 报错 / 未产出文件）。"""


class UnsupportedPlatformError(MediaDownloadError):
    """平台不在 yt-dlp 白名单内。"""


def get_media_base_dir() -> Path:
    """媒体下载根目录：env MEDIA_DOWNLOAD_DIR 优先，否则 backend/outputs/media。"""
    env_dir = os.environ.get("MEDIA_DOWNLOAD_DIR", "")
    if env_dir:
        return Path(env_dir)
    return Path(__file__).resolve().parents[3] / DEFAULT_MEDIA_DIR


def _coerce_cookie_value(v: Any) -> str:
    """cookies dict 的 value 兼容两种形态：纯字符串 / {value: ...} 结构。"""
    if isinstance(v, dict):
        return str(v.get("value", ""))
    return str(v)


def _coerce_cookie_domain(v: Any, default_domain: str) -> str:
    if isinstance(v, dict) and v.get("domain"):
        return str(v["domain"])
    return default_domain


def write_netscape_cookiefile(cookies: dict[str, Any], domain: str, path: Path) -> None:
    """将 cookies dict 写为 Netscape 格式文件（yt-dlp cookiefile 要求）。

    行格式: domain \t include_subdomains \t path \t secure \t expiry \t name \t value
    参考: yt_dlp/cookies.py
    """
    lines = ["# Netscape HTTP Cookie File"]
    expiry = str(int(time.time()) + 30 * 86400)  # 30 天有效期占位
    for name, raw in cookies.items():
        value = _coerce_cookie_value(raw)
        if not value:
            continue
        cookie_domain = _coerce_cookie_domain(raw, domain)
        lines.append(f"{cookie_domain}\tTRUE\t/\tFALSE\t{expiry}\t{name}\t{value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _domain_for_platform(platform: str) -> str:
    return {
        "bilibili": ".bilibili.com",
        "youtube": ".youtube.com",
    }.get(platform, ".bilibili.com")


def _resolve_ffmpeg() -> str | None:
    """定位 ffmpeg：PATH 优先，降级到 imageio_ffmpeg 自带二进制（视频管线依赖）。"""
    import shutil

    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        return None


def download_media(
    url: str,
    platform: str,
    out_dir: str | Path | None = None,
    cookies: dict[str, Any] | None = None,
    max_height: int | None = 1080,
    timeout: int | None = None,
) -> dict[str, Any]:
    """下载单个视频文件到本地。

    Args:
        url: 视频页面链接
        platform: 平台标识（须在 YTDLP_PLATFORM_WHITELIST 内）
        out_dir: 输出目录，默认 {MEDIA_DOWNLOAD_DIR}/{platform}/
        cookies: 登录态 cookies（dict，与 PlatformAccount.cookies_json 一致）
        max_height: 画质上限（像素），None 表示不限制
        timeout: 下载超时秒数，None 表示不限制

    Returns:
        {platform, url, filepath, title, duration, extractor}

    Raises:
        UnsupportedPlatformError: 平台不在白名单
        MediaDownloadError: 下载失败或未产出文件
    """
    if not url or not str(url).strip():
        raise MediaDownloadError("url 不能为空")
    if platform not in YTDLP_PLATFORM_WHITELIST:
        raise UnsupportedPlatformError(
            f"平台 {platform!r} 不在 yt-dlp 白名单内"
            f"（白名单: {sorted(YTDLP_PLATFORM_WHITELIST)}）"
        )

    # 延迟导入：未安装 yt-dlp 时给出可读错误，不影响 spider 元数据链路
    try:
        from yt_dlp import YoutubeDL
    except ImportError as e:
        raise MediaDownloadError(
            "yt-dlp 未安装，请执行: pip install yt-dlp -i https://mirrors.aliyun.com/pypi/simple/"
        ) from e

    base_dir = Path(out_dir) if out_dir else get_media_base_dir() / platform
    base_dir.mkdir(parents=True, exist_ok=True)

    fmt = (
        "bv*+ba/b"
        if max_height is None
        else f"bv*[height<={max_height}]+ba/b[height<={max_height}]/b"
    )
    opts: dict[str, Any] = {
        "format": fmt,
        "outtmpl": str(base_dir / "%(id)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "retries": 3,
    }
    if timeout:
        opts["socket_timeout"] = timeout

    # bv*+ba 混合格式合流需要 ffmpeg；PATH 没有时用 imageio_ffmpeg 自带二进制
    ffmpeg = _resolve_ffmpeg()
    if ffmpeg:
        opts["ffmpeg_location"] = ffmpeg

    cookiefile: Path | None = None
    if cookies:
        fd, cookie_path = tempfile.mkstemp(prefix="ytdlp_cookies_", suffix=".txt")
        os.close(fd)  # Windows 下句柄未关闭会导致 finally 中的 unlink 失败
        cookiefile = Path(cookie_path)
        write_netscape_cookiefile(cookies, _domain_for_platform(platform), cookiefile)
        opts["cookiefile"] = str(cookiefile)

    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if info is None:
                raise MediaDownloadError(f"yt-dlp 未返回视频信息: {url}")
            # extract_info 对 bv*+ba 合流场景返回 requested_downloads；单文件直接取 filepath
            filepath = ""
            requested = info.get("requested_downloads") or []
            if requested:
                filepath = requested[0].get("filepath", "")
            if not filepath:
                filepath = ydl.prepare_filename(info)
            if not filepath or not Path(filepath).exists():
                raise MediaDownloadError(f"下载完成但未找到产物文件: {filepath!r}")

            return {
                "platform": platform,
                "url": url,
                "filepath": filepath,
                "title": info.get("title", ""),
                "duration": info.get("duration"),
                "extractor": info.get("extractor_key", ""),
            }
    except MediaDownloadError:
        raise
    except Exception as e:
        raise MediaDownloadError(f"yt-dlp 下载失败: {e}") from e
    finally:
        if cookiefile and cookiefile.exists():
            try:
                cookiefile.unlink()
            except OSError:
                logger.warning("临时 cookiefile 清理失败: %s", cookiefile)
