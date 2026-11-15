"""repair published_content_performance.workflow_id to nullable

Revision ID: 023_pcp_wf_nullable
Revises: 022_side_sessions
Create Date: 2026-11-04

017 已声明把 workflow_id 改为 nullable，但部分环境（表由旧版模型建出）仍是 NOT NULL，
导致 Spider 采集入库（workflow_id=None）触发 1048 错误并中断整个同步任务。
此处显式修复该列约束，保证升级后各环境一致。
"""

from alembic import op
import sqlalchemy as sa

revision = "023_pcp_wf_nullable"
down_revision = "022_side_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("published_content_performance") as batch_op:
        batch_op.alter_column(
            "workflow_id", existing_type=sa.String(26), nullable=True
        )


def downgrade() -> None:
    with op.batch_alter_table("published_content_performance") as batch_op:
        batch_op.alter_column(
            "workflow_id", existing_type=sa.String(26), nullable=False
        )