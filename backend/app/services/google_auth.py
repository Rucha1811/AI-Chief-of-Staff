import json
import os
import secrets
import threading
from contextlib import contextmanager
from datetime import datetime
from typing import Optional

from google.oauth2.credentials import Credentials
from google.auth.exceptions import RefreshError
from googleapiclient.discovery import build
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import decrypt_bytes, encrypt_bytes
from app.models.user_integration import UserIntegration
from app.services.auth_state import AuthStateStore
from app.services.ownership import owner_user_id

_HTTP_TIMEOUT = 15
_refresh_lock = threading.Lock()

SCOPE_CALENDAR = "https://www.googleapis.com/auth/calendar"
SCOPE_CALENDAR_EVENTS = "https://www.googleapis.com/auth/calendar.events"
SCOPE_CALENDAR_READONLY = "https://www.googleapis.com/auth/calendar.readonly"

# Superset → covered scopes. A token granted a superset scope satisfies
# requests for the narrower scopes (e.g. old "calendar" tokens keep working
# after we switched to requesting only "calendar.events").
_SCOPE_SUPERSETS = {
    SCOPE_CALENDAR: {SCOPE_CALENDAR_EVENTS, SCOPE_CALENDAR_READONLY},
}

# Scopes requested by the integration "Connect Google" flow (least privilege):
#  - Gmail: read only (summarize, detect deadlines, schedule)
#  - Classroom: read only
#  - Calendar: read + create events only (no settings/delete)
#  - openid/userinfo: identity so each user is bound to a unique Google account
#
# ``classroom.courseWork.list`` (assignment titles/due dates) requires the
# coursework scope; the student-submissions scope alone is insufficient.
# Reconnecting Google is required for existing tokens to receive this scope.
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/classroom.courses.readonly",
    "https://www.googleapis.com/auth/classroom.coursework.me.readonly",
    "https://www.googleapis.com/auth/classroom.student-submissions.me.readonly",
    SCOPE_CALENDAR_EVENTS,
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/contacts.readonly",
]

# Identity-only scopes for the Google *login* flow. Login only authenticates
# the user (who they are); it does not need Gmail/Classroom/Calendar access.
# Requesting the full SCOPES here would make sign-in fail whenever Google
# declines any integration scope (e.g. a Workspace that has not allowlisted
# ``classroom.coursework.me.readonly``), because oauthlib raises on the
# resulting scope reduction. The integration scopes are requested separately
# by the Settings "Connect Google" flow.
IDENTITY_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]

CREDENTIALS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "credentials")
CREDENTIALS_FILE = os.path.join(CREDENTIALS_DIR, "credentials.json")


class GoogleAuthError(RuntimeError):
    pass


@contextmanager
def _accept_google_granted_scopes():
    """Allow Google to omit optional Workspace scopes during token exchange.

    Google may approve only a subset of the requested integration scopes
    (especially Classroom coursework, Drive, or Contacts). The returned token
    scopes are persisted and each provider checks its own required scope.
    """
    key = "OAUTHLIB_RELAX_TOKEN_SCOPE"
    previous = os.environ.get(key)
    os.environ[key] = "1"
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = previous


def has_scope(creds: Credentials, scope: str) -> bool:
    scopes = set(getattr(creds, "scopes", None) or [])
    if scope in scopes:
        return True
    return any(scope in _SCOPE_SUPERSETS.get(s, ()) for s in scopes)


def build_service(service_name: str, version: str, creds: Credentials):
    import httplib2

    from google_auth_httplib2 import AuthorizedHttp

    http = AuthorizedHttp(
        credentials=creds,
        http=httplib2.Http(timeout=_HTTP_TIMEOUT),
    )
    return build(
        service_name,
        version,
        http=http,
        cache_discovery=False,
    )


async def probe_credentials_for_user(db: Session = None, user_id: int = None) -> dict:
    """Per-user version of probe_credentials (never raises)."""
    import asyncio

    owns_db = db is None
    if owns_db:
        db = SessionLocal()
    try:
        user_id = user_id or owner_user_id(db)
        creds = await asyncio.to_thread(credentials_for_user, db, user_id)
        if not creds:
            AuthStateStore.set_state("not_authorized", reason="not_authorized", user_id=user_id)
            return {
                "connected": False,
                "state": "not_authorized",
                "reason": "not_authorized",
                "last_error": (
                    "No Google authorization found. Connect Google from the Settings page."
                ),
                "expires_at": None,
                "last_checked_at": None,
                "refresh_attempts": 0,
                "has_refresh_token": False,
                "scopes": [],
            }
        try:
            await ensure_refreshed_for_user(db, user_id, creds)
            AuthStateStore.renew_expiry(expires_at=creds.expiry, user_id=user_id)
        except GoogleAuthError:
            pass
        info = AuthStateStore.get_state(user_id=user_id)
        info["has_refresh_token"] = bool(creds.refresh_token)
        info["scopes"] = list(getattr(creds, "scopes", None) or [])
        return info
    finally:
        if owns_db:
            db.close()


