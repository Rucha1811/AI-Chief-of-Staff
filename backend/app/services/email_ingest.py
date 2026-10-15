import datetime
import logging
from typing import Optional

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.models.email import Email
from app.models.user import User
from app.models.user_integration import UserIntegration
from app.services.ownership import owner_user_id

logger = logging.getLogger("ai-chief.ingest")


class EmailIngestIdentityError(RuntimeError):
    """Raised when the claimed ingest identity is unknown or unauthorized."""

    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        super().__init__(message)


def resolve_ingest_user(db, user_id: Optional[int] = None,
                        account_id: Optional[str] = None) -> int:
    """Resolve and validate the user that an ingest batch belongs to.

    FastAPI is authoritative for identity; a raw ``user_id`` is never trusted
    on its own. In a multi-user n8n deployment the Gmail ``account_id`` is the
    trusted identity anchor: it was bound to a user by their own OAuth flow in
    ``user_integrations`` (a server-side mapping n8n cannot forge).

    Resolution precedence:
    - Neither claimed -> legacy single-user fallback to the owner (the
      original n8n contract, still gated by the ingest API key).
    - ``account_id`` only -> resolve to the user whose ACTIVE Google
      integration owns that account. Unknown -> 404; the same account on more
      than one active integration is ambiguous -> 409.
    - ``user_id`` (+ optional matching ``account_id``) -> existing per-user
      validation: active user AND active Google integration (403 otherwise),
      and a claimed account must belong to that integration (403 otherwise).
    """
    if user_id is not None:
        user = db.query(User).filter(User.id == user_id, User.status == "active").first()
        if user is None:
            raise EmailIngestIdentityError(404, "Unknown ingest user")

        row = (
            db.query(UserIntegration)
            .filter(
                UserIntegration.user_id == user_id,
                UserIntegration.provider == "google",
            )
            .first()
        )
        if row is None or row.status != "active":
            raise EmailIngestIdentityError(
                403, f"User #{user_id} has no active Google integration for ingest"
            )

        if account_id:
            expected = (row.account_id or "").strip().lower()
            if not expected or expected != str(account_id).strip().lower():
                raise EmailIngestIdentityError(
                    403, "Ingest account does not belong to the requested user"
                )
        return user.id

    if account_id:
        account = str(account_id).strip().lower()
        rows = (
            db.query(UserIntegration)
            .filter(
                UserIntegration.provider == "google",
                UserIntegration.status == "active",
                func.lower(func.trim(UserIntegration.account_id)) == account,
            )
            .all()
        )
        if not rows:
            raise EmailIngestIdentityError(404, "Unknown ingest account")
        if len(rows) > 1:
            raise EmailIngestIdentityError(
                409, "Ingest account is ambiguous; it resolves to multiple users"
            )
        owner_id = rows[0].user_id
        user = db.query(User).filter(User.id == owner_id, User.status == "active").first()
        if user is None:
            raise EmailIngestIdentityError(404, "Unknown ingest user")
        return user.id

    return owner_user_id(db)


def _build_rows(items) -> list[dict]:
    """Map validated ingest items onto Email columns.

    body_html is deliberately NEVER persisted (privacy: raw HTML dropped
    in the n8n layer). Only the fields the existing application uses are
    stored; retention_until and sensitivity are stamped from config."""
    now = datetime.datetime.utcnow()
    retention_until = now + datetime.timedelta(days=settings.EMAIL_RETENTION_DAYS)
    rows = []
    for it in items:
        rows.append(
            {
                "message_id": it.message_id,
                "thread_id": it.thread_id,
                "from_email": it.from_email,
                "from_name": it.from_name,
                "to_email": it.to_email,
                "subject": it.subject or "",
                "body_text": it.body_text,
                "received_at": it.received_at,
                "is_read": bool(it.is_read),
                "category": "unknown",
                "retention_until": retention_until,
                "sensitivity": settings.EMAIL_SENSITIVITY,
            }
        )
    return rows


def ingest_emails(db, items, user_id: Optional[int] = None,
                  account_id: Optional[str] = None) -> dict:
    """Idempotent batch insert keyed on (user_id, message_id).

    Gmail message ids are unique per mailbox (per user), so the dedup key is
    the per-user compound. Duplicates for the SAME user are ignored, never
    duplicated; an identical provider id belonging to ANOTHER user is a fresh
    row for that user, never suppressed or overwritten.

    Ownership is resolved/validated per user (see resolve_ingest_user) and
    every row in the batch carries the same resolved owner.

    Returns {inserted_ids, results:[{message_id,status,email_id}], ...}."""
    target_user_id = resolve_ingest_user(db, user_id, account_id)
    rows = _build_rows(items)
    for row in rows:
        row["user_id"] = target_user_id
    inserted_ids: list[int] = []
    inserted_by_mid: dict[str, int] = {}
    try:
        if db.bind.dialect.name == "postgresql":
            stmt = (
                pg_insert(Email.__table__)
                .values(rows)
                .on_conflict_do_nothing(
                    index_elements=["user_id", "message_id"]
                )
                .returning(Email.__table__.c.id, Email.__table__.c.message_id)
            )
            for eid, mid in db.execute(stmt).fetchall():
                inserted_ids.append(eid)
                inserted_by_mid[mid] = eid
        else:
            for row in rows:
                email = Email(**row)
                db.add(email)
                try:
                    db.flush()
                except IntegrityError:
                    db.rollback()
                    continue
                inserted_ids.append(email.id)
                inserted_by_mid[email.message_id] = email.id
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("ingest batch failed")
        raise

    results = []
    for it in items:
        eid = inserted_by_mid.get(it.message_id)
        results.append(
            {
                "message_id": it.message_id,
                "status": "inserted" if eid is not None else "ignored",
                "email_id": eid,
            }
        )

    return {
        "inserted_ids": inserted_ids,
        "results": results,
        "received": len(items),
        "inserted": len(inserted_ids),
        "ignored": len(items) - len(inserted_ids),
    }