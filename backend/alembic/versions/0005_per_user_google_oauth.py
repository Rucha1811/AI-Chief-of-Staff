"""per-user google oauth schema

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-13 17:00:00.000000

Step 10 (per-user Google OAuth):
- add user_integrations.meta JSONB (oauth state nonce, google userinfo)
- make google_auth_state one row per user (unique index on user_id)

Idempotent for coexistence with create_all.
"""
revision: str = '0005'
down_revision: str = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    from alembic import op as _op

    op = _op
    op.execute(
        "ALTER TABLE user_integrations ADD COLUMN IF NOT EXISTS meta JSONB"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_google_auth_state_user "
        "ON google_auth_state (user_id)"
    )


def downgrade() -> None:
    from alembic import op as _op

    op = _op
    op.execute("DROP INDEX IF EXISTS ux_google_auth_state_user")
    op.execute("ALTER TABLE user_integrations DROP COLUMN IF EXISTS meta")