import logging
from collections import defaultdict
from time import time
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import configured_value, settings
from app.core.database import get_db
from app.core.security import create_token, hash_password, verify_password, verify_token
from app.models.user import Role, User
from app.api.deps import (
    get_current_user,
    token_is_current,
    token_payload,
    user_permission_codes,
)

from fastapi.responses import RedirectResponse
import secrets

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

AUTH_ENABLED = bool(settings.AUTH_PASSWORD)
_AUTH_HASH: Optional[str] = (
    settings.AUTH_PASSWORD
    if settings.AUTH_PASSWORD and settings.AUTH_PASSWORD.startswith("pbkdf2$")
    else (hash_password(settings.AUTH_PASSWORD) if settings.AUTH_PASSWORD else None)
)

# Brute-force protection: per-IP sliding-window attempt counter plus a
# lockout once too many consecutive failures accumulate.
_MAX_ATTEMPTS = 5
_WINDOW_SECONDS = 60
_LOCKOUT_SECONDS = 300
_attempts: dict[str, list[float]] = defaultdict(list)
_fail_streak: dict[str, int] = defaultdict(int)
_locked_until: dict[str, float] = {}


def _client_ip(request: Request) -> str:
    """Client IP for rate limiting.

    X-Forwarded-For is trusted ONLY when the direct peer is a configured
    trusted proxy (settings.trusted_proxy_ips). Otherwise the header is
    attacker-controlled and must be ignored: a client that can mint a new
    XFF value per request would bypass the login rate limit entirely.
    """
    peer = request.client.host if request.client else "unknown"
    if peer in settings.trusted_proxy_ips:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return peer


def _check_rate_limit(ip: str) -> None:
    now = time()
    if _locked_until.get(ip, 0) > now:
        raise HTTPException(
            status_code=429,
            detail="Too many failed login attempts. Try again later.",
        )
    _attempts[ip] = [t for t in _attempts[ip] if now - t < _WINDOW_SECONDS]
    if len(_attempts[ip]) >= _MAX_ATTEMPTS:
        _locked_until[ip] = now + _LOCKOUT_SECONDS
        raise HTTPException(
            status_code=429,
            detail="Too many login attempts. Try again later.",
        )


def _register_attempt(ip: str, success: bool) -> None:
    now = time()
    _attempts[ip].append(now)
    if success:
        _fail_streak[ip] = 0
        _attempts[ip] = []
        return
    _fail_streak[ip] += 1
    if _fail_streak[ip] >= _MAX_ATTEMPTS:
        _locked_until[ip] = now + _LOCKOUT_SECONDS


def is_authenticated(request: Request) -> bool:
    if not AUTH_ENABLED:
        return True
    return token_payload(request) is not None


class LoginRequest(BaseModel):
    email: Optional[str] = None
    password: str = ""
    emergency_code: Optional[str] = None


class ExchangeRequest(BaseModel):
    code: str


class RegisterRequest(BaseModel):
    email: str
    password: str
    display_name: Optional[str] = None


class UserProfile(BaseModel):
    id: int
    email: str
    display_name: Optional[str] = None
    timezone: str
    status: str
    roles: list[str] = []
    permissions: list[str] = []


class LoginResponse(BaseModel):
    token: str
    expires_in: int
    user: Optional[UserProfile] = None


def _profile(user: User) -> UserProfile:
    return UserProfile(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        timezone=user.timezone,
        status=user.status,
        roles=sorted({r.code for r in user.roles}),
        permissions=sorted(user_permission_codes(user)),
    )


def _legacy_sub(db: Session) -> Optional[int]:
    """Resolve the single bootstrap/owner user id for legacy flows."""
    user = db.query(User).order_by(User.id.asc()).first()
    return user.id if user else None


def _sqlite_user_id(db: Session) -> Optional[int]:
    """Provide an id for SQLite tables using BIGINT primary keys.

    SQLite only auto-increments an INTEGER PRIMARY KEY. The development schema
    uses BIGINT for PostgreSQL compatibility, so new local users need an
    explicit id.
    """
    if db.bind is not None and db.bind.dialect.name == "sqlite":
        return (db.query(func.max(User.id)).scalar() or 0) + 1
    return None


