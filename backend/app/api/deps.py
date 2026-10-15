from typing import Optional, Set

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.security import verify_token
from app.models.user import Permission, Role, User


def bearer_token(request: Request) -> Optional[str]:
    header = request.headers.get("Authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return None


def token_payload(request: Request) -> Optional[dict]:
    token = bearer_token(request)
    if not token:
        return None
    return verify_token(token, settings.SECRET_KEY)


def token_is_current(user: User, payload: dict) -> bool:
    """True when a verified token was minted at the user's current session
    epoch.

    Tokens carry a ``ver`` claim (users.token_version at mint time). Missing
    values (legacy tokens, or rows written before migration 0011) are treated as
    0, so nothing is invalidated by deploying this control; an explicit logout
    increments the user's version and retires every older token at once.
    """
    return int(payload.get("ver", 0) or 0) == int(
        getattr(user, "token_version", 0) or 0
    )


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """Resolve the authenticated user from the bearer token (or the legacy
    bootstrap owner when the token carries no subject)."""
    payload = token_payload(request)
    if not payload:
        raise HTTPException(status_code=401, detail="Authentication required")

    user_id = payload.get("sub")
    if user_id is None:
        # Legacy token minted before subject was added: resolve to the
        # earliest (bootstrap/owner) user.
        user = db.query(User).order_by(User.id.asc()).first()
    else:
        user = db.query(User).filter(User.id == user_id).first()

    if user is None:
        raise HTTPException(status_code=401, detail="Invalid token")
    if not token_is_current(user, payload):
        # Revoked by a logout / "sign out everywhere" after this token was
        # minted. Same 401 as an invalid token: the caller only needs to know
        # to re-authenticate.
        raise HTTPException(status_code=401, detail="Session expired or revoked")
    if user.status != "active":
        raise HTTPException(status_code=403, detail="Account is disabled")
    return user


def user_permission_codes(user: User) -> Set[str]:
    codes: Set[str] = set()
    for role in user.roles:
        for perm in role.permissions:
            codes.add(perm.code)
    return codes


def require_permission(permission_code: str):
    """Dependency factory asserting the current user holds a permission."""

    def _checker(user: User = Depends(get_current_user)) -> User:
        codes = user_permission_codes(user)
        if permission_code not in codes:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return user

    return _checker