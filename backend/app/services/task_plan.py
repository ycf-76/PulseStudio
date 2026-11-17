"""任务清单服务（多日定时发布）。

本模块仅保留任务拆解与状态管理，执行降级为标记（不再启动工作流引擎）。

三层模型：
TaskPlan（清单，用户一句话意图）→ TaskItem（逐日子任务）→ TaskRun（单日执行现场）

job 入口 run_task_scan() 由 main.py lifespan 注册（每分钟）。
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import (
    TaskItem,
    TaskItemStatus,
    TaskPlan,
    TaskPlanStatus,
    TaskRun,
    TaskRunStatus,
)
from app.db.session import AsyncSessionLocal
from app.services.notification_bus import notification_bus

logger = logging.getLogger(__name__)

# 上海时区（plan_time 的 HH:MM 按此时区解释）
_TZ_SH = timezone(timedelta(hours=8))

# 每轮最多执行几个到期任务（防积压雪崩）
_SCAN_BATCH_LIMIT = 5

# 过期超过该小时数的条目直接跳过，不补发（防重启后一次性连发积压内容）
_STALE_SKIP_HOURS = 6

_TASK_PIPELINE_DEF_NAME = "定时发布流水线"

_QUALITY_GATE_THRESHOLD = 7.0


def _now() -> datetime:
    return datetime.now(UTC)


# ============================================================================
# 拆解器：用户一句话意图 → 结构化逐日清单
# ============================================================================

_DECOMPOSE_SYSTEM_PROMPT = """你是小红书内容排期助手。把用户的发布意图拆解成逐日清单。

要求：
1. 输出严格 JSON，不要任何其他文字。结构：
{"title": "清单标题", "daily_time": "HH:MM", "total_days": N,
 "items": [{"day_index": 1, "topic": "当天发布主题", "keyword": "搜索关键词"}],
 "warnings": ["需要提醒用户的事项"]}
