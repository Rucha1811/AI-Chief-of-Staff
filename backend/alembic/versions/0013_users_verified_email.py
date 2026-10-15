"""users verified institutional email (CHARUSAT attendance eligibility)

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-15 15:00:00.000000

The CHARUSAT attendance feature may only be offered to accounts whose
institutional identity an identity provider has actually confirmed. The
registrable ``users.email`` field is user-supplied at registration and is
therefore not proof of anything, so this migration adds the two columns that
hold a provider-confirmed address:

- ``email_verified``: the provider reported the address as verified.
- ``verified_email``: the address the provider verified.

Both default to "nothing verified" (``false`` / NULL), so applying this
migration grants nobody eligibility and changes no existing behaviour.

Idempotent for coexistence with create_all (the test schema and fresh installs
create the columns from the model before Alembic runs).
"""
revision: str = "0013"
down_revision: str = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from alembic import op as _op

    op = _op
    op.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verified "
        "BOOLEAN NOT NULL DEFAULT false"
    )
    op.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS verified_email VARCHAR(512)"
    )


def downgrade() -> None:
    from alembic import op as _op

    op = _op
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS verified_email")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS email_verified")
