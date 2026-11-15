"""add_ai_personality

Revision ID: 019_ai_personality
Revises: 018_byok
Create Date: 2026-10-23

用户画像新增 AI 人格字段：
  - ai_personality: 预设人格名（default/spicy/dramatic/deadpan/netslang/custom）
  - ai_personality_custom: 用户自建人格的 prompt 文本（仅 ai_personality=custom 时使用）
"""

from alembic import op
import sqlalchemy as sa

revision = "019_ai_personality"
down_revision = "018_byok"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_profiles",
        sa.Column("ai_personality", sa.String(length=50), nullable=True, server_default="default"),
    )
    op.add_column(
        "user_profiles",
        sa.Column("ai_personality_custom", sa.Text, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user_profiles", "ai_personality_custom")
    op.drop_column("user_profiles", "ai_personality")