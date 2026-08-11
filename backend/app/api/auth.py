from collections import defaultdict
from time import time
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.core.config import settings
from app.core.security import create_token, hash_password, verify_password, verify_token

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
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


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


class LoginRequest(BaseModel):
    password: str = ""
    emergency_code: Optional[str] = None


class LoginResponse(BaseModel):
    token: str
    expires_in: int


def bearer_token(request: Request) -> Optional[str]:
    header = request.headers.get("Authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return None


def is_authenticated(request: Request) -> bool:
    if not AUTH_ENABLED:
        return True
    token = bearer_token(request)
    return bool(token and verify_token(token, settings.SECRET_KEY))


@router.post("/login", response_model=LoginResponse)
def login(request: Request, body: LoginRequest):
    if not AUTH_ENABLED:
        raise HTTPException(status_code=400, detail="Authentication is not enabled on this server.")
    ip = _client_ip(request)
    _check_rate_limit(ip)
    ttl = settings.AUTH_TOKEN_TTL_DAYS * 86400

    if body.emergency_code:
        from app.services.emergency_codes import consume_emergency_code

        if consume_emergency_code(body.emergency_code):
            _register_attempt(ip, success=True)
            return LoginResponse(token=create_token(settings.SECRET_KEY, ttl), expires_in=ttl)

    if _AUTH_HASH and verify_password(body.password, _AUTH_HASH):
        _register_attempt(ip, success=True)
        return LoginResponse(token=create_token(settings.SECRET_KEY, ttl), expires_in=ttl)

    _register_attempt(ip, success=False)
    raise HTTPException(status_code=401, detail="Incorrect password or invalid emergency code.")


@router.get("/status")
def status(request: Request):
    return {
        "enabled": AUTH_ENABLED,
        "authenticated": is_authenticated(request),
    }
