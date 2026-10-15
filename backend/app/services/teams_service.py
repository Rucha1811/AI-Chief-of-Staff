import logging
import time
from datetime import datetime, timezone
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import encrypt_bytes, decrypt_bytes
from app.models.event import Event
from app.models.user_integration import UserIntegration

logger = logging.getLogger(__name__)

PROVIDER = "ms_teams"

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
MS_SCOPES = "https://graph.microsoft.com/Calendars.Read offline_access"


class TeamsAuthError(RuntimeError):
    pass


class TeamsUnavailableError(RuntimeError):
    pass


def is_configured() -> bool:
    return bool(settings.MS_CLIENT_ID)


def _tenant() -> str:
    return settings.MS_TENANT_ID or "consumers"


def _naive_utc(dt):
    if dt is None:
        return None
    if getattr(dt, "tzinfo", None) is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def integration_row(db: Session, user_id: int) -> Optional[UserIntegration]:
    """The current user's Microsoft 365 integration row (provider ms_teams)."""
    if user_id is None:
        return None
    return (
        db.query(UserIntegration)
        .filter(UserIntegration.user_id == user_id, UserIntegration.provider == PROVIDER)
        .first()
    )


def _token_from_row(row: Optional[UserIntegration]) -> Optional[dict]:
    if row is None or not row.access_token_encrypted:
        return None
    try:
        access_token = (
            decrypt_bytes(row.access_token_encrypted.encode(), settings.SECRET_KEY).decode()
            if row.access_token_encrypted
            else None
        )
        refresh_token = (
            decrypt_bytes(row.refresh_token_encrypted.encode(), settings.SECRET_KEY).decode()
            if row.refresh_token_encrypted
            else None
        )
    except Exception:
        return None
    meta = dict(row.meta or {})
    expiry = meta.get("token_expires_at")
    if expiry is None and row.token_expires_at is not None:
        expiry = (row.token_expires_at - datetime(1970, 1, 1)).total_seconds()
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at": expiry or 0,
        "account": row.account_id,
        "scope": row.scopes or MS_SCOPES,
    }


def _save_token_for_user(
    db: Session, user_id: int, token: dict, account: Optional[str] = None
) -> None:
    row = integration_row(db, user_id)
    if row is None:
        row = UserIntegration(user_id=user_id, provider=PROVIDER, status="active")
        db.add(row)
    if token.get("access_token"):
        row.access_token_encrypted = encrypt_bytes(
            token["access_token"].encode(), settings.SECRET_KEY
        ).decode()
    if token.get("refresh_token"):
        row.refresh_token_encrypted = encrypt_bytes(
            token["refresh_token"].encode(), settings.SECRET_KEY
        ).decode()
    expires_at = token.get("expires_at")
    if expires_at:
        row.token_expires_at = _naive_utc(
            datetime.fromtimestamp(expires_at, timezone.utc)
        )
    if token.get("scope"):
        row.scopes = token["scope"]
    if account:
        row.account_id = account
    row.status = "active"
    row.last_error = None
    meta = dict(row.meta or {})
    if expires_at:
        meta["token_expires_at"] = expires_at
    row.meta = meta
    db.commit()


def _device_flow(db: Session, user_id: int):
    row = integration_row(db, user_id)
    if row is None:
        return None
    return (row.meta or {}).get("device_flow")


def _store_device_flow(db: Session, user_id: int, flow: dict) -> None:
    row = integration_row(db, user_id)
    if row is None:
        row = UserIntegration(user_id=user_id, provider=PROVIDER, status="pending")
        db.add(row)
    meta = dict(row.meta or {})
    meta["device_flow"] = flow
    row.meta = meta
    db.commit()


def _clear_device_flow(db: Session, user_id: int) -> None:
    row = integration_row(db, user_id)
    if row is None:
        return
    meta = dict(row.meta or {})
    meta.pop("device_flow", None)
    row.meta = meta
    db.commit()


async def start_device_flow(db: Session, user_id: int) -> dict:
    if user_id is None:
        raise TeamsAuthError("Authenticated user required.")
    if not is_configured():
        raise TeamsAuthError("Microsoft not configured. Set MS_CLIENT_ID in .env")
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            f"https://login.microsoftonline.com/{_tenant()}/oauth2/v2.0/devicecode",
            data={
                "client_id": settings.MS_CLIENT_ID,
                "scope": MS_SCOPES,
            },
        )
        resp.raise_for_status()
        data = resp.json()
    _store_device_flow(
        db,
        user_id,
        {
            "device_code": data.get("device_code"),
            "expires_at": time.time() + data.get("expires_in", 900),
            "interval": data.get("interval", 5),
        },
    )
    return {
        "user_code": data.get("user_code"),
        "verification_uri": data.get("verification_uri"),
        "message": data.get("message"),
        "expires_in": data.get("expires_in"),
    }


