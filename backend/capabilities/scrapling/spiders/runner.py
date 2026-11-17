"""Spider 运行器：subprocess.Popen 子进程 + async 桥接 + 通知总线进度推送。

Spider 继承 Scrapling Spider ABC，start() 自动调度
start_requests() → parse() → on_scraped_item()（实时入库）。
因 Scrapling 内部使用 Playwright sync API，必须在独立子进程中运行。

子进程通信方式：subprocess.Popen + stdout JSON 行协议，
彻底绕开 Windows multiprocessing spawn 模式的 import 问题。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path

from sqlalchemy import select

from app.db.models import PlatformAccount
from app.db.session import AsyncSessionLocal

logger = logging.getLogger(__name__)

SPIDER_WORKER_SCRIPT = Path(__file__).resolve().parent / "spider_worker.py"

_last_sync_time: dict[str, float] = {}


def _sync_timeout() -> float:
    """子进程等待超时（秒）。逐条笔记都要拉详情页，笔记多时远超旧的 300s 硬编码。"""
    try:
        from app.config import get_settings

        return float(get_settings().spider_sync_timeout)
    except Exception:
        return 1800.0


def _detail_budget_arg(force: bool) -> str:
    """单轮详情页预算（传给子进程）。

    force=True 时传 "0"（不限制）：用户显式要求全量刷新过期图文，
    一次性尽量刷完；未刷到的部分靠"尝试次数少优先"排序在下一轮继续，
    子进程结果也是逐条实时入库，超时不会丢已采集数据。
    """
    if force:
        return "0"
    try:
        from app.config import get_settings

        return str(int(get_settings().spider_detail_budget))
    except Exception:
        return "12"


def _last_stderr_line(stderr_output: str, limit: int = 300) -> str:
    """取 stderr 最后一行非空内容，用于把子进程异常原因带进同步错误里。"""
    for line in reversed(stderr_output.strip().splitlines()):
        text = line.strip()
        if text:
            return text[:limit]
    return ""


async def _read_result_line(
    proc: subprocess.Popen, timeout: float
) -> tuple[str | None, bool, list[str]]:
    """读子进程 stdout，直到拿到结果 JSON 行。

    子进程理论上只在最后输出一行结果 JSON，但 Scrapling / 第三方库可能往 stdout
    打日志。旧实现只读第一行，遇到非 JSON 行就会误判"结果解析失败"；
    这里跳过非 JSON 行，直到拿到含 ok/error 键的那一行。

    返回 (结果行, 是否超时, 被跳过的非结果行)。
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    skipped: list[str] = []

    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            return None, True, skipped
        try:
            line = await asyncio.wait_for(
                loop.run_in_executor(None, proc.stdout.readline), timeout=remaining
            )
        except asyncio.TimeoutError:
            return None, True, skipped
        except Exception as e:
            logger.warning(f"Spider: read stdout failed: {e}")
            return None, False, skipped

        if not line:
            return None, False, skipped

        text = (
            line.decode("utf-8", errors="replace") if isinstance(line, bytes) else str(line)
        ).strip()
        if not text:
            continue

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            skipped.append(text)
            continue

        if isinstance(parsed, dict) and ("ok" in parsed or "error" in parsed):
            return text, False, skipped
        skipped.append(text)