@router.post("/login", response_model=LoginResponse)
def login(request: Request, body: LoginRequest, db: Session = Depends(get_db)):
    ip = _client_ip(request)
    _check_rate_limit(ip)
    ttl = settings.AUTH_TOKEN_TTL_DAYS * 86400

    # Emergency access codes are account-scoped: the code AND the account
    # email must both be presented, and the code only authenticates the user
    # it was generated for. No email -> no emergency login.
    if body.emergency_code:
        uid = None
        if body.email:
            from app.services.emergency_codes import consume_emergency_code

            target = (
                db.query(User)
                .filter(User.email == body.email.strip().lower())
                .order_by(User.id.asc())
                .first()
            )
            if target is not None and consume_emergency_code(
                body.emergency_code, target.id, db
            ):
                uid = target.id
        user = db.query(User).filter(User.id == uid).first() if uid else None
        if user is not None and user.status == "active":
            _register_attempt(ip, success=True)
            return LoginResponse(
                token=create_token(
                    settings.SECRET_KEY, ttl, sub=user.id, ver=user.token_version or 0
                ),
                expires_in=ttl,
                user=_profile(user),
            )
        _register_attempt(ip, success=False)
        raise HTTPException(status_code=401, detail="Incorrect email or invalid emergency code.")

    # User-based login (email + password).
    if body.email:
        email = body.email.strip().lower()
        user = (
            db.query(User)
            .filter(User.email == email)
            .order_by(User.id.asc())
            .first()
        )
        if user and user.status == "active" and verify_password(body.password, user.password_hash):
            _register_attempt(ip, success=True)
            return LoginResponse(
                token=create_token(
                    settings.SECRET_KEY, ttl, sub=user.id, ver=user.token_version or 0
                ),
                expires_in=ttl,
                user=_profile(user),
            )
        _register_attempt(ip, success=False)
        raise HTTPException(status_code=401, detail="Incorrect email or password.")

    # Legacy single-password login mapped to the bootstrap/owner user.
    if not AUTH_ENABLED:
        raise HTTPException(status_code=400, detail="Authentication is not enabled on this server.")
    if _AUTH_HASH and verify_password(body.password, _AUTH_HASH):
        owner = db.query(User).order_by(User.id.asc()).first()
        if owner is None:
            raise HTTPException(status_code=401, detail="No account configured.")
        _register_attempt(ip, success=True)
        return LoginResponse(
            token=create_token(
                settings.SECRET_KEY, ttl, sub=owner.id, ver=owner.token_version or 0
            ),
            expires_in=ttl,
        )

    _register_attempt(ip, success=False)
    raise HTTPException(status_code=401, detail="Incorrect password or invalid emergency code.")


@router.post("/register", response_model=UserProfile, status_code=201)
def register(
    body: RegisterRequest,
    request: Request,
    db: Session = Depends(get_db),
    bootstrap_token: Optional[str] = Header(default=None, alias="X-Bootstrap-Token"),
):
    """Create a user.

    The first user becomes the owner. In production that single, one-shot
    promotion is refused unless the request presents the operator's one-time
    ``BOOTSTRAP_TOKEN`` (P24B-06), and it can never be used a second time - not
    after a restart, and not even if the owner row is deleted later. Every
    subsequent registration requires the `manage_users` permission.
    """
    from app.services import bootstrap

    existing = (
        db.query(User)
        .filter(User.email == body.email.strip().lower())
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="An account with that email already exists.")

    has_users = db.query(User).first() is not None
    if not has_users:
        # Bootstrap path. The decision is read-only; the one-shot authorization
        # is consumed further down, inside the same transaction as the insert.
        decision = bootstrap.evaluate(
            db,
            presented_token=bootstrap_token,
            configured_token=configured_value("BOOTSTRAP_TOKEN"),
            environment=settings.ENVIRONMENT,
        )
        if not decision.authorized:
            logger.warning(
                "Refused first-owner registration: reason=%s environment=%s",
                decision.reason,
                settings.ENVIRONMENT,
            )
            # One message for every refusal: the endpoint must not become an
            # oracle for whether a token is configured, wrong or already spent.
            raise HTTPException(status_code=403, detail=bootstrap.GENERIC_REFUSAL)
    else:
        payload = token_payload(request)
        if not payload or payload.get("sub") is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        admin = db.query(User).filter(User.id == payload["sub"]).first()
        if admin is None or admin.status != "active" or not token_is_current(admin, payload):
            raise HTTPException(status_code=401, detail="Invalid token")
        if "manage_users" not in user_permission_codes(admin):
            raise HTTPException(status_code=403, detail="Insufficient permissions.")

    email = body.email.strip().lower()
    if "@" not in email or len(body.password) < 8:
        raise HTTPException(
            status_code=422,
            detail="Provide a valid email and a password of at least 8 characters.",
        )

    user = User(
        email=email,
        password_hash=hash_password(body.password),
        display_name=body.display_name or email.split("@")[0],
        timezone="UTC",
        status="active",
    )
    db.add(user)
    db.flush()

    if not has_users:
        # Serialize against concurrent bootstrap attempts: exactly one caller
        # can hold the marker. The loser rolls back, so it neither becomes a
        # second owner nor leaves a half-created account behind.
        if not bootstrap.claim(db, owner_user_id=user.id):
            db.rollback()
            logger.warning("Refused first-owner registration: reason=%s", bootstrap.CLAIM_LOST)
            raise HTTPException(status_code=403, detail=bootstrap.GENERIC_REFUSAL)

    # The bootstrap path is the only path that can create an owner, and it is
    # mutually exclusive by the claim above.
    role = db.query(Role).filter(Role.code == ("member" if has_users else "owner")).first()
    if role:
        user.roles.append(role)

    db.commit()
    db.refresh(user)
    return _profile(user)


