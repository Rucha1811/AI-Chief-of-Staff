from sqlalchemy.orm import Session

from app.models.user import User


def owner_user_id(db: Session) -> int | None:
    """Id of the bootstrap/owner user.

    Phase A bridge: background sync and legacy single-user flows stamp rows
    with this id so existing features keep working. Phase B will replace
    these call sites with an explicit per-user context.
    """
    user = db.query(User).order_by(User.id.asc()).first()
    if user is None:
        # Return None if no user exists yet; callers should handle this
        # gracefully (e.g., skip user-specific operations until a user
        # is registered). This avoids crashing the background worker during
        # initial startup in development mode.
        return None
    return user.id