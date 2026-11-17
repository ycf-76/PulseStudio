"""Feishu Bitable Skill — 创建/打开飞书多维表格。

包装 FeishuClient 的 bitable v1 能力为 Skill 工具，让智能体能够：
- 创建多维表格应用（Bitable App）
- 在应用中创建数据表（含字段定义）
- 批量写入结构化记录
- 打开/读取已有表格（解析链接 → 字段 + 记录预览）

与 feishu_wiki_doc（知识库/文档）互补：本技能专注结构化表格数据。
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field

from app.core.skill_base import Skill
from app.core.skill_registry import register

logger = logging.getLogger(__name__)

# Bitable 字段类型编码（官方 bitable v1 规范）
FIELD_TYPES = "1=文本 2=数字 3=单选 4=多选 5=日期 7=复选框 11=人员 13=电话 15=超链接 17=附件"


class FeishuBitableInput(BaseModel):
    operation: str = Field(
        ...,
        description=(
            "操作类型：create_app(创建多维表格应用)、create_table(在应用中创建数据表，含字段定义)、"
            "add_records(向数据表批量写入记录)、open_table(打开/读取已有表格的字段与记录)"
        ),
    )
    url: str = Field(
        default="",
        description="open_table 时：多维表格链接（https://xxx.feishu.cn/base/<app_token>?table=<table_id>），提供后自动解析，无需再传 app_token/table_id",
    )
    name: str = Field(
        default="", description="create_app 时多维表格的名称（用简短业务名，如「小红书内容排期」）"
    )
    folder_token: str = Field(
        default="", description="create_app 可选：目标文件夹 token，留空则在云空间根目录创建"
    )
    app_token: str = Field(
        default="",
        description="create_table/add_records 时：多维表格 app_token。从 create_app 返回值或飞书链接 .../base/<app_token> 获取",
    )
    table_name: str = Field(default="", description="create_table 时数据表的名称")
    fields: list[dict[str, Any]] = Field(
        default=[],
        description=(
            f'create_table 时字段定义列表，每项 {{"field_name": 字段名, "type": 类型编码}}，类型编码：{FIELD_TYPES}。'
            "第一个字段建议用 1(文本) 作为主字段"
        ),
    )
    table_id: str = Field(
        default="", description="add_records 时：数据表 table_id，从 create_table 返回值获取"
    )
    records: list[dict[str, Any]] = Field(
        default=[],
        description="add_records 时要写入的记录列表，每项为 {字段名: 值}，键必须与字段名完全一致",
    )


@register
class FeishuBitableSkill(Skill):
    node_type = "feishu_bitable"
    name = "feishu_bitable"
    display_name = "飞书多维表格"
    description = (
        "创建/打开飞书多维表格：创建多维表格应用(create_app)、在应用中创建数据表并定义字段(create_table)、"
        "向数据表批量写入记录(add_records)、打开/读取已有表格的字段与记录(open_table，支持直接传链接)。"
        "适用于「帮我在飞书建个多维表格存/整理数据」以及「打开/查看这个多维表格」场景。"
        "与 feishu_wiki_doc(知识库/文档) 互补，本技能专注结构化表格。"
    )
    platform = "feishu"
    trigger_words = ["飞书", "feishu", "lark", "多维表格", "bitable", "表格", "数据表"]
    prompt_guidance = (
        "【飞书多维表格操作 — 最高优先级】\n"
        "当用户要求「创建多维表格 / 建表并把数据放进去」时，必须用 feishu_bitable，禁止改走知识库建文档。\n"
        "从零建表的三步流程（务必按顺序串行执行，每步的返回值是下一步的入参）：\n"
        "1. feishu_bitable(operation='create_app', name='业务名称') → 记下返回的 app_token\n"
        f"2. feishu_bitable(operation='create_table', app_token=..., table_name=..., fields=[{{'field_name':'名称','type':1}},...]) → 记下返回的 table_id。"
        f"字段类型编码：{FIELD_TYPES}；根据用户数据推断字段，第一个字段用 1(文本) 做主字段\n"
        "3. feishu_bitable(operation='add_records', app_token=..., table_id=..., records=[{字段名:值},...]) → 批量写入\n"
        "完成后把返回的 url 告诉用户，可直接在飞书打开查看。\n"
        "【打开/查看已有多维表格】当用户发来多维表格链接并要求「打开/查看/读取」时，用 "
        "feishu_bitable(operation='open_table', url='用户给的链接')：返回表名、字段、记录预览和 url。"
        "务必把返回的 url 原样发到聊天里——用户点击链接即可在右侧栏直接查看表格视图；不要只口述数据。\n"
        "注意：向已有表格写数据时，让用户提供多维表格链接（从中取 app_token）或先用对应工具查询；"
        "若只是想归档非结构化内容（文章/笔记），应该用 feishu_wiki_doc 而不是多维表格。"
    )
    input_schema = FeishuBitableInput

    async def execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        operation = (inputs.get("operation") or "").strip().lower()

        try:
            from app.config import get_settings
            from capabilities.messaging.adapters.feishu import FeishuAPIError, FeishuClient

            settings = get_settings()
            if not (settings.feishu_app_id and settings.feishu_app_secret):
                return {
                    "ok": False,
                    "error": "飞书尚未配置。请先在 设置 > 飞书 填入 App ID 和 App Secret。",
                }
            client = FeishuClient(
                app_id=settings.feishu_app_id,
                app_secret=settings.feishu_app_secret,
            )
        except ImportError as e:
            return {"ok": False, "error": f"飞书模块不可用: {e}"}

        ctx = inputs.get("_context")
        user_id = ctx.user_id if ctx and hasattr(ctx, "user_id") else "default"
        logger.info(f"[FeishuBitableSkill] user_id={user_id}, operation={operation}")
        access_token = await self._get_user_access_token(user_id)

        try:
            if operation == "create_app":
                return await self._create_app(client, inputs, access_token)
            if operation == "create_table":
                return await self._create_table(client, inputs, access_token)
            if operation == "add_records":
                return await self._add_records(client, inputs, access_token)
            if operation == "open_table":
                return await self._open_table(client, inputs, access_token)
            return {
                "ok": False,
                "error": f"未知操作: {operation}，可用：create_app/create_table/add_records/open_table",
            }
        except FeishuAPIError as e:
            logger.error(f"[FeishuBitableSkill] FeishuAPIError: {e}")
            hint = self._permission_hint(e)
            return {"ok": False, "error": f"飞书API错误: {e.msg} (code={e.code}){hint}"}
        except Exception as e:
            logger.exception(f"[FeishuBitableSkill] {operation} failed")
            return {"ok": False, "error": f"操作失败: {e}"}

    # -------------------- 操作实现 --------------------

    async def _get_user_access_token(self, user_id: str) -> str | None:
        """按当前聊天用户取 user_access_token；未绑定时返回 None，回退应用身份。"""
        try:
            from app.core.context_vars import current_db_session
            from capabilities.messaging.services.feishu_oauth import get_valid_user_access_token

            db = current_db_session.get(None)
            if db is not None and user_id and user_id != "default":
                return await get_valid_user_access_token(db, user_id)
        except Exception as exc:
            logger.warning(
                f"[FeishuBitableSkill] user token unavailable, fallback tenant token: {exc}"
            )
        return None

    async def _create_app(
        self, client: Any, inputs: dict[str, Any], access_token: str | None = None
    ) -> dict[str, Any]:
        name = (inputs.get("name") or "").strip()
        if not name:
            return {
                "ok": False,
                "error": "create_app 需要提供 name（多维表格名称，如「小红书内容排期」）",
            }

        created = await client.create_bitable_app(
            name,
            folder_token=(inputs.get("folder_token") or "").strip(),
            access_token=access_token,
        )
        return {
            "ok": True,
            "app_token": created["app_token"],
            "url": created["url"],
            "hint": "下一步用 operation='create_table' 在该应用中创建数据表（传 app_token），定义字段后写入记录",
        }

    async def _create_table(
        self, client: Any, inputs: dict[str, Any], access_token: str | None = None
    ) -> dict[str, Any]:
        app_token = (inputs.get("app_token") or "").strip()
        table_name = (inputs.get("table_name") or "").strip()
        fields = inputs.get("fields") or []
        if not app_token:
            return {
                "ok": False,
                "error": "create_table 需要提供 app_token（可先用 create_app 创建）",
            }
        if not table_name:
            return {"ok": False, "error": "create_table 需要提供 table_name（数据表名称）"}
        if not isinstance(fields, list) or not fields:
            return {
                "ok": False,
                "error": f"create_table 需要提供 fields（字段定义列表，每项 {{'field_name': 名称, 'type': 编码}}，编码：{FIELD_TYPES}）",
            }

        created = await client.create_bitable_table(
            app_token,
            table_name,
            fields=fields,
            access_token=access_token,
        )
        return {
            "ok": True,
            "app_token": app_token,
            "table_id": created["table_id"],
            "field_names": [f.get("field_name", "") for f in fields],
            "hint": "下一步用 operation='add_records' 写入数据（传 app_token + table_id，records 的键必须与上面的 field_names 完全一致）",
        }

    async def _add_records(
        self, client: Any, inputs: dict[str, Any], access_token: str | None = None
    ) -> dict[str, Any]:
        app_token = (inputs.get("app_token") or "").strip()
        table_id = (inputs.get("table_id") or "").strip()
        records = inputs.get("records") or []
        if not app_token or not table_id:
            return {
                "ok": False,
                "error": "add_records 需要提供 app_token 和 table_id（从 create_app/create_table 返回值获取）",
            }
        if not isinstance(records, list) or not records:
            return {
                "ok": False,
                "error": "add_records 需要提供 records（记录列表，每项为 {字段名: 值}）",
            }

        created = await client.batch_create_bitable_records(app_token, table_id, records)
        return {
            "ok": True,
            "app_token": app_token,
            "table_id": table_id,
            "created_count": len(created),
            "url": f"https://feishu.cn/base/{app_token}",
            "hint": "数据已写入，把 url 告诉用户可在飞书直接打开查看",
        }

    async def _open_table(
        self, client: Any, inputs: dict[str, Any], access_token: str | None = None
    ) -> dict[str, Any]:
        """打开/读取已有多维表格：解析链接 → 字段 + 记录（扁平化预览）。"""
        from fastapi import HTTPException

        from app.api.routers.feishu_docs import _cell_to_text, _parse_bitable_url

        url = (inputs.get("url") or "").strip()
        app_token = (inputs.get("app_token") or "").strip()
        table_id = (inputs.get("table_id") or "").strip()

        if url and not (app_token and table_id):
            try:
                parsed_app, parsed_table = _parse_bitable_url(url)
            except HTTPException as e:
                return {"ok": False, "error": str(e.detail)}
            app_token = app_token or parsed_app
            table_id = table_id or (parsed_table or "")

        if not app_token:
            return {"ok": False, "error": "open_table 需要提供多维表格链接（url）或 app_token"}

        tables = await client.list_bitable_tables(app_token, access_token=access_token)
        if not tables:
            return {"ok": False, "error": "该多维表格中没有任何数据表"}
        table = (
            next((t for t in tables if t.get("table_id") == table_id), None)
            if table_id
            else tables[0]
        )
        if table is None:
            return {
                "ok": False,
                "error": f"table_id={table_id} 不存在",
                "available_tables": [t.get("name") or t.get("table_id") for t in tables],
            }
        table_id = table["table_id"]

        fields = await client.list_bitable_fields(app_token, table_id, access_token=access_token)
        raw_records = await client.search_bitable_records(
            app_token, table_id, access_token=access_token
        )

        type_by_name = {(f.get("field_name") or ""): int(f.get("type") or 0) for f in fields}
        records: list[dict[str, str]] = []
        for r in raw_records:
            flat = {
                name: _cell_to_text(value, type_by_name.get(name, 0))
                for name, value in (r.get("fields") or {}).items()
            }
            records.append({k: v for k, v in flat.items() if v})

        preview_limit = 50
        return {
            "ok": True,
            "app_token": app_token,
            "table_id": table_id,
            "table_name": table.get("name") or "",
            "field_names": [f.get("field_name") or "" for f in fields],
            "total_records": len(records),
            "records_preview": records[:preview_limit],
            "url": f"https://feishu.cn/base/{app_token}?table={table_id}",
            "hint": (
                f"共 {len(records)} 条记录，已预览前 {min(preview_limit, len(records))} 条。"
                "把返回的 url 原样发到聊天里，用户点击链接即可在右侧栏查看完整表格视图"
            ),
        }

    # -------------------- 辅助 --------------------

    @staticmethod
    def _permission_hint(e: Any) -> str:
        """根据错误码给出权限排查提示。"""
        code = getattr(e, "code", 0)
        if code in (99991672, 99991661, 99991679, 230002):
            return (
                "。可能缺少权限：请在飞书开放平台为应用开通 多维表格(bitable:app) 权限并发布版本；"
                "应用身份时还需确认应用对目标多维表格有编辑权限（文档右上角「...」> 添加文档应用）"
            )
        if code == 99991663:
            return "。无权限访问该 app_token 对应的多维表格，请确认表格已授权"
        return ""