async def poll_device_flow(db: Session, user_id: int) -> dict:
    if user_id is None:
        raise TeamsAuthError("Authenticated user required.")
    if not is_configured():
        raise TeamsAuthError("Microsoft not configured. Set MS_CLIENT_ID in .env")
    flow = _device_flow(db, user_id)
    if not flow:
        return {"status": "no_pending_flow"}
    if time.time() > (flow.get("expires_at") or 0):
        _clear_device_flow(db, user_id)
        return {"status": "expired"}

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            f"https://login.microsoftonline.com/{_tenant()}/oauth2/v2.0/token",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "client_id": settings.MS_CLIENT_ID,
                "device_code": flow["device_code"],
            },
        )
        data = resp.json()

    if "access_token" in data:
        _clear_device_flow(db, user_id)
        token = {
            "access_token": data["access_token"],
            "refresh_token": data.get("refresh_token"),
            "expires_at": time.time() + data.get("expires_in", 3600),
            "scope": data.get("scope", ""),
        }
        account = (await _me(data["access_token"])).get("userPrincipalName")
        _save_token_for_user(db, user_id, token, account=account)
        return {"status": "success", "account": account}
    if data.get("error") == "authorization_pending":
        return {"status": "pending"}
    return {"status": "error", "detail": data.get("error_description", data.get("error"))}


async def _me(access_token: str) -> dict:
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{GRAPH_BASE}/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        return resp.json() if resp.status_code == 200 else {}


async def _access_token_for_user(db: Session, user_id: int) -> str:
    token = _token_from_row(integration_row(db, user_id))
    if not token or not token.get("access_token"):
        raise TeamsAuthError(
            "Microsoft account not connected yet. Call /api/teams/connect to sign in."
        )
    if token["expires_at"] > time.time() + 60:
        return token["access_token"]

    refresh_token = token.get("refresh_token")
    if not refresh_token:
        raise TeamsAuthError("Microsoft token can't be refreshed. Re-connect.")
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            f"https://login.microsoftonline.com/{_tenant()}/oauth2/v2.0/token",
            data={
                "grant_type": "refresh_token",
                "client_id": settings.MS_CLIENT_ID,
                "refresh_token": refresh_token,
                "scope": MS_SCOPES,
            },
        )
        data = resp.json()
    if "access_token" not in data:
        raise TeamsAuthError("Microsoft refresh failed. Re-connect via /api/teams/connect.")
    token["access_token"] = data["access_token"]
    if data.get("refresh_token"):
        token["refresh_token"] = data["refresh_token"]
    token["expires_at"] = time.time() + data.get("expires_in", 3600)
    token["scope"] = data.get("scope") or token.get("scope") or MS_SCOPES
    _save_token_for_user(db, user_id, token)
    return token["access_token"]


async def status(db: Session, user_id: int) -> dict:
    if user_id is None:
        return {"configured": is_configured(), "connected": False, "reason": "unauthorized"}
    if not is_configured():
        return {"configured": False, "connected": False, "reason": "not_configured"}
    row = integration_row(db, user_id)
    if row is None or not row.access_token_encrypted:
        return {"configured": True, "connected": False, "reason": "not_connected"}
    try:
        await _access_token_for_user(db, user_id)
        return {
            "configured": True,
            "connected": True,
            "account": row.account_id,
            "reason": "ok",
        }
    except TeamsAuthError as e:
        return {"configured": True, "connected": False, "reason": str(e)}


async def fetch_events(db: Session, user_id: int, limit: int = 100) -> list[dict]:
    token = await _access_token_for_user(db, user_id)
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(
            f"{GRAPH_BASE}/me/events",
            headers={
                "Authorization": f"Bearer {token}",
                "Prefer": 'outlook.timezone="UTC"',
            },
            params={"$top": limit, "$select": "id,subject,start,end,location,isAllDay,bodyPreview"},
        )
        if resp.status_code in (401, 403):
            raise TeamsAuthError("Microsoft authorization expired. Re-connect.")
        if resp.status_code >= 500:
            raise TeamsUnavailableError(f"Microsoft Graph error (HTTP {resp.status_code})")
        resp.raise_for_status()
        return resp.json().get("value", [])


def _to_event_item(item: dict) -> dict:
    start = item.get("start") or {}
    end = item.get("end") or {}
    try:
        start_dt = datetime.fromisoformat(start.get("dateTime", "").replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(end.get("dateTime", "").replace("Z", "+00:00"))
    except ValueError:
        return None
    start_dt = start_dt.astimezone(timezone.utc).replace(tzinfo=None)
    end_dt = end_dt.astimezone(timezone.utc).replace(tzinfo=None)
    location = (item.get("location") or {}).get("displayName")
    return {
        # calendar_event_id is intentionally NOT prefilled with the MS id
        # so that push_events_to_calendar can push it to Google Calendar.
        # The MS id is stored in source_id instead.
        "title": item.get("subject") or "(No subject)",
        "description": item.get("bodyPreview"),
        "location": location,
        "start_time": start_dt,
        "end_time": end_dt,
        "is_all_day": bool(item.get("isAllDay")),
    }


async def sync_events(db: Session = None, user_id: int = None) -> dict:
    if user_id is None:
        raise TeamsAuthError("Authenticated user required.")
    owns_db = db is None
    if owns_db:
        db = SessionLocal()
    events = await fetch_events(db, user_id, 100)
    created = 0
    skipped = 0
    try:
        existing_titles = {
            e.title
            for e in db.query(Event)
            .filter(Event.source == "teams", Event.user_id == user_id)
            .all()
        }
        for item in events:
            parsed = _to_event_item(item)
            if not parsed:
                continue
            if parsed["title"] in existing_titles:
                skipped += 1
                continue
            db.add(Event(source="teams", user_id=user_id, **parsed))
            created += 1
        db.commit()
    finally:
        if owns_db:
            db.close()
    return {"fetched": len(events), "created": created, "skipped": skipped}