# ---------------------------------------------------------------------------
# Per-user Google OAuth (web flow)
#
# Each user has at most one "google" row in user_integrations holding their
# encrypted refresh/access tokens. No global token.pickle is read at runtime;
# the standalone script scripts/migrate_legacy_google_token.py performs a
# one-time import of a pre-existing token.pickle (if any) into the owner's row.
# ---------------------------------------------------------------------------

_OIDC_USERINFO_URI = "https://openidconnect.googleapis.com/v1/userinfo"
_GOOGLE_AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"


def _client_config() -> dict:
    """OAuth web client config: env overrides win, else credentials.json."""
    client_id = settings.GOOGLE_CLIENT_ID
    client_secret = settings.GOOGLE_CLIENT_SECRET
    auth_uri = _GOOGLE_AUTH_URI
    token_uri = _GOOGLE_TOKEN_URI
    if not client_id or not client_secret:
        if not os.path.exists(CREDENTIALS_FILE):
            raise GoogleAuthError(
                "Google OAuth is not configured. Set GOOGLE_CLIENT_ID/"
                "GOOGLE_CLIENT_SECRET or place credentials.json in backend/credentials/"
            )
        try:
            with open(CREDENTIALS_FILE) as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            raise GoogleAuthError(f"Could not read Google credentials.json: {e}") from e
        section = data.get("web") or data.get("installed") or {}
        client_id = section.get("client_id")
        client_secret = section.get("client_secret")
        auth_uri = section.get("auth_uri", auth_uri)
        token_uri = section.get("token_uri", token_uri)
    if not client_id or not client_secret:
        raise GoogleAuthError("Google OAuth is not configured (missing client id/secret).")
    return {
        "client_id": client_id,
        "client_secret": client_secret,
        "auth_uri": auth_uri,
        "token_uri": token_uri,
    }


def _naive_utc(dt):
    if dt is None:
        return None
    if getattr(dt, "tzinfo", None) is not None:
        return dt.astimezone(__import__("datetime").timezone.utc).replace(tzinfo=None)
    return dt


def integration_row(db: Session, provider: str = "google", user_id: int = None) -> Optional[UserIntegration]:
    user_id = user_id or owner_user_id(db)
    return (
        db.query(UserIntegration)
        .filter(UserIntegration.user_id == user_id, UserIntegration.provider == provider)
        .first()
    )


def _creds_from_row(row: UserIntegration) -> Optional[Credentials]:
    if row is None or not row.refresh_token_encrypted:
        return None
    try:
        cfg = _client_config()
    except GoogleAuthError:
        return None
    try:
        creds = Credentials(
            token=(
                decrypt_bytes(row.access_token_encrypted, settings.SECRET_KEY).decode()
                if row.access_token_encrypted
                else None
            ),
            refresh_token=decrypt_bytes(row.refresh_token_encrypted, settings.SECRET_KEY).decode(),
            token_uri=cfg["token_uri"],
            client_id=cfg["client_id"],
            client_secret=cfg["client_secret"],
            scopes=(row.scopes or "").split() or None,
        )
    except Exception:
        return None
    if row.token_expires_at:
        creds.expiry = row.token_expires_at
    else:
        # NF-3: a missing expiry used to be read by google.auth as "valid
        # forever" (``Credentials.valid`` is True when ``expiry is None``), so
        # health reported "healthy" for a dead credential without ever calling
        # Google. Force the refresh path instead: with an epoch expiry the
        # credential is treated as expired, the refresh token is exercised, and
        # the true state is observed and persisted (or surfaced as an error).
        creds.expiry = datetime(1970, 1, 1)
    return creds


def credentials_for_user(db: Session, user_id: int = None) -> Optional[Credentials]:
    """Load a user's Google credentials from user_integrations only."""
    user_id = user_id or owner_user_id(db)
    row = integration_row(db, user_id=user_id)
    if row is not None and row.status == "active" and row.refresh_token_encrypted:
        return _creds_from_row(row)
    return None


