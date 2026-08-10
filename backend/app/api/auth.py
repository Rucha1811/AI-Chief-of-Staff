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
def login(body: LoginRequest):
    if not AUTH_ENABLED:
        raise HTTPException(status_code=400, detail="Authentication is not enabled on this server.")
    ttl = settings.AUTH_TOKEN_TTL_DAYS * 86400

    if body.emergency_code:
        from app.services.emergency_codes import consume_emergency_code

        if consume_emergency_code(body.emergency_code):
            return LoginResponse(token=create_token(settings.SECRET_KEY, ttl), expires_in=ttl)

    if _AUTH_HASH and verify_password(body.password, _AUTH_HASH):
        return LoginResponse(token=create_token(settings.SECRET_KEY, ttl), expires_in=ttl)

    raise HTTPException(status_code=401, detail="Incorrect password or invalid emergency code.")


@router.get("/status")
def status(request: Request):
    return {
        "enabled": AUTH_ENABLED,
        "authenticated": is_authenticated(request),
    }
