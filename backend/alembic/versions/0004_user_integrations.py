"""create user_integrations table

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-13 15:20:00.000000

Per-user integration connections (provider, account, status, encrypted
token columns, sync metadata). One connection per (user, provider);
idempotent for coexistence with create_all.
"""
revision: str = '0004'
down_revision: str = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    from alembic import op as _op

    op = _op
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS user_integrations (
            id BIGSERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            provider VARCHAR(64) NOT NULL,
            account_id VARCHAR(512),
            status VARCHAR(16) DEFAULT 'active' NOT NULL,
            refresh_token_encrypted TEXT,
            access_token_encrypted TEXT,
            token_expires_at TIMESTAMP,
            scopes TEXT,
            last_synced_at TIMESTAMP,
            last_error TEXT,
            created_at TIMESTAMP DEFAULT now() NOT NULL,
            updated_at TIMESTAMP DEFAULT now() NOT NULL
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_user_integrations_user_provider "
        "ON user_integrations (user_id, provider)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_user_integrations_provider "
        "ON user_integrations (provider)"
    )


def downgrade() -> None:
    from alembic import op as _op

    op = _op
    op.execute("DROP TABLE IF EXISTS user_integrations")