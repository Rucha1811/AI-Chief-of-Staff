import secrets
from datetime import datetime, timedelta
from typing import Optional

from app.core.database import SessionLocal
from app.core.config import settings
from app.core.security import hash_password, verify_password
from app.models.emergency_code import EmergencyCode


def generate_emergency_code(user_id: int, ttl_hours: Optional[int] = None, db=None) -> str:
    """Create a single-use emergency login code bound to one user.

    Returns the plaintext code. The code can ONLY authenticate ``user_id``
    (login must present both the account's email and the code)."""
    ttl_hours = ttl_hours if ttl_hours is not None else settings.EMERGENCY_CODE_TTL_HOURS
    owns = db is None
    db = db or SessionLocal()
    code = secrets.token_urlsafe(16)
    try:
        _purge_expired(db)
        db.add(
            EmergencyCode(
                user_id=user_id,
                code_hash=hash_password(code),
                expires_at=datetime.utcnow() + timedelta(hours=ttl_hours),
            )
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        if owns:
            db.close()
    return code


def consume_emergency_code(code: str, user_id: Optional[int], db=None) -> bool:
    """Consume a single-use emergency login code for ``user_id``.

    True only if the code exists, belongs to the given user, is unused, and is
    not expired. Consumption is atomic (single-use): the row is marked used via
    an UPDATE gated on ``used = false`` and ``expires_at`` still in the future,
    so two concurrent attempts cannot both succeed.

    A code generated for User A can never authenticate User B here."""
    if not code or user_id is None:
        return False
    owns = db is None
    db = db or SessionLocal()
    try:
        now = datetime.utcnow()
        rows = (
            db.query(EmergencyCode)
            .filter(
                EmergencyCode.user_id == user_id,
                EmergencyCode.used == False,  # noqa: E712
            )
            .order_by(EmergencyCode.id.asc())
            .all()
        )
        for row in rows:
            if row.expires_at and row.expires_at < now:
                continue
            if verify_password(code, row.code_hash):
                updated = (
                    db.query(EmergencyCode)
                    .filter(
                        EmergencyCode.id == row.id,
                        EmergencyCode.used == False,  # noqa: E712
                        EmergencyCode.expires_at > now,
                    )
                    .update(
                        {"used": True, "consumed_at": now},
                        synchronize_session=False,
                    )
                )
                if updated:
                    db.commit()
                    return True
                db.rollback()
                return False
        return False
    except Exception:
        db.rollback()
        return False
    finally:
        if owns:
            db.close()


def _purge_expired(db) -> None:
    db.query(EmergencyCode).filter(
        EmergencyCode.expires_at < datetime.utcnow()
    ).delete(synchronize_session=False)