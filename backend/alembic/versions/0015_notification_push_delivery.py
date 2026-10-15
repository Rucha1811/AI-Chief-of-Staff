"""track browser push delivery for notifications

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-15
"""

from alembic import op
import sqlalchemy as sa


revision: str = "0015"
down_revision: str = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("notifications", sa.Column("push_sent_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("notifications", "push_sent_at")
