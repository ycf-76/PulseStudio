"""add feedback_events table and make workflow_id nullable on pcp

Revision ID: 017_feedback_events
Revises: 016_video_studio
Create Date: 2026-10-17 00:00:00.000000

1. 创建 feedback_events 表（点赞/点踩/重试反馈管道）
2. published_content_performance.workflow_id 改为 nullable（不再有 workflow 实例）
"""

from alembic import op
import sqlalchemy as sa

revision = "017_feedback_events"
down_revision = "016_video_studio"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "feedback_events",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("user_id", sa.String(128), nullable=False, index=True),
        sa.Column("session_id", sa.String(26), nullable=True, index=True),
        sa.Column("workflow_id", sa.String(26), nullable=True),
        sa.Column(
            "event_type",
            sa.Enum("like", "dislike", "retry", name="feedback_event_type", native_enum=True),
            nullable=False,
        ),
        sa.Column("dimension", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Index("ix_feedback_events_user_id", "user_id"),
        sa.Index("ix_feedback_events_session_id", "session_id"),
        sa.Index("ix_feedback_events_created_at", "created_at"),
    )

    with op.batch_alter_table("published_content_performance") as batch_op:
        batch_op.alter_column("workflow_id", existing_type=sa.String(26), nullable=True)

    with op.batch_alter_table("agent_memories") as batch_op:
        batch_op.alter_column(
            "memory_type",
            existing_type=sa.Enum(
                "preferences", "topic_history", "copywrite_history",
                "publish_history", "my_works_summary", "my_attribution",
                "avoid_patterns",
                name="memorytype",
                native_enum=True,
            ),
            type_=sa.Enum(
                "preferences", "topic_history", "copywrite_history",
                "publish_history", "my_works_summary", "my_attribution",
                "avoid_patterns", "retrospect_insight",
                name="memorytype",
                native_enum=True,
            ),
            nullable=False,
        )


def downgrade() -> None:
    op.drop_table("feedback_events")

    with op.batch_alter_table("published_content_performance") as batch_op:
        batch_op.alter_column("workflow_id", existing_type=sa.String(26), nullable=False)