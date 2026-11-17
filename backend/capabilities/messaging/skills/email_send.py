"""Email Send Skill — Agent 通过 SMTP 发送邮件给用户。

复用项目已有的 SMTP 配置（smtp_host/port/user/pass），
Agent 可在对话中主动发送邮件通知、报告、创作内容等。
不限 QQ 邮箱，任何邮箱地址均可接收。
"""

from __future__ import annotations

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

from pydantic import BaseModel, Field

from app.core.skill_base import Skill
from app.core.skill_registry import register

logger = logging.getLogger(__name__)


class EmailSendInput(BaseModel):
    to: str = Field(..., description="收件人邮箱地址")
    subject: str = Field(..., description="邮件主题")
    body: str = Field(..., description="邮件正文（纯文本）")
    html_body: str = Field(default="", description="邮件正文（HTML 格式，可选，优先于纯文本）")


def _send_email(
    to_addr: str,
    subject: str,
    plain_body: str,
    html_body: str = "",
) -> dict[str, Any]:
    from app.config import get_settings

    settings = get_settings()
    smtp_host = settings.smtp_host
    smtp_port = settings.smtp_port
    smtp_user = settings.smtp_user
    smtp_pass = settings.smtp_pass
    smtp_from = settings.smtp_from or smtp_user

    if not smtp_host or not smtp_user or not smtp_pass:
        return {
            "ok": False,
            "error": "SMTP not configured. Please set smtp_host, smtp_user, smtp_pass in .env",
        }

    try:
        if html_body:
            msg = MIMEMultipart("alternative")
            msg.attach(MIMEText(plain_body, "plain", "utf-8"))
            msg.attach(MIMEText(html_body, "html", "utf-8"))
        else:
            msg = MIMEText(plain_body, "plain", "utf-8")

        msg["Subject"] = subject
        msg["From"] = f"Pulse Studio <{smtp_from}>"
        msg["To"] = to_addr

        if settings.smtp_use_tls:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as s:
                s.starttls()
                s.login(smtp_user, smtp_pass)
                s.sendmail(smtp_from, [to_addr], msg.as_string())
        else:
            with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=15) as s:
                s.login(smtp_user, smtp_pass)
                s.sendmail(smtp_from, [to_addr], msg.as_string())

        logger.info(f"[EmailSendSkill] email sent to {to_addr}, subject={subject!r}")
        return {"ok": True, "to": to_addr, "subject": subject}

    except smtplib.SMTPAuthenticationError as e:
        logger.error(f"[EmailSendSkill] SMTP auth failed: {e}")
        return {"ok": False, "error": f"SMTP authentication failed: {e}"}
    except smtplib.SMTPRecipientRefused as e:
        logger.error(f"[EmailSendSkill] recipient refused: {e}")
        return {"ok": False, "error": f"Recipient refused: {to_addr}"}
    except Exception as e:
        logger.error(f"[EmailSendSkill] send failed: {e}")
        return {"ok": False, "error": str(e)}


@register
class EmailSendSkill(Skill):
    node_type = "email_send"
    name = "email_send"
    display_name = "邮件发送"
    description = (
        "通过邮件发送消息给用户。当用户要求发送邮件、邮件通知、推送报告到邮箱时调用。"
        "支持纯文本和 HTML 格式。收件人可以是任何邮箱地址（QQ、Gmail、163 等）。"
        "subject 为邮件主题，body 为纯文本正文，html_body 为可选 HTML 正文。"
    )
    platform = "email"
    trigger_words = ["邮件", "email", "邮箱", "发邮件", "发到邮箱", "邮件通知", "邮件推送"]
    input_schema = EmailSendInput

    async def execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        to_addr = inputs.get("to", "").strip()
        subject = inputs.get("subject", "").strip()
        body = inputs.get("body", "").strip()
        html_body = inputs.get("html_body", "").strip()

        if not to_addr:
            ctx = inputs.get("_context")
            if ctx and hasattr(ctx, "user_id") and ctx.user_id:
                from sqlalchemy import select

                from app.core.context_vars import current_db_session
                from app.db.models import User

                db = current_db_session.get()
                if db:
                    result = await db.execute(select(User).where(User.id == ctx.user_id))
                    user = result.scalar_one_or_none()
                    if user and user.email:
                        to_addr = user.email
                        logger.info(
                            f"[EmailSendSkill] to not specified, using current user email: {to_addr}"
                        )

        if not to_addr:
            return {
                "ok": False,
                "error": "No recipient email address. Please provide 'to' parameter or ensure the current user has an email.",
            }

        if not subject:
            return {"ok": False, "error": "subject is required"}

        if not body and not html_body:
            return {"ok": False, "error": "body or html_body is required"}

        return _send_email(to_addr, subject, body, html_body)
