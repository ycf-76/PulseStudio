"""add video storyboards and video jobs tables

Revision ID: 016_video_studio
Revises: 014
Create Date: 2026-10-16 16:20:00.000000

AI 视频工坊任务层（docs/VIDEO_ARCHITECTURE_ALIGNMENT_MIGRATION_PLAN.md §4）：
1. video_storyboards - 分镜会话态（draft/confirmed/composing/completed），shots 内嵌 JSON
2. video_jobs - 异步任务四态状态机（queued/running/succeeded/failed），Worker 认领执行

注意：015_add_workflow_metrics_and_evolution_proposals.py 为空文件（无 revision 声明），
当前 head 仍为 014，故本迁移 down_revision = "014"。
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "016_video_studio"
down_revision = "014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ===== Table 1: video_storyboards =====
    op.create_table(
        "video_storyboards",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("session_id", sa.String(128), nullable=True),
        sa.Column("title", sa.String(255), server_default="", nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), server_default="draft", nullable=False),
        sa.Column("ratio", sa.String(8), server_default="9:16", nullable=False),
        sa.Column("shot_duration", sa.Float(), server_default="5.0", nullable=False),
        sa.Column("style", sa.String(32), server_default="", nullable=False),
        sa.Column("scene_description", sa.JSON(), nullable=True),
        sa.Column("shots", sa.JSON(), nullable=False),
        sa.Column("reference_asset_ids", sa.JSON(), nullable=False),
        sa.Column("final_asset_id", sa.String(26), nullable=True),
        sa.Column("final_url", sa.String(512), nullable=True),
        sa.Column("degradation", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_vs_user_id", "video_storyboards", ["user_id"])
    op.create_index("ix_vs_status", "video_storyboards", ["status"])
    op.create_index("ix_vs_created_at", "video_storyboards", ["created_at"])

    # ===== Table 2: video_jobs =====
    op.create_table(
        "video_jobs",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("storyboard_id", sa.String(26), nullable=False),
        sa.Column("shot_id", sa.String(26), nullable=True),
        sa.Column("job_type", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), server_default="queued", nullable=False),
        sa.Column("progress", sa.Integer(), server_default="0", nullable=False),
        sa.Column("provider", sa.String(16), server_default="mock", nullable=False),
        sa.Column("model", sa.String(64), server_default="", nullable=False),
        sa.Column("input", sa.JSON(), nullable=True),
        sa.Column("result_asset_id", sa.String(26), nullable=True),
        sa.Column("result_url", sa.String(512), nullable=True),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("credits_est", sa.Float(), server_default="0.0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_vj_user_status", "video_jobs", ["user_id", "status"])
    op.create_index("ix_vj_storyboard_id", "video_jobs", ["storyboard_id"])
    op.create_index("ix_vj_status_created", "video_jobs", ["status", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_vj_status_created", table_name="video_jobs")
    op.drop_index("ix_vj_storyboard_id", table_name="video_jobs")
    op.drop_index("ix_vj_user_status", table_name="video_jobs")
    op.drop_table("video_jobs")
    op.drop_index("ix_vs_created_at", table_name="video_storyboards")
    op.drop_index("ix_vs_status", table_name="video_storyboards")
    op.drop_index("ix_vs_user_id", table_name="video_storyboards")
    op.drop_table("video_storyboards")