async def start_sync(account_id: str, user_id: str, platform: str, force: bool = False) -> None:
    """启动 Spider 同步任务（子进程运行）。"""
    from capabilities.scrapling.spiders.db_sink import update_sync_status

    await update_sync_status(user_id, platform, "running")

    try:
        account = await _get_account(account_id)
        if not account:
            await update_sync_status(user_id, platform, "idle", "账号不存在")
            return

        cookies = account.cookies_json or {}
        platform_uid = account.platform_uid or ""

        storage_path = (
            Path(__file__).resolve().parent.parent.parent.parent
            / "app"
            / "storage_states"
            / f"{platform}.json"
        )
        if not storage_path.exists():
            logger.warning(
                f"Spider: no storageState for {platform}, login may not persist. User should re-login."
            )

        cookies_json = json.dumps(cookies, ensure_ascii=False)

        backend_dir = str(Path(__file__).resolve().parents[3])
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        pythonpath = env.get("PYTHONPATH", "")
        if backend_dir not in pythonpath.split(os.pathsep):
            env["PYTHONPATH"] = (
                f"{backend_dir}{os.pathsep}{pythonpath}" if pythonpath else backend_dir
            )

        budget_arg = _detail_budget_arg(force)
        try:
            proc = subprocess.Popen(
                [
                    sys.executable,
                    str(SPIDER_WORKER_SCRIPT),
                    platform,
                    platform_uid,
                    cookies_json,
                    user_id,
                    "1" if force else "0",
                    budget_arg,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
            )
        except Exception as e:
            logger.error(f"Spider: failed to start spider_worker subprocess: {e}", exc_info=True)
            await update_sync_status(user_id, platform, "idle", f"启动子进程失败: {e}")
            return

        logger.info(
            f"Spider subprocess started for {platform}, pid={proc.pid}, "
            f"force={force}, detail_budget={budget_arg or 'default'}"
        )

        stderr_chunks: list[str] = []

        def _drain_stderr() -> None:
            try:
                for chunk in iter(proc.stderr.readline, b""):
                    stderr_chunks.append(chunk.decode("utf-8", errors="replace"))
            except Exception:
                pass

        stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
        stderr_thread.start()

        timeout = _sync_timeout()
        result_line, timed_out, skipped = await _read_result_line(proc, timeout)

        if timed_out:
            if proc.poll() is None:
                proc.terminate()
                logger.warning(
                    f"Spider subprocess timed out after {timeout:.0f}s, terminated: {platform}"
                )

        try:
            proc.wait(timeout=10)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

        stderr_thread.join(timeout=5)

        stderr_output = "".join(stderr_chunks)
        if stderr_output:
            logger.debug(f"Spider worker stderr: {stderr_output[:500]}")
        if skipped:
            logger.debug(f"Spider stdout skipped {len(skipped)} non-result line(s)")

        if timed_out:
            # 子进程是逐条实时入库的（on_scraped_item），超时只意味着"没跑完"，
            # 已采集的数据不会丢；这里如实汇报累计入库量，并提示下轮继续。
            total_works = await _sync_works_count(user_id, platform)
            msg = (
                f"同步超时（{timeout:.0f}s 已终止子进程），已入库作品全部保留，"
                f"累计 {total_works} 条；剩余详情页会在下次同步继续刷新"
            )
            await update_sync_status(user_id, platform, "idle", msg)
            await _publish(
                "spider_sync_timeout",
                {
                    "platform": platform,
                    "total_works": total_works,
                    "user_id": user_id,
                },
            )
            logger.warning(
                f"Spider sync timed out: platform={platform}, kept {total_works} works, "
                f"stderr tail: {_last_stderr_line(stderr_output)}"
            )
            return

        if not result_line:
            tail = _last_stderr_line(stderr_output)
            msg = "Spider 子进程无结果输出"
            if tail:
                msg = f"Spider 子进程无结果输出：{tail}"
            logger.warning(
                f"Spider worker produced no result line, stderr tail: {stderr_output[-1500:]}"
            )
            await update_sync_status(user_id, platform, "idle", msg)
            return

        raw = result_line.strip()
        try:
            result = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.error(f"Spider result JSON decode error: {e}, raw[:200]={raw[:200]}")
            await update_sync_status(user_id, platform, "idle", f"Spider 结果解析失败: {e}")
            return

        if result.get("error"):
            await update_sync_status(user_id, platform, "idle", result["error"])
            return

        items = result.get("items", [])
        # 子进程的 on_scraped_item 已经逐条实时入库（含图片本地化），
        # 父进程不能再入库一遍：重复入库会把同一批 CDN 图再下载一次，
        # 这正是旧版同步动辄跑满超时的主因之一。
        saved_count = int(result.get("saved") or len(items))
        deferred = int(result.get("deferred") or 0)

        total_works = await _sync_works_count(user_id, platform)
        # 注意：sync_error 会被前端当成"同步失败"红色提示，所以成功路径一律清空；
        # 还有多少条图集待刷新只走日志与通知事件，不占用错误通道。
        await update_sync_status(user_id, platform, "idle")

        await _publish(
            "spider_sync_completed",
            {
                "platform": platform,
                "total": len(items),
                "saved": saved_count,
                "deferred": deferred,
                "total_works": total_works,
                "user_id": user_id,
            },
        )

        logger.info(
            f"Spider sync completed: platform={platform}, total={len(items)}, "
            f"saved={saved_count}, deferred={deferred}, total_works={total_works}"
        )

    except Exception as e:
        logger.error(f"Spider sync failed: platform={platform}, error={e}", exc_info=True)
        await update_sync_status(user_id, platform, "idle", str(e)[:500])
        await _publish(
            "spider_sync_failed",
            {
                "platform": platform,
                "error": str(e)[:200],
                "user_id": user_id,
            },
        )


async def _publish(event: str, payload: dict) -> None:
    """推送同步进度事件（通知总线不可用时静默降级）。"""
    try:
        from app.services.notification_bus import notification_bus

        await notification_bus.publish(event, payload)
    except Exception as e:
        logger.debug(f"Spider: publish {event} failed: {e}")


async def _sync_works_count(user_id: str, platform: str) -> int:
    """统计并回写账号的作品总数。"""
    try:
        from capabilities.scrapling.spiders.db_sink import (
            count_collected_works,
            update_works_count,
        )

        total = await count_collected_works(user_id, platform)
        await update_works_count(user_id, platform, total)
        return total
    except Exception as e:
        logger.warning(f"Spider: update works count failed: {e}")
        return 0


async def _get_account(account_id: str) -> PlatformAccount | None:
    """获取 PlatformAccount。"""
    async with AsyncSessionLocal() as db:
        return await db.scalar(select(PlatformAccount).where(PlatformAccount.id == account_id))
