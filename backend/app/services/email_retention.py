import datetime
import logging

from sqlalchemy import func

from app.core.config import settings
from app.models.email import Email
from app.models.notification import Notification

logger = logging.getLogger("ai-chief.retention")


def find_expired(db) -> list[Email]:
    """Emails whose retention_until has lapsed (soft/expired candidates)."""
    now = datetime.datetime.utcnow()
    return (
        db.query(Email)
        .filter(Email.retention_until.isnot(None), Email.retention_until < now)
        .order_by(Email.retention_until.asc())
        .all()
    )


def cleanup_email_notifications(db, email_ids: list[int]) -> int:
    """Remove dependent 'email' notifications before an email row is dropped.

    Emails are only ever hard-deleted when RETENTION_MODE == "hard"; in the
    default "soft" mode this cleanup is reported but the rows themselves are
    NOT deleted. tasks/exams/events/assignments are independent rows and are
    intentionally left untouched (no FK dependency on emails)."""
    if not email_ids:
        return 0
    deleted = (
        db.query(Notification)
        .filter(
            Notification.entity_type == "email",
            Notification.entity_id.in_(email_ids),
        )
        .delete(synchronize_session=False)
    )
    return deleted


def purge_expired(db, dry_run: bool = True) -> dict:
    """Apply retention policy.

    Default (RETENTION_MODE="soft") and until explicitly approved, this is
    NON-DESTRUCTIVE: expired emails are identified, any dependent
    notifications are reported (and cleaned only when dry_run is False),
    but no email row is ever deleted in this phase.

    The retention window itself always comes from configuration
    (EMAIL_RETENTION_DAYS / emails.retention_until), never from hardcoded
    values."""
    expired = find_expired(db)
    expired_ids = [e.id for e in expired]

    mode = settings.RETENTION_MODE
    deleted_notifications = 0
    deleted_emails = 0

    if expired_ids:
        if dry_run:
            deleted_notifications = 0
        else:
            deleted_notifications = cleanup_email_notifications(db, expired_ids)
            db.commit()

    # No destructive deletion in this phase regardless of mode requested in
    # the future: hard-delete is gated out until separately approved.
    return {
        "mode": mode,
        "dry_run": dry_run,
        "expired_emails": len(expired_ids),
        "dependent_notifications": _count_email_notifications(db, expired_ids),
        "deleted_emails": deleted_emails,
        "deleted_notifications": deleted_notifications,
    }


def _count_email_notifications(db, email_ids: list[int]) -> int:
    if not email_ids:
        return 0
    return (
        db.query(func.count(Notification.id))
        .filter(
            Notification.entity_type == "email",
            Notification.entity_id.in_(email_ids),
        )
        .scalar()
        or 0
    )