2. 单词/知识点类清单：每天换不同角度（单词卡/联想记忆/真题例句/易混对比），topic 要具体到当天内容。
3. 禁止编造用户没有给出的条目；用户给了 10 个单词就拆 10 天，每天对应一个。
4. 用户未指定发布时间时 daily_time 用 "09:00"，并在 warnings 里说明。
5. total_days 不超过 60。topic 80 字以内。"""


def _parse_daily_time(value: str | None) -> str:
    """校验 HH:MM，非法回退 09:00。"""
    if not value:
        return "09:00"
    try:
        h, m = value.strip().split(":")
        if 0 <= int(h) <= 23 and 0 <= int(m) <= 59:
            return f"{int(h):02d}:{int(m):02d}"
    except Exception:
        pass
    return "09:00"


def _rule_based_decompose(intent_text: str, daily_time: str) -> dict[str, Any]:
    """dry-run 规则拆解：不调 LLM，按行/分隔符切条目。用于零 token 测试与 LLM 失败降级。"""
    lines = [ln.strip(" \t-•0123456789.、") for ln in intent_text.splitlines()]
    entries = [ln for ln in lines if ln]
    if len(entries) <= 1:
        # 单行内的顿号/逗号/分号列表（中英文均支持）
        single = entries[0] if entries else intent_text.strip()
        normalized = (
            single.replace("；", ";").replace("、", ";").replace("，", ";").replace(",", ";")
        )
        entries = [e.strip() for e in normalized.split(";") if e.strip()]
    warnings: list[str] = []
    if not entries:
        entries = [intent_text.strip()[:80] or "未命名主题"]
        warnings.append("未能从意图中识别出多条目，已按单日任务处理，请手动补充清单")
    items = [
        {"day_index": i + 1, "topic": e[:500], "keyword": e[:200]}
        for i, e in enumerate(entries[:60])
    ]
    return {
        "title": (entries[0][:40] + ("…" if len(entries[0]) > 40 else "")) or "任务清单",
        "daily_time": daily_time,
        "total_days": len(items),
        "items": items,
        "warnings": warnings + ["dry-run 规则拆解结果（未调用 LLM）"],
    }


def _validate_decompose(data: dict[str, Any]) -> list[str]:
    """拆解结果结构性校验，返回错误列表（空 = 合法）。"""
    errors: list[str] = []
    items = data.get("items")
    if not isinstance(items, list) or not items:
        return ["items 为空"]
    if len(items) > 60:
        errors.append("条目数超过 60")
    day_indexes: list[int] = []
    for i, it in enumerate(items):
        di = it.get("day_index")
        topic = str(it.get("topic", "")).strip()
        if not isinstance(di, int) or di < 1:
            errors.append(f"第 {i+1} 条 day_index 非法: {di!r}")
        else:
            day_indexes.append(di)
        if not topic:
            errors.append(f"第 {i+1} 条 topic 为空")
    if day_indexes and sorted(day_indexes) != list(range(1, len(day_indexes) + 1)):
        errors.append("day_index 不连续（必须从 1 开始逐日递增）")
    return errors


async def _call_llm_json(system: str, user: str, timeout: int = 40) -> dict[str, Any] | None:
    """轻量 LLM 调用（OpenAI 兼容协议，同 monitor_agent._classify_with_llm 模式）。

    失败返回 None（调用方降级），不抛异常。
    """
    settings = get_settings()
    api_key = (settings.deepseek_api_key or "").strip()
    base_url = (settings.deepseek_base_url or "https://api.deepseek.com").rstrip("/")
    if not api_key:
        logger.warning("[task_plan] LLM 调用跳过：未配置 deepseek_api_key")
        return None
    payload = {
        "model": settings.deepseek_model_v3 or "deepseek-chat",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.2,
        "max_tokens": 2000,
        "response_format": {"type": "json_object"},
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
            )
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"]["content"]
            return json.loads(text)
    except Exception as e:
        logger.warning(f"[task_plan] LLM 调用失败: {e}")
        return None


async def decompose_intent(
    intent_text: str,
    daily_time: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """把用户意图拆解为逐日清单预览（无副作用，不落库）。

    dry_run=True 时不调 LLM（零 token 测试路径），用规则拆解。
    LLM 失败/校验失败时自动降级规则拆解，并在 warnings 说明。
    """
    dt = _parse_daily_time(daily_time)
    if dry_run:
        return _rule_based_decompose(intent_text, dt)

    result = await _call_llm_json(
        _DECOMPOSE_SYSTEM_PROMPT,
        f"每日发布时间偏好：{dt}\n用户意图：\n{intent_text}",
    )
    if result is not None:
        errors = _validate_decompose(result)
        if not errors:
            result["daily_time"] = _parse_daily_time(result.get("daily_time") or dt)
            return result
        # 校验失败重试一次
        logger.warning(f"[task_plan] 拆解校验失败，重试一次: {errors}")
        result = await _call_llm_json(
            _DECOMPOSE_SYSTEM_PROMPT,
            f"上一次输出未通过校验（错误：{errors}），请修正后重新输出严格 JSON。\n"
            f"每日发布时间偏好：{dt}\n用户意图：\n{intent_text}",
        )
        if result is not None and not _validate_decompose(result):
            result["daily_time"] = _parse_daily_time(result.get("daily_time") or dt)
            return result

    fallback = _rule_based_decompose(intent_text, dt)
    fallback["warnings"] = fallback.get("warnings", []) + [
        "LLM 拆解失败，已降级为规则拆解，请手动核对清单"
    ]
    return fallback


# ============================================================================
# 核心服务
# ============================================================================


class TaskPlanService:
    """任务清单 CRUD + 到期执行 + 挂起处理 + 状态收敛。"""

    def __init__(self, db: AsyncSession):
        self.db = db

    # ---------- 查询 ----------

    async def list_plans(self, user_id: str) -> list[dict[str, Any]]:
        rows = await self.db.scalars(
            select(TaskPlan).where(TaskPlan.user_id == user_id).order_by(TaskPlan.created_at.desc())
        )
        return [self._plan_to_dict(p) for p in rows]

    async def get_plan_detail(self, user_id: str, plan_id: str) -> dict[str, Any] | None:
        plan = await self.db.scalar(
            select(TaskPlan).where(TaskPlan.id == plan_id, TaskPlan.user_id == user_id)
        )
        if not plan:
            return None
        items = await self.db.scalars(
            select(TaskItem).where(TaskItem.plan_id == plan_id).order_by(TaskItem.day_index)
        )
        runs = await self.db.scalars(
            select(TaskRun)
            .where(TaskRun.plan_id == plan_id)
            .order_by(TaskRun.created_at.desc())
            .limit(200)
        )
        run_map: dict[str, dict] = {}
        for r in runs:
            run_map.setdefault(r.task_item_id, self._run_to_dict(r))
        return {
            **self._plan_to_dict(plan),
            "items": [{**self._item_to_dict(it), "latest_run": run_map.get(it.id)} for it in items],
        }

    # ---------- 创建 / 生命周期 ----------

    async def create_plan(
        self,
        user_id: str,
        intent_text: str,
        items: list[dict[str, Any]],
        plan_config: dict[str, Any] | None = None,
        account_id: str | None = None,
        title: str = "",
    ) -> TaskPlan:
        """落库并激活清单（前端已确认过拆解预览）。

        items 为用户编辑后的最终清单：[{day_index, topic, keyword?}]。
        plan_time 按 today + day_index - 1 的 daily_time（Asia/Shanghai）回填。
        """
        config = dict(plan_config or {})
        daily_time = _parse_daily_time(config.get("daily_time"))
        config["daily_time"] = daily_time
        # 默认质量门审核 + 人工确认发布（auto_publish 必须由调用方显式开启）
        config.setdefault("review_mode", "quality_gate")
        config.setdefault("model_settings", {})
        config["model_settings"].setdefault("auto_publish", False)
        config.setdefault("confirm_timeout_min", 30)
        config.setdefault("max_retry", 1)

        plan = TaskPlan(
            user_id=user_id,
            account_id=account_id or None,
            title=(title or intent_text.strip()[:100])[:200],
            intent_text=intent_text,
            status=TaskPlanStatus.ACTIVE,
            plan_config=config,
            total_days=len(items),
        )
        self.db.add(plan)
        await self.db.flush()

        base_date = datetime.now(_TZ_SH).date()
        hh, mm = daily_time.split(":")
        for it in items:
            plan_time = datetime(
                base_date.year,
                base_date.month,
                base_date.day,
                int(hh),
                int(mm),
                tzinfo=_TZ_SH,
            ) + timedelta(days=max(int(it.get("day_index", 1)) - 1, 0))
            self.db.add(
                TaskItem(
                    plan_id=plan.id,
                    day_index=int(it.get("day_index", 1)),
                    topic=str(it.get("topic", ""))[:500],
                    keyword=(str(it["keyword"])[:200] if it.get("keyword") else None),
                    plan_time=plan_time,
                    status=TaskItemStatus.PENDING,
                )
            )
        await self.db.commit()
        await self.db.refresh(plan)
        logger.info(
            f"[task_plan] plan created: {plan.id}, days={plan.total_days}, "
            f"daily_time={daily_time}, review_mode={config['review_mode']}"
        )
        return plan

    async def pause_plan(self, user_id: str, plan_id: str) -> bool:
        plan = await self.db.scalar(
            select(TaskPlan).where(TaskPlan.id == plan_id, TaskPlan.user_id == user_id)
        )
        if not plan or plan.status != TaskPlanStatus.ACTIVE:
            return False
        plan.status = TaskPlanStatus.PAUSED
        await self.db.commit()
        return True

    async def resume_plan(self, user_id: str, plan_id: str) -> bool:
        plan = await self.db.scalar(
            select(TaskPlan).where(TaskPlan.id == plan_id, TaskPlan.user_id == user_id)
        )
        if not plan or plan.status != TaskPlanStatus.PAUSED:
            return False
        plan.status = TaskPlanStatus.ACTIVE
        await self.db.commit()
        return True

    async def cancel_plan(self, user_id: str, plan_id: str) -> bool:
        """取消清单：pending 条目全部置 cancelled，running 条目保持（执行中不动）。"""
        plan = await self.db.scalar(
            select(TaskPlan).where(TaskPlan.id == plan_id, TaskPlan.user_id == user_id)
        )
        if not plan or plan.status in (TaskPlanStatus.CANCELLED, TaskPlanStatus.COMPLETED):
            return False
        plan.status = TaskPlanStatus.CANCELLED
        await self.db.execute(
            sa_update(TaskItem)
            .where(TaskItem.plan_id == plan_id, TaskItem.status == TaskItemStatus.PENDING)
            .values(status=TaskItemStatus.CANCELLED)
        )
        await self.db.commit()
        return True

    # ---------- 到期执行 ----------

    async def scan_and_execute(self) -> dict[str, int]:
        """捞到期任务并执行（每分钟 job 调用）。返回统计。"""
        result = {"executed": 0, "failed": 0, "skipped": 0}
        now = _now()
        stale_before = now - timedelta(hours=_STALE_SKIP_HOURS)
        # 只扫 active plan 下到期 pending 条目
        rows = await self.db.execute(
            select(TaskItem, TaskPlan)
            .join(TaskPlan, TaskItem.plan_id == TaskPlan.id)
            .where(
                TaskItem.status == TaskItemStatus.PENDING,
                TaskItem.plan_time <= now,
                TaskPlan.status == TaskPlanStatus.ACTIVE,
            )
            .order_by(TaskItem.plan_time)
            .limit(_SCAN_BATCH_LIMIT)
        )
        for item, plan in rows.all():
            # 积压过期条目直接跳过，避免停机恢复后一次性连发历史内容
            if item.plan_time < stale_before:
                flip = await self.db.execute(
                    sa_update(TaskItem)
                    .where(
                        TaskItem.id == item.id,
                        TaskItem.status == TaskItemStatus.PENDING,
                    )
                    .values(status=TaskItemStatus.SKIPPED)
                )
                if flip.rowcount == 1:
                    logger.warning(
                        f"[task_plan] stale item skipped: plan={plan.id} "
                        f"day={item.day_index} plan_time={item.plan_time.isoformat()} "
                        f"(超过 {_STALE_SKIP_HOURS}h 未执行)"
                    )
                    result["skipped"] += 1
                continue

            ok = await self._execute_item(item, plan)
            if ok:
                result["executed"] += 1
            else:
                result["failed"] += 1
        if any(result.values()):
            await self.db.commit()
        return result

    async def _execute_item(self, item: TaskItem, plan: TaskPlan) -> bool:
        """执行单个到期条目：原子翻转 → 账号预检 → 拉起工作流。"""
        # 原子防重：仅当仍为 pending 时翻转（与扫描 job 的 max_instances=1 双保险）
        flip = await self.db.execute(
            sa_update(TaskItem)
            .where(TaskItem.id == item.id, TaskItem.status == TaskItemStatus.PENDING)
            .values(status=TaskItemStatus.RUNNING)
        )
        if flip.rowcount != 1:
            return False

        # 账号预检：掉线直接失败 + plan 暂停 + 通知，不烧生成 token
        if plan.account_id:
            account = None  # XhsAccount removed
            if not account or account.status.value != "active":
                await self._fail_item(
                    item,
                    plan,
                    reason=f"小红书账号不可用（status={account.status.value if account else 'missing'}）",
                    pause_plan=True,
                )
                await self._notify_feishu_or_fallback(
                    plan,
                    "任务清单已暂停：小红书账号失效",
                    f"清单「{plan.title}」Day {item.day_index} 发布前检测到账号不可用，"
                    f"已暂停全部后续任务。请重新登录后手动恢复。",
                )
                return False

        definition_id = await self._get_pipeline_definition_id()

        logger.warning(
            f"[task_plan] workflow engine not available — item {item.id} marked as pending (no workflow started)"
        )
        item.status = TaskItemStatus.PENDING
        self.db.add(
            TaskRun(
                task_item_id=item.id,
                plan_id=plan.id,
                workflow_id=None,
                status=TaskRunStatus.RUNNING,
            )
        )
        logger.info(
            f"[task_plan] item queued (no workflow engine): plan={plan.id} day={item.day_index}"
        )
        return True

    async def _get_pipeline_definition_id(self) -> str | None:
        return None

    # ---------- 运行状态收敛 ----------

    async def sync_running_runs(self) -> dict[str, int]:
        """运行状态收敛降级为空操作（无工作流引擎）。"""
        return {"published": 0, "failed": 0, "unchanged": 0}

    async def _maybe_complete_plan(self, plan: TaskPlan) -> None:
        """所有条目终态后置 completed。"""
        pending_cnt = await self.db.scalar(
            select(TaskItem.id)
            .where(
                TaskItem.plan_id == plan.id,
                TaskItem.status.in_(
                    [
                        TaskItemStatus.PENDING,
                        TaskItemStatus.RUNNING,
                    ]
                ),
            )
            .limit(1)
        )
        if not pending_cnt:
            plan.status = TaskPlanStatus.COMPLETED

    async def _fail_item(
        self,
        item: TaskItem,
        plan: TaskPlan,
        reason: str,
        run: TaskRun | None = None,
        pause_plan: bool = False,
    ) -> None:
        """失败处理：当日重试一次（30 分钟后重扫），超限置 failed 并通知。

        连续 2 日失败或显式要求 → plan 自动暂停 + 通知（防持续烧 token）。
        """
        config = plan.plan_config or {}
        max_retry = int(config.get("max_retry", 1))
        if item.retry_count < max_retry:
            # 回置 pending，30 分钟后重扫
            item.retry_count += 1
            item.status = TaskItemStatus.PENDING
            item.plan_time = _now() + timedelta(minutes=30)
            item.last_error = {"reason": reason[:500], "at": _now().isoformat()}
            logger.warning(
                f"[task_plan] item retry {item.retry_count}/{max_retry}: "
                f"plan={plan.id} day={item.day_index} reason={reason[:120]}"
            )
            return

        item.status = TaskItemStatus.FAILED
        item.last_error = {"reason": reason[:500], "at": _now().isoformat()}
        plan.failed_days += 1
        if run:
            run.status = TaskRunStatus.FAILED
            run.failure_reason = reason[:500]
            run.finished_at = _now()

        should_pause = pause_plan or plan.failed_days >= 2
        if should_pause and plan.status == TaskPlanStatus.ACTIVE:
            plan.status = TaskPlanStatus.PAUSED
        await self._notify_feishu_or_fallback(
            plan,
            f"定时任务失败（Day {item.day_index}）",
            f"清单「{plan.title}」Day {item.day_index} 执行失败：{reason[:300]}\n"
            + ("连续失败已达 2 天，清单已自动暂停。" if should_pause else "将在下个周期重试。"),
        )
        logger.error(
            f"[task_plan] item failed: plan={plan.id} day={item.day_index} reason={reason[:200]}"
        )

    # ---------- 挂起工作流处理（审核三档 + 服务端渲染） ----------

    async def handle_paused_workflows(self) -> dict[str, int]:
        """paused workflow handling is now a no-op (no workflow engine)."""
        return {"auto_passed": 0, "rendered": 0, "card_sent": 0, "timeout": 0, "unchanged": 0}

    async def handle_card_action(self, payload: dict[str, Any]) -> dict[str, Any]:
        """card action handling is now a no-op (no workflow engine)."""
        return {"success": False, "message": "workflow engine not available in this project"}

    async def _send_confirm_card(
        self, run: TaskRun, plan: TaskPlan, node: str, state_values: dict
    ) -> bool:
        """发飞书确认卡片（三按钮：确认发布/打回重做/跳过本条）。

        卡片回调（card.action.trigger）在 api/routers/feishu_bot.py 处理。
        未配置飞书或发送失败返回 False（调用方降级处理）。
        """
        settings = get_settings()
        if not (settings.feishu_app_id and settings.feishu_app_secret):
            return False
        open_id = (plan.plan_config or {}).get("feishu_open_id")
        if not open_id:
            return False
        try:
            from capabilities.messaging.adapters.feishu import FeishuClient
            from capabilities.rpa.feishu_agent_bridge import BlockKitBuilder

            node_outputs = state_values.get("node_outputs") or {}
            copy_out = node_outputs.get("copywrite") or {}
            item = await self.db.get(TaskItem, run.task_item_id)
            day_index = item.day_index if item else 0
            card = BlockKitBuilder.task_confirm_card(
                plan_title=plan.title,
                day_index=day_index,
                node=node,
                title=str(copy_out.get("title") or wf.topic)[:50],
                content_preview=str(copy_out.get("content") or "")[:150],
                value={
                    "biz": "task_plan",
                    "workflow_id": wf.id,
                    "task_item_id": run.task_item_id,
                    "plan_id": plan.id,
                },
            )
            client = FeishuClient(
                app_id=settings.feishu_app_id,
                app_secret=settings.feishu_app_secret,
            )
            await client.send_card(open_id, card, receive_id_type="open_id")
            return True
        except Exception as e:
            logger.warning(f"[task_plan] send confirm card failed: {e}")
            return False

    async def _notify_feishu_or_fallback(self, plan: TaskPlan, title: str, message: str) -> None:
        """飞书文本通知，不可达时降级站内 SSE 通知。"""
        settings = get_settings()
        open_id = (plan.plan_config or {}).get("feishu_open_id")
        if settings.feishu_app_id and settings.feishu_app_secret and open_id:
            try:
                from capabilities.messaging.adapters.feishu import FeishuClient

                client = FeishuClient(
                    app_id=settings.feishu_app_id,
                    app_secret=settings.feishu_app_secret,
                )
                await client.send_text(
                    open_id, f"【{title}】\n{message}", receive_id_type="open_id"
                )
                return
            except Exception as e:
                logger.warning(f"[task_plan] feishu notify failed, fallback to SSE: {e}")
        await notification_bus.publish(
            "task_plan_event",
            {"plan_id": plan.id, "title": title, "message": message},
        )

    # ---------- 重启恢复 ----------

    async def recover_orphans(self) -> dict[str, int]:
        """orphan recovery is now a no-op (no workflow engine)."""
        return {"converged": 0, "requeued": 0}

    # ---------- 序列化 ----------

    @staticmethod
    def _plan_to_dict(p: TaskPlan) -> dict[str, Any]:
        return {
            "id": p.id,
            "title": p.title,
            "intent_text": p.intent_text,
            "status": p.status.value,
            "account_id": p.account_id,
            "plan_config": p.plan_config or {},
            "total_days": p.total_days,
            "published_days": p.published_days,
            "failed_days": p.failed_days,
            "created_at": p.created_at.isoformat() if p.created_at else None,
        }

    @staticmethod
    def _item_to_dict(it: TaskItem) -> dict[str, Any]:
        return {
            "id": it.id,
            "day_index": it.day_index,
            "topic": it.topic,
            "keyword": it.keyword,
            "plan_time": it.plan_time.isoformat() if it.plan_time else None,
            "status": it.status.value,
            "retry_count": it.retry_count,
            "last_error": it.last_error,
            "workflow_id": it.workflow_id,
        }

    @staticmethod
    def _run_to_dict(r: TaskRun) -> dict[str, Any]:
        return {
            "id": r.id,
            "status": r.status.value,
            "workflow_id": r.workflow_id,
            "failure_reason": r.failure_reason,
            "detail": r.detail or {},
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        }


# 「定时发布流水线」definition id 进程内缓存
_PIPELINE_DEF_CACHE: str | None = None


# ============================================================================
# 调度 job 入口（main.py lifespan 注册，模式同 workflow_cleanup.cleanup_stale_workflows）
# ============================================================================


async def run_task_scan() -> dict[str, int]:
    """每分钟执行：到期任务扫描执行 → 运行状态收敛 → 挂起工作流处理。

    自开 session（AsyncSessionLocal），不依赖请求上下文。
    """
    result: dict[str, int] = {}
    try:
        async with AsyncSessionLocal() as db:
            svc = TaskPlanService(db)
            result.update(await svc.scan_and_execute())
            sync = await svc.sync_running_runs()
            result["sync_published"] = sync["published"]
            result["sync_failed"] = sync["failed"]
            paused = await svc.handle_paused_workflows()
            for k, v in paused.items():
                result[f"paused_{k}"] = v
    except Exception as e:
        logger.exception(f"[task_plan] scan job failed: {e}")
    if any(v for v in result.values()):
        logger.info(f"[task_plan] scan: {result}")
    return result


async def recover_on_startup() -> None:
    """lifespan 启动时收敛重启前 in-flight 的任务（不阻塞启动）。"""
    try:
        async with AsyncSessionLocal() as db:
            await TaskPlanService(db).recover_orphans()
    except Exception as e:
        logger.warning(f"[task_plan] recover_on_startup failed: {e}")