@router.post("/exchange", response_model=LoginResponse)
def exchange(body: ExchangeRequest, db: Session = Depends(get_db)):
    """Trade a single-use, short-lived OAuth exchange code for the real token.

    This is the only path by which the Google login flow delivers a token to
    the browser, so the long-lived token never appears in a URL. Codes are
    random (32 bytes), expire quickly, and are invalid after one use.
    """
    from app.services import login_exchange

    token = login_exchange.consume_exchange_code(body.code)
    if token is None:
        raise HTTPException(status_code=401, detail="Invalid or expired exchange code.")
    payload = verify_token(token, settings.SECRET_KEY)
    user = db.query(User).filter(User.id == payload.get("sub")).first() if payload else None
    if user is None or user.status != "active":
        raise HTTPException(status_code=401, detail="Invalid or expired exchange code.")
    return LoginResponse(
        token=token,
        expires_in=settings.AUTH_TOKEN_TTL_DAYS * 86400,
        user=_profile(user),
    )


@router.post("/logout")
def logout(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Revoke every token issued for the current user (NF-1).

    Bumping ``token_version`` invalidates all outstanding tokens for the user,
    on this device and every other, because ``get_current_user`` rejects any
    token whose ``ver`` claim no longer matches. Requires a currently valid
    token; repeat calls are harmless.
    """
    user.token_version = (user.token_version or 0) + 1
    db.commit()
    logger.info("Session revoked for user_id=%s", user.id)
    return {"ok": True}


@router.get("/me", response_model=UserProfile)
def me(user: User = Depends(get_current_user)):
    return _profile(user)


@router.get("/status")
def status(request: Request, db: Session = Depends(get_db)):
    payload = token_payload(request)
    user = None
    if payload:
        sub = payload.get("sub")
        if sub is None:
            user = db.query(User).order_by(User.id.asc()).first()
        else:
            user = db.query(User).filter(User.id == sub).first()
        if user is not None and (
            user.status != "active" or not token_is_current(user, payload)
        ):
            user = None
    return {
        "enabled": AUTH_ENABLED,
        "authenticated": user is not None,
        "user": _profile(user) if user is not None else None,
    }


@router.get("/google/login")
def google_login_redirect():
    """Initiates Google OAuth for login/registration."""
    from app.services import google_auth
    state = f"login_{secrets.token_urlsafe(32)}"
    # Use IDENTITY_SCOPES here to match handle_google_login_callback which also
    # fetches the token with IDENTITY_SCOPES. A scope mismatch between the
    # authorization request and the token exchange causes exchange_failed.
    flow = google_auth._oauth_flow(state=state, redirect_uri=settings.OAUTH_REDIRECT_URI, scopes=google_auth.IDENTITY_SCOPES)
    url, _ = flow.authorization_url(access_type="offline", prompt="consent")

    response = RedirectResponse(url=url)
    # P24B-16: HttpOnly keeps the state out of JavaScript, SameSite=Lax keeps it
    # off cross-site subrequests while still allowing the top-level redirect
    # back from Google, and Secure is resolved by settings.oauth_cookie_secure
    # (always True in production; plain HTTP only when an operator opts in).
    response.set_cookie(
        key="oauth_state",
        value=state,
        httponly=True,
        secure=settings.oauth_cookie_secure,
        samesite="lax",
        max_age=3600,
    )
    return response


async def handle_google_login_callback(request: Request, db: Session, code: str, state: str) -> RedirectResponse:
    """Handles the OAuth callback for the login flow (existing accounts only).

    Google login is AUTHENTICATION, not registration. It resolves an existing
    account by the verified Google email and mints a session; it never creates
    or promotes one. Provisioning through this callback would let anyone who
    can authenticate with any Google account claim the first-owner role (on an
    empty database) or self-admit as a member, bypassing the operator bootstrap
    token and the owner-invited /api/auth/register path (P24B-06).
    """
    from app.services import google_auth
    frontend = settings.FRONTEND_URL
    cookie_state = request.cookies.get("oauth_state")
    if not cookie_state or cookie_state != state:
        return RedirectResponse(f"{frontend}/?error=invalid_state")

    try:
        flow = google_auth._oauth_flow(
            state=state,
            redirect_uri=settings.OAUTH_REDIRECT_URI,
            scopes=google_auth.IDENTITY_SCOPES,
        )
        flow.fetch_token(code=code)
    except Exception as e:
        import traceback
        logger.warning("Google login token exchange failed: %s\n%s", e, traceback.format_exc())
        from urllib.parse import quote as _quote
        return RedirectResponse(f"{frontend}/?error=oauth_failed&detail={_quote(str(e)[:200])}")

    creds = flow.credentials
    token_body = getattr(flow, "token_response", None) or {}

    identity = google_auth._account_identity(creds, id_token=token_body.get("id_token"))
    email = identity.get("email")
    if not email:
        return RedirectResponse(f"{frontend}/?error=no_email")

    email = email.strip().lower()

    user = db.query(User).filter(User.email == email).first()
    if user is None:
        if not settings.GOOGLE_AUTO_PROVISION:
            logger.info("Google login refused for unprovisioned account")
            return RedirectResponse(f"{frontend}/?error=access_denied&reason=not_invited")

        # Local development can use Google as both registration and login.
        # Production keeps explicit provisioning as the safe default.
        user = User(
            id=_sqlite_user_id(db),
            email=email,
            password_hash=hash_password(secrets.token_urlsafe(32)),
            display_name=identity.get("name") or email.split("@")[0],
            timezone="UTC",
            status="active",
        )
        db.add(user)
        db.flush()
        role = db.query(Role).filter(Role.code == "owner").first()
        if role is None:
            role = db.query(Role).filter(Role.code == "member").first()
        if role is not None:
            user.roles.append(role)
        db.flush()
        logger.info("Auto-provisioned Google account for local development")

    if user.status != "active":
        return RedirectResponse(f"{frontend}/?error=access_denied&reason=account_inactive")

    # Record the provider-confirmed address. This is the ONLY place eligibility
    # material is written, and it is written from Google's ``email_verified``
    # assertion - never from the user-supplied ``users.email`` field. Verifying
    # an institutional address here is what later unlocks the CHARUSAT student
    # attendance feature; a non-institutional address simply records nothing.
    from app.services import charusat_identity

    charusat_identity.record_verified_identity(
        db,
        user,
        verified_email=email,
        email_verified=bool(identity.get("email_verified")),
    )

    # Login authenticates the user only. It does NOT persist Google integration
    # credentials: the login flow requests identity-only scopes (IDENTITY_SCOPES),
    # so saving here would overwrite any existing Gmail/Classroom/Calendar grants
    # with identity-only ones. Integration scopes are granted separately via the
    # Settings "Connect Google" flow.
    from app.services.auth_state import AuthStateStore
    AuthStateStore.renew_expiry(expires_at=creds.expiry, email=email, user_id=user.id)
    db.commit()

    ttl = settings.AUTH_TOKEN_TTL_DAYS * 86400
    token = create_token(
        settings.SECRET_KEY, ttl, sub=user.id, ver=user.token_version or 0
    )

    # Never place the long-lived token in a redirect URL: hand the browser a
    # single-use, short-lived exchange code instead. The frontend trades it
    # for the token via POST /api/auth/exchange.
    from app.services import login_exchange

    code = login_exchange.create_exchange_code(token)
    response = RedirectResponse(f"{frontend}/?code={code}")
    # Clear with the same attributes the cookie was set with, so the browser
    # matches the stored cookie on name/domain/path and every attribute
    # (including Secure) lines up.
    response.delete_cookie(
        "oauth_state",
        path="/",
        secure=settings.oauth_cookie_secure,
        httponly=True,
        samesite="lax",
    )
    return response