"""side sessions: chat_sessions kind/parent/side_meta + side_snapshots table

Revision ID: 022_side_sessions
Revises: 021_local_video
Create Date: 2026-11-03

侧会话（Side Session）MVP：
1. chat_sessions 新增 kind（main/side）、parent_session_id（无外键）、side_meta（JSON）。
   kind 带 server_default="main"，兼容存量行。
2. 新建 side_snapshots 表（创建时对主会话上下文的一次性快照，一对一挂侧会话）。
"""

import sqlalchemy as sa

from alembic import op

revision = "022_side_sessions"
down_revision = "021_local_video"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chat_sessions",
        sa.Column("kind", sa.String(length=10), nullable=False,
                  server_default="main", comment="main/side"),
    )
    op.add_column(
        "chat_sessions",
        sa.Column("parent_session_id", sa.String(length=26), nullable=True),
    )
    op.add_column(
        "chat_sessions",
        sa.Column("side_meta", sa.JSON(), nullable=True),
    )
    op.create_table(
        "side_snapshots",
        sa.Column("id", sa.String(length=26), primary_key=True),
        sa.Column(
            "session_id", sa.String(length=26),
            sa.ForeignKey("chat_sessions.id", ondelete="CASCADE"),
            nullable=False, unique=True,
        ),
        sa.Column("baseline_message_id", sa.String(length=26), nullable=False,
                  server_default=""),
        sa.Column("granularity", sa.String(length=20), nullable=False),
        sa.Column("recent_n", sa.Integer(), nullable=True),
        sa.Column("content", sa.JSON(), nullable=True),
        sa.Column("token_level", sa.String(length=10), nullable=False,
                  server_default="low"),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("side_snapshots")
    op.drop_column("chat_sessions", "side_meta")
    op.drop_column("chat_sessions", "parent_session_id")
    op.drop_column("chat_sessions", "kind")
