import logging
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError

from app.core.database import SessionLocal
from app.models.google_auth_state import GoogleAuthState
from app.services.ownership import owner_user_id

logger = logging.getLogger(__name__)


def _naive_utc(dt):
    if dt is None:
        return None
    if getattr(dt, "tzinfo", None) is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _default_state() -> dict:
    return {
        "connected": False,
        "state": "unknown",
        "reason": None,
        "last_error": None,
        "expires_at": None,
        "last_checked_at": None,
        "refresh_attempts": 0,
    }


class AuthStateStore:
    """Persists Google authorization health across restarts so the app can
    surface a clear "re-authorize" state instead of failing silently.

    One row per user (see migration 0005). A missing user_id falls back to
    the owner (legacy single-user behavior)."""

    @staticmethod
    def _row(db, user_id=None):
        user_id = user_id or owner_user_id(db)
        row = (
            db.query(GoogleAuthState)
            .filter(GoogleAuthState.user_id == user_id)
            .order_by(GoogleAuthState.id.asc())
            .first()
        )
        if row is not None:
            return row
        try:
            row = GoogleAuthState(state="unknown", user_id=user_id)
            db.add(row)
            db.flush()
            return row
        except IntegrityError:
            db.rollback()
            return (
                db.query(GoogleAuthState)
                .filter(GoogleAuthState.user_id == user_id)
                .order_by(GoogleAuthState.id.asc())
                .first()
            )

    @staticmethod
    def get_state(user_id=None) -> dict:
        try:
            db = SessionLocal()
        except Exception:
            return _default_state()
        try:
            if user_id is None:
                user_id = owner_user_id(db)
            row = (
                db.query(GoogleAuthState)
                .filter(GoogleAuthState.user_id == user_id)
                .order_by(GoogleAuthState.id.asc())
                .first()
            )
        except Exception:
            return _default_state()
        finally:
            db.close()
        if row is None:
            return _default_state()
        return {
            "connected": row.state == "healthy",
            "state": row.state or "unknown",
            "reason": row.reason,
            "last_error": row.last_error,
            "expires_at": row.expires_at.isoformat() if row.expires_at else None,
            "last_checked_at": (
                row.last_checked_at.isoformat() if row.last_checked_at else None
            ),
            "refresh_attempts": row.refresh_attempts or 0,
        }

    @staticmethod
    def set_state(state: str, reason: str = None, last_error: str = None,
                  expires_at=None, email: str = None, user_id=None) -> None:
        try:
            db = SessionLocal()
        except Exception:
            logger.warning("Could not open DB session to record Google auth state")
            return
        try:
            row = AuthStateStore._row(db, user_id=user_id)
            row.state = state
            row.reason = reason
            row.last_error = last_error
            row.expires_at = _naive_utc(expires_at)
            row.last_checked_at = datetime.utcnow()
            if state == "healthy":
                row.refresh_attempts = 0
            if email is not None:
                row.email = email
            db.commit()
        except Exception as e:
            db.rollback()
            logger.warning("Could not record Google auth state: %s", e)
        finally:
            db.close()

    @staticmethod
    def renew_expiry(expires_at=None, email: str = None, user_id=None) -> None:
        AuthStateStore.set_state(
            "healthy", reason="ok", expires_at=expires_at, email=email, user_id=user_id
        )

    @staticmethod
    def mark_dead(reason: str = "expired", last_error: str = None, user_id=None) -> None:
        try:
            db = SessionLocal()
        except Exception:
            return
        try:
            row = AuthStateStore._row(db, user_id=user_id)
            row.state = "unhealthy"
            row.reason = reason
            row.last_error = last_error
            row.last_checked_at = datetime.utcnow()
            row.refresh_attempts = (row.refresh_attempts or 0) + 1
            db.commit()
        except Exception as e:
            db.rollback()
            logger.warning("Could not mark Google auth as dead: %s", e)
        finally:
            db.close()
