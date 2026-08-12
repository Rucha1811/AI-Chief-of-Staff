import secrets
from datetime import datetime, timedelta
from typing import Optional

from app.core.database import SessionLocal
from app.core.config import settings
from app.core.security import hash_password, verify_password
from app.models.emergency_code import EmergencyCode


def generate_emergency_code(ttl_hours: Optional[int] = None, db=None) -> str:
    """Create a single-use emergency login code and return the plaintext."""
    ttl_hours = ttl_hours if ttl_hours is not None else settings.EMERGENCY_CODE_TTL_HOURS
    owns = db is None
    db = db or SessionLocal()
    code = secrets.token_urlsafe(16)
    try:
        _purge_expired(db)
        db.add(
            EmergencyCode(
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


def consume_emergency_code(code: str, db=None) -> bool:
    """Consume a single-use emergency login code. Returns True on success."""
    if not code:
        return False
    owns = db is None
    db = db or SessionLocal()
    try:
        now = datetime.utcnow()
        rows = db.query(EmergencyCode).filter(EmergencyCode.used == False).all()
        for row in rows:
            if row.expires_at and row.expires_at < now:
                continue
            if verify_password(code, row.code_hash):
                row.used = True
                row.consumed_at = now
                db.commit()
                return True
    except Exception:
        db.rollback()
        return False
    finally:
        if owns:
            db.close()
    return False


def _purge_expired(db) -> None:
    db.query(EmergencyCode).filter(
        EmergencyCode.expires_at < datetime.utcnow()
    ).delete(synchronize_session=False)
