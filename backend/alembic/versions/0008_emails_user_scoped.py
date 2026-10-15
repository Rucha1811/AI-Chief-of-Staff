"""emails message_id user-scoped

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-03 09:00:00.000000

Step P2.2 (global uniqueness review):
- replace the GLOBAL unique index on emails.message_id with a per-user
  compound unique index (user_id, message_id). Gmail message ids are unique
  per mailbox (per user, since each user owns exactly one connected Google
  account), so a global key lets User A's message id block User B's identical
  id from being inserted and duplicates the single-user assumption in the
  Gmail sync dedup.

Idempotent for coexistence with create_all (the fresh create_all schema built
from the model carries the compound index and no legacy global key):
- drop both legacy forms of the global unique: the UNIQUE CONSTRAINT that
  create_all adds for the old model's unique=True column (emails_message_id_key)
  and the UNIQUE INDEX present in existing/legacy databases
  (ix_emails_message_id).
- message_id is set NOT NULL: it is a required provider identifier in every
  write path (Gmail sync skips missing ids; ingest schema requires it), and
  the current database has zero NULL rows.

Collision check outcome (recorded at migration time; no data is deleted):
every existing row has DISTINCT (user_id, message_id) — the composite can be
created without any conflict resolution.
"""
revision: str = '0008'
down_revision: str = '0007'
branch_labels = None
depends_on = None

_COMPOUND_INDEX = "ux_emails_user_message_id"
_LEGACY_CONSTRAINT = "emails_message_id_key"
_LEGACY_INDEX = "ix_emails_message_id"


def upgrade() -> None:
    from alembic import op as _op

    op = _op
    op.execute(f"ALTER TABLE emails DROP CONSTRAINT IF EXISTS {_LEGACY_CONSTRAINT}")
    op.execute(f"DROP INDEX IF EXISTS {_LEGACY_INDEX}")
    op.execute("ALTER TABLE emails ALTER COLUMN message_id SET NOT NULL")
    op.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS {_COMPOUND_INDEX} "
        "ON emails (user_id, message_id)"
    )


def downgrade() -> None:
    from alembic import op as _op

    op = _op
    op.execute(f"DROP INDEX IF EXISTS {_COMPOUND_INDEX}")
    op.execute(
        "ALTER TABLE emails ALTER COLUMN message_id DROP NOT NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS "
        f"{_LEGACY_INDEX} ON emails (message_id)"
    )