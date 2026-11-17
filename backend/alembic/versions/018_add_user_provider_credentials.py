"""Add user_provider_credentials table for BYOK (Bring Your Own Key)

Revision ID: 018_byok
Revises: 017_feedback_events
Create Date: 2026-10-21

用户级模型 Provider 凭据表：
  - 每个用户可以为每个 Provider 配置自己的 API Key
  - API Key AES-GCM 加密存储
  - 支持自定义 Base URL 和自定义模型列表
  - 同一用户同一 Provider 唯一约束
"""

import sqlalchemy as sa

from alembic import op

revision = "018_byok"
down_revision = "017_feedback_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_provider_credentials",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("encrypted_api_key", sa.Text, nullable=False),
        sa.Column("custom_base_url", sa.String(512), nullable=False, server_default=""),
        sa.Column("custom_models", sa.Text, nullable=False, server_default="[]"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_upc_user_id", "user_provider_credentials", ["user_id"])
    op.create_unique_constraint(
        "uq_user_provider", "user_provider_credentials", ["user_id", "provider"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_user_provider", "user_provider_credentials", type_="unique")
    op.drop_index("ix_upc_user_id", table_name="user_provider_credentials")
    op.drop_table("user_provider_credentials")