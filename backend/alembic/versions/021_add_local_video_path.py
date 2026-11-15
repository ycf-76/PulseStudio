"""add local_video_path to published_content_performance

Revision ID: 021_local_video
Revises: 020_user_avatar
Create Date: 2026-11-01

作品表新增 local_video_path 字段，存储 yt-dlp 下载的视频本地落盘路径，
前端作品详情页据此播放/提供本地文件。
"""

from alembic import op
import sqlalchemy as sa

revision = "021_local_video"
down_revision = "020_user_avatar"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "published_content_performance",
        sa.Column("local_video_path", sa.String(length=1024), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("published_content_performance", "local_video_path")
