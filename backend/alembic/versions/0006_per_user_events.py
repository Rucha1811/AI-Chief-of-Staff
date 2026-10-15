"""per-user events uniqueness

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-15 12:00:00.000000

Step 11 (per-user sync):
- replace the GLOBAL unique index on events.calendar_event_id with a
  per-user compound unique index (user_id, calendar_event_id). Two users in
  the same Google Classroom share coursework ids, so the old global unique
  index made multi-user sync impossible.

Idempotent for coexistence with create_all.
"""
revision: str = '0006'
down_revision: str = '0005'
branch_labels = None
depends_on = None

_COMPOUND_INDEX = "ux_events_user_calendar_event_id"


def upgrade() -> None:
    from alembic import op as _op

    op = _op
    # Fresh create_all DBs (tests, new installs) already have the compound
    # index from the model and no legacy constraint; prod DBs need both steps.
    # The legacy unique=True column produced a named UNIQUE CONSTRAINT (with a
    # backing index), so drop it as a constraint.
    op.execute(
        "ALTER TABLE events DROP CONSTRAINT IF EXISTS events_calendar_event_id_key"
    )
    op.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS {_COMPOUND_INDEX} "
        "ON events (user_id, calendar_event_id)"
    )


def downgrade() -> None:
    from alembic import op as _op

    op = _op
    op.execute(f"DROP INDEX IF EXISTS {_COMPOUND_INDEX}")
    op.execute(
        "DO $$ BEGIN "
        "IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'events_calendar_event_id_key') "
        "THEN ALTER TABLE events ADD CONSTRAINT events_calendar_event_id_key UNIQUE (calendar_event_id); "
        "END IF; END $$;"
    )