def save_credentials_for_user(
    db: Session, user_id: int, creds: Credentials,
    account_id: str = None, metadata: dict = None,
) -> UserIntegration:
    user_id = user_id or owner_user_id(db)
    row = integration_row(db, user_id=user_id)
    if row is None:
        row = UserIntegration(user_id=user_id, provider="google", status="active")
        db.add(row)
    if account_id:
        row.account_id = account_id
    if getattr(creds, "refresh_token", None):
        row.refresh_token_encrypted = encrypt_bytes(
            creds.refresh_token.encode(), settings.SECRET_KEY
        ).decode()
    if getattr(creds, "token", None):
        row.access_token_encrypted = encrypt_bytes(
            creds.token.encode(), settings.SECRET_KEY
        ).decode()
    if getattr(creds, "expiry", None):
        row.token_expires_at = _naive_utc(creds.expiry)
    if getattr(creds, "scopes", None):
        row.scopes = " ".join(creds.scopes)
    row.status = "active"
    row.last_error = None
    if metadata:
        current = dict(row.meta or {})
        current.update(metadata)
        row.meta = current
    db.commit()
    return row


def _refresh_creds_only(creds: Credentials) -> None:
    from google.auth.transport.requests import Request

    with _refresh_lock:
        if not creds.valid:
            creds.refresh(Request())


async def ensure_refreshed_for_user(db: Session, user_id: int = None, creds: Credentials = None) -> None:
    import asyncio

    user_id = user_id or owner_user_id(db)
    if creds is None:
        creds = await asyncio.to_thread(credentials_for_user, db, user_id)
    if creds is None:
        AuthStateStore.mark_dead(
            reason="not_authorized",
            last_error="No Google authorization found. Connect Google in Settings.",
            user_id=user_id,
        )
        raise GoogleAuthError(
            "No Google authorization found for this account. "
            "Connect Google from the Settings page."
        )
    if creds.valid:
        return
    if not (creds.expired and creds.refresh_token):
        AuthStateStore.mark_dead(
            reason="invalid",
            last_error="Google authorization is invalid (missing refresh token).",
            user_id=user_id,
        )
        raise GoogleAuthError(
            "Google authorization is invalid. Re-connect Google from the Settings page."
        )
    try:
        await asyncio.wait_for(asyncio.to_thread(_refresh_creds_only, creds), timeout=30.0)
        AuthStateStore.renew_expiry(expires_at=creds.expiry, user_id=user_id)
        row = integration_row(db, user_id=user_id)
        if row is not None:
            row.access_token_encrypted = encrypt_bytes(
                creds.token.encode(), settings.SECRET_KEY
            ).decode()
            row.token_expires_at = _naive_utc(creds.expiry)
            row.last_error = None
            db.commit()
    except (asyncio.TimeoutError, RefreshError) as e:
        AuthStateStore.mark_dead(reason="expired", last_error=str(e), user_id=user_id)
        raise GoogleAuthError(
            "Google authorization has expired and can't be refreshed. "
            "Re-connect Google from the Settings page."
        ) from e
    except Exception as e:
        raise GoogleAuthError(f"Could not refresh Google authorization: {e}") from e


def _oauth_flow(state: str = None, redirect_uri: str = None, scopes: list = None):
    from google_auth_oauthlib.flow import Flow

    return Flow.from_client_config(
        {"web": _client_config()},
        scopes=scopes or SCOPES,
        state=state,
        redirect_uri=redirect_uri or settings.OAUTH_REDIRECT_URI,
    )


def _store_pending_state(db: Session, user_id: int, state: str) -> None:
    row = integration_row(db, user_id=user_id)
    if row is None:
        integration_id = None
        if db.bind is not None and db.bind.dialect.name == "sqlite":
            integration_id = (db.query(func.max(UserIntegration.id)).scalar() or 0) + 1
        row = UserIntegration(
            id=integration_id,
            user_id=user_id,
            provider="google",
            status="pending",
        )
        db.add(row)
    elif row.status != "active":
        # Re-authorizing must also work from a disconnected/revoked (or any
        # other non-active) row. The callback resolves the owning user by
        # scanning rows whose status is active/pending (see
        # app.api.google_oauth._resolve_user_by_state); leaving a revoked row
        # revoked made the callback fail with reason=unknown_state so the
        # account could never be reconnected. An already-active connection is
        # left untouched so a re-auth never disables a working one.
        row.status = "pending"
    metadata = dict(row.meta or {})
    metadata["oauth_state"] = state
    row.meta = metadata
    db.commit()


def build_authorization_url(db: Session, user_id: int = None, redirect_uri: str = None) -> dict:
    """Create a Google authorization URL bound to a specific user.

    The state nonce is stored server-side in the user's pending integration
    row so the callback can resolve which user completed the flow."""
    user_id = user_id or owner_user_id(db)
    state = secrets.token_urlsafe(32)
    redirect_uri = redirect_uri or settings.OAUTH_REDIRECT_URI
    flow = _oauth_flow(state=state, redirect_uri=redirect_uri)
    # ``include_granted_scopes`` is intentionally NOT set: it asks Google to
    # re-grant every scope the user previously consented to. If this account
    # ever authorised a scope that is no longer requested (e.g. the coursework
    # scope that Google does not grant for this project), Google would return it
    # in the granted set, so granted != requested and oauthlib's scope-change
    # guard rejects the exchange (exchange_failed). Omitting it makes Google
    # grant exactly the requested scopes, so the granted set always matches.
    url, _ = flow.authorization_url(access_type="offline", prompt="consent")
    _store_pending_state(db, user_id, state)
    return {"url": url, "state": state, "redirect_uri": redirect_uri}


def _account_identity(creds: Credentials, id_token: str = None) -> dict:
    """Best-effort identity (email/name/sub) for a fresh web-OAuth token."""
    if id_token:
        try:
            from google.auth.transport import requests as greq
            from google.oauth2 import id_token as idt

            info = idt.verify_oauth2_token(id_token, greq.Request(), creds.client_id)
            return {
                "email": info.get("email"),
                "name": info.get("name"),
                "google_sub": info.get("sub"),
                # Google's own assertion that it controls and has verified the
                # address. This - not the address string - is what proves
                # institutional identity.
                "email_verified": bool(info.get("email_verified")),
            }
        except Exception:
            pass
    try:
        import requests

        resp = requests.get(
            _OIDC_USERINFO_URI,
            headers={"Authorization": f"Bearer {creds.token}"},
            timeout=15,
        )
        if resp.ok:
            info = resp.json()
            return {
                "email": info.get("email"),
                "name": info.get("name"),
                "google_sub": info.get("sub"),
                "email_verified": bool(info.get("email_verified")),
            }
    except Exception:
        pass
    return {"email": None, "name": None, "google_sub": None, "email_verified": False}


def exchange_authorization_code(
    db: Session, user_id: int = None, code: str = None,
    state: str = None, redirect_uri: str = None,
) -> dict:
    """Exchange an OAuth code for tokens and persist them for the user.

    Verifies the state nonce against the server-side pending state, then
    stores the (encrypted) tokens and account identity in user_integrations."""
    user_id = user_id or owner_user_id(db)
    redirect_uri = redirect_uri or settings.OAUTH_REDIRECT_URI
    row = integration_row(db, user_id=user_id)
    stored = (row.meta or {}).get("oauth_state") if row else None
    if not state or not stored or not secrets.compare_digest(state, stored):
        raise GoogleAuthError("OAuth state mismatch or expired. Please connect again.")
    flow = _oauth_flow(state=state, redirect_uri=redirect_uri)
    try:
        with _accept_google_granted_scopes():
            flow.fetch_token(code=code)
    except Exception as e:
        raise GoogleAuthError(f"Google rejected the authorization code: {e}") from e
    creds = flow.credentials
    token_body = getattr(flow, "token_response", None) or {}
    identity = _account_identity(creds, id_token=token_body.get("id_token"))
    email = identity.get("email")
    save_credentials_for_user(
        db, user_id, creds,
        account_id=email or (row.account_id if row else None),
        metadata={
            "email": email,
            "name": identity.get("name"),
            "google_sub": identity.get("google_sub"),
            "auth_label": email or "Google account",
        },
    )
    AuthStateStore.renew_expiry(expires_at=creds.expiry, email=email, user_id=user_id)
    return {
        "email": email,
        "name": identity.get("name"),
        "google_sub": identity.get("google_sub"),
    }


def disconnect_user_google(db: Session, user_id: int = None) -> bool:
    user_id = user_id or owner_user_id(db)
    row = integration_row(db, user_id=user_id)
    if row is None:
        AuthStateStore.set_state("not_authorized", reason="disconnected", user_id=user_id)
        return False
    row.status = "revoked"
    db.commit()
    AuthStateStore.set_state("not_authorized", reason="disconnected", user_id=user_id)
    return True
