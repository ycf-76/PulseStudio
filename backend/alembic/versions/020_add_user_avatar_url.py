"""add user avatar_url

Revision ID: 020_user_avatar
Revises: 019_ai_personality
Create Date: 2026-10-24

User 表新增 avatar_url 字段，存储根据邮箱自动解析的头像 URL。
策略：QQ 邮箱 → QQ 头像，其他邮箱 → Gravatar identicon，无邮箱 → 本地默认 SVG。
"""

from alembic import op
import sqlalchemy as sa

revision = "020_user_avatar"
down_revision = "019_ai_personality"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("avatar_url", sa.String(length=1024), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "avatar_url")