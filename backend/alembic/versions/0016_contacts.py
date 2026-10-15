"""add Google contacts

Revision ID: 0016
Revises: 0015
"""

from alembic import op
import sqlalchemy as sa

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "contacts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("resource_name", sa.String(256), nullable=False),
        sa.Column("display_name", sa.String(512), nullable=False),
        sa.Column("emails", sa.Text(), nullable=True),
        sa.Column("phone_numbers", sa.Text(), nullable=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "resource_name", name="ux_contacts_user_resource"),
    )


def downgrade() -> None:
    op.drop_table("contacts")
