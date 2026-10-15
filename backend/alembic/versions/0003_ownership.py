"""add user_id ownership to all user-owned tables

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-13 15:10:00.000000

Every user-owned table gains a NOT NULL user_id backfilled to the earliest
(owner) user, plus FK + index. Idempotent so it is safe on fresh installs
where create_all already built user_id into the schema.

Also converts daily_briefings' UNIQUE(date) into UNIQUE(user_id, date) so
multiple users can each have today's briefing.
"""
from typing import List

revision: str = '0003'
down_revision: str = '0002'
branch_labels = None
depends_on = None

# (table, unique_user_columns or None) — all are user-owned.
_TABLES: List[str] = [
    "emails",
    "events",
    "tasks",
    "assignments",
    "attendance",
    "timetable_entries",
    "notifications",
    "whatsapp_messages",
    "documents",
    "daily_briefings",
    "exams",
    "push_subscriptions",
    "google_auth_state",
]


def _backfill_user_id_sql(table: str) -> str:
    return (
        f"UPDATE {table} SET user_id = "
        "(SELECT id FROM users ORDER BY id ASC LIMIT 1) WHERE user_id IS NULL"
    )


def _add_fk_sql(table: str) -> str:
    return f"""
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1
            FROM pg_constraint c
            JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey)
            WHERE c.conrelid = '{table}'::regclass
              AND c.contype = 'f'
              AND a.attname = 'user_id'
        ) THEN
            ALTER TABLE {table}
                ADD CONSTRAINT fk_{table}_user
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
        END IF;
    END $$
    """


def upgrade() -> None:
    from alembic import op as _op

    op = _op
    for table in _TABLES:
        op.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS user_id BIGINT")
        op.execute(_backfill_user_id_sql(table))
        op.execute(f"ALTER TABLE {table} ALTER COLUMN user_id SET NOT NULL")
        op.execute(_add_fk_sql(table))
        op.execute(f"CREATE INDEX IF NOT EXISTS ix_{table}_user_id ON {table} (user_id)")

    # Multi-user fix: the daily briefing unique constraint must be per-user.
    op.execute("DROP INDEX IF EXISTS ix_daily_briefings_date")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_daily_briefings_user_date "
        "ON daily_briefings (user_id, date)"
    )


def downgrade() -> None:
    from alembic import op as _op

    op = _op
    for table in reversed(_TABLES):
        op.execute(f"DROP INDEX IF EXISTS ix_{table}_user_id")
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS user_id")
    op.execute("DROP INDEX IF EXISTS ux_daily_briefings_user_date")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_daily_briefings_date "
        "ON daily_briefings (date)"
    )