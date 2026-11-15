"""add detail refresh state to published_content_performance

Revision ID: 024_pcp_detail_refresh
Revises: 023_pcp_wf_nullable
Create Date: 2026-11-05

图集仍是小红书 CDN 外链（签名约 2 天过期）的笔记，每次同步都会被判定"需要拉详情页"，
旧逻辑不限条数也不限重试次数，导致每轮同步都要全量重抓，跑满超时被强杀、整批结果丢失。
新增两列记录详情页刷新的最近尝试时间与累计次数，用于冷却与重试上限（渐进刷新）。
"""

import sqlalchemy as sa

from alembic import op

revision = "024_pcp_detail_refresh"
down_revision = "023_pcp_wf_nullable"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("published_content_performance") as batch_op:
        batch_op.add_column(
            sa.Column("detail_refresh_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "detail_refresh_attempts",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("published_content_performance") as batch_op:
        batch_op.drop_column("detail_refresh_attempts")
        batch_op.drop_column("detail_refresh_at")
