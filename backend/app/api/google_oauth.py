import logging
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

from app.core.config import settings
from app.core.database import get_db
from app.models.user import User
from app.api.deps import get_current_user
from app.services import google_auth

router = APIRouter(prefix="/api/integrations/google", tags=["google-oauth"])

# Loopback hosts permitted for a caller-supplied ``redirect_uri``. Matching is
# done on the parsed hostname, never on a string prefix: a prefix check accepts
# ``http://localhost.evil.com`` and ``http://localhost:3000@evil.com``, which are
# not loopback at all.
_LOCAL_REDIRECT_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _is_local_redirect(uri: str) -> bool:
    """True only when ``uri`` is an http(s) URL on a loopback host.

    Rejects embedded credentials, non-loopback hosts and any scheme other than
    http/https, so the dev redirect override cannot be pointed off-machine.
    """
    try:
        parts = urlsplit(uri)
    except ValueError:
        return False
    if parts.scheme not in ("http", "https"):
        return False
    if parts.username is not None or parts.password is not None:
        return False
    return parts.hostname in _LOCAL_REDIRECT_HOSTS



@router.get("/authorize")
async def google_authorize(
    redirect_uri: str = Query(default=None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Build a Google OAuth consent URL bound to the current user.

    The returned `state` nonce is stored server-side; the callback only
    completes for the user that initiated it."""
    try:
        if redirect_uri and not _is_local_redirect(redirect_uri):
            raise HTTPException(status_code=400, detail="Redirect URI must target localhost during dev")
        return google_auth.build_authorization_url(db, user_id=user.id, redirect_uri=redirect_uri)
    except google_auth.GoogleAuthError as e:
        logger.warning("google authorize failed for user %s: %s", user.id, e)
        raise HTTPException(
            status_code=503,
            detail="Google OAuth is not configured or is temporarily unavailable.",
        )


@router.get("/callback")
async def google_callback(
    request: Request,
    code: str = Query(default=None),
    state: str = Query(default=None),
    error: str = Query(default=None),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Browser redirect target. Resolves which user owns `state`, exchanges
    the code for tokens and sends the browser back to the frontend."""
    from urllib.parse import quote

    if state and state.startswith("login_"):
        from app.api.auth import handle_google_login_callback
        return await handle_google_login_callback(request, db, code, state)

    frontend = settings.FRONTEND_URL
    if error:
        return RedirectResponse(f"{frontend}/settings?google=error&reason={quote(error)}")
    if not code or not state:
        return RedirectResponse(f"{frontend}/settings?google=error&reason=missing_params")
    user_id = _resolve_user_by_state(db, state)
    if user_id is None:
        return RedirectResponse(
            f"{frontend}/settings?google=error&reason=unknown_state"
        )
    try:
        google_auth.exchange_authorization_code(db, user_id=user_id, code=code, state=state)
    except google_auth.GoogleAuthError as e:
        # Log full error detail for diagnosis
        logger.warning("google code exchange failed for user %s: %s", user_id, e)
        import traceback
        logger.warning("google exchange traceback: %s", traceback.format_exc())
        # Temporarily expose error reason in redirect for debugging
        from urllib.parse import quote as _quote
        return RedirectResponse(f"{frontend}/settings?google=error&reason=exchange_failed&detail={_quote(str(e)[:200])}")
    return RedirectResponse(f"{frontend}/settings?google=success")


def _resolve_user_by_state(db: Session, state: str):
    from app.models.user_integration import UserIntegration

    for row in (
        db.query(UserIntegration)
        .filter(
            UserIntegration.provider == "google",
            UserIntegration.status.in_(["active", "pending"]),
        )
        .order_by(UserIntegration.id.desc())
        .all()
    ):
        if (row.meta or {}).get("oauth_state") == state:
            return row.user_id
    return None


@router.post("/disconnect")
async def google_disconnect(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Revoke the current user's Google connection (tokens kept but inactive)."""
    removed = google_auth.disconnect_user_google(db, user_id=user.id)
    return {"connected": not removed, "revoked": removed}