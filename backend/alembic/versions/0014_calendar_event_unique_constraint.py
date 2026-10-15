"""calendar event unique constraint for duplicate prevention

Add a unique constraint on (user_id, title, start_time) to prevent
concurrent duplicate event creation in Google Calendar push.
This is a safety net in addition to application-level read-before-write.

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-15
"""
revision: str = "0014"
down_revision: str = "0013"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa


def upgrade() -> None:
    # Add unique constraint to prevent duplicate events with same title and start time for a user
    # Using a partial index approach: only enforce when calendar_event_id is NULL (not yet pushed to Google)
    # This allows multiple users to have events with same title/start_time, and allows
    # updates to events that already have a calendar_event_id
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_events_user_title_start_time_not_pushed
        ON events (user_id, title, start_time)
        WHERE calendar_event_id IS NULL;
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ux_events_user_title_start_time_not_pushed;")
