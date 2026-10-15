"""users.token_version for session revocation

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-11 09:00:00.000000

Step NF-1 (session revocation / logout).

Every auth token carries the subject's session epoch (claim ``ver``). Adding
``users.token_version`` gives that epoch a durable home so ``POST
/api/auth/logout`` can invalidate every previously issued token for a user by
incrementing one integer - no server-side session table required.

Idempotent for coexistence with create_all (the test schema and fresh
installs create the column from the model before Alembic runs). Existing rows
receive 0, and pre-existing tokens without a ``ver`` claim are treated as 0, so
nothing is invalidated merely by applying this migration.
"""
revision: str = "0011"
down_revision: str = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from alembic import op as _op

    op = _op
    op.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS token_version "
        "INTEGER NOT NULL DEFAULT 0"
    )


def downgrade() -> None:
    from alembic import op as _op

    op = _op
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS token_version")
