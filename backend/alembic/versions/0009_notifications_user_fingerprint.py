"""notifications fingerprint user-scoped

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-03 12:00:00.000000

Step P2.2 (global uniqueness review): notifications.fingerprint.

reminder_service.generate_reminders resolves duplicates per user
(``WHERE fingerprint = :fp AND user_id = :uid``), so the database key was
STRICTER than the application: one user's fingerprint silently suppressed the
identical fingerprint of a different user (the app reported "created", the
database rejected the row). Notifications are per-user rows, so the dedup key
is user-scoped and becomes UNIQUE (user_id, fingerprint).

Read-only preflight: if any (user_id, fingerprint) duplicate exists the
migration raises and NOTHING is written. It never deletes, merges, or
re-assigns rows - the operator must resolve the conflict explicitly.

NULL behaviour is preserved exactly: PostgreSQL treats NULLs as distinct in a
unique index, so multiple rows with a NULL fingerprint are allowed before and
after. (Today no row has a NULL fingerprint; reminder_service always sets
one.)

Identifiers deliberately NOT changed here - see the P2.2 audit:
- whatsapp_messages.message_id: globally unique per provider spec (Twilio
  MessageSid, Meta wamid), not user-derived.
- push_subscriptions.endpoint: one Web Push endpoint is one browser
  registration, unique regardless of which user is signed in.
- emails.message_id and events.calendar_event_id: already per-user (0008, 0006).

Idempotent for coexistence with create_all (a fresh create_all schema is built
from the model, which already carries the composite constraint and no legacy
global key), and it removes the legacy global uniqueness in both forms it can
exist as - the UNIQUE CONSTRAINT create_all emits for the old model's
unique=True column (notifications_fingerprint_key) and the UNIQUE INDEX present
in existing databases (ix_notifications_fingerprint).
"""
from sqlalchemy import text

revision: str = '0009'
down_revision: str = '0008'
branch_labels = None
depends_on = None

_COMPOSITE_INDEX = "ux_notifications_user_fingerprint"
_LEGACY_GLOBAL_INDEX = "ix_notifications_fingerprint"
# A schema built by create_all from the OLD model (fingerprint = unique=True)
# carries the global uniqueness as a UNIQUE CONSTRAINT rather than an index.
# It must be dropped too, otherwise global uniqueness would silently survive
# and defeat the whole point of this migration.
_LEGACY_GLOBAL_CONSTRAINT = "notifications_fingerprint_key"


def _assert_no_user_scoped_duplicates(op) -> None:
    """Abort if (user_id, fingerprint) duplicates exist. Read-only."""
    duplicates = op.get_bind().execute(
        text(
            "SELECT user_id, fingerprint, count(*) AS rows, "
            "       array_agg(id ORDER BY id) AS ids "
            "FROM notifications "
            "GROUP BY user_id, fingerprint "
            "HAVING count(*) > 1 "
            "ORDER BY count(*) DESC, user_id LIMIT 20"
        )
    ).fetchall()
    if duplicates:
        detail = "\n".join(
            f"  user_id={row[0]} fingerprint={row[1]!r} rows={row[2]} ids={row[3]}"
            for row in duplicates
        )
        raise RuntimeError(
            "Cannot create UNIQUE (user_id, fingerprint) on notifications: "
            f"{len(duplicates)} duplicate group(s) already exist. No rows were "
            "changed. Resolve these duplicates manually, then re-run:\n" + detail
        )


def upgrade() -> None:
    from alembic import op as _op

    op = _op
    # 1. Preflight (read-only, aborts on conflict).
    _assert_no_user_scoped_duplicates(op)
    # 2. Drop the over-strict global unique in both forms it can exist as: the
    #    UNIQUE CONSTRAINT create_all emits for the old unique=True column, and
    #    the UNIQUE INDEX present in existing databases.
    op.execute(
        f"ALTER TABLE notifications DROP CONSTRAINT IF EXISTS {_LEGACY_GLOBAL_CONSTRAINT}"
    )
    op.execute(f"DROP INDEX IF EXISTS {_LEGACY_GLOBAL_INDEX}")
    # 3. Per-user composite uniqueness. NULLs stay distinct (unchanged).
    #    IF NOT EXISTS covers a create_all schema that already declares the
    #    constraint of the same name.
    op.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS {_COMPOSITE_INDEX} "
        "ON notifications (user_id, fingerprint)"
    )


def downgrade() -> None:
    from alembic import op as _op

    op = _op
    # Restoring global uniqueness FAILS if two users share a fingerprint, which
    # is exactly what upgrade() permits. Preflight and abort rather than
    # deleting or re-attributing a user's notification.
    clashes = op.get_bind().execute(
        text(
            "SELECT fingerprint, count(DISTINCT user_id) AS users, "
            "       array_agg(DISTINCT user_id ORDER BY user_id) AS user_ids "
            "FROM notifications "
            "WHERE fingerprint IS NOT NULL "
            "GROUP BY fingerprint "
            "HAVING count(DISTINCT user_id) > 1 "
            "ORDER BY fingerprint LIMIT 20"
        )
    ).fetchall()
    if clashes:
        detail = "\n".join(
            f"  fingerprint={row[0]!r} shared by {row[1]} users: {row[2]}"
            for row in clashes
        )
        raise RuntimeError(
            "Cannot restore the GLOBAL unique index on notifications.fingerprint: "
            f"{len(clashes)} fingerprint(s) are now shared across users. No rows "
            "were changed. Downgrade aborted:\n" + detail
        )
    # The composite may exist either as a standalone index (existing databases)
    # or as the UNIQUE CONSTRAINT a create_all schema gets from the model
    # declaration; drop the constraint first, then any index of that name.
    op.execute(
        f"ALTER TABLE notifications DROP CONSTRAINT IF EXISTS {_COMPOSITE_INDEX}"
    )
    op.execute(f"DROP INDEX IF EXISTS {_COMPOSITE_INDEX}")
    op.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS {_LEGACY_GLOBAL_INDEX} "
        "ON notifications (fingerprint)"
    )
