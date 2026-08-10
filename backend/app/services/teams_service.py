import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.event import Event

logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
MS_CREDENTIALS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "credentials")
MS_TOKEN_FILE = os.path.join(MS_CREDENTIALS_DIR, "ms_token.json")

MS_SCOPES = "https://graph.microsoft.com/Calendars.Read offline_access"

_pending_flows: dict = {}


class TeamsAuthError(RuntimeError):
    pass


class TeamsUnavailableError(RuntimeError):
    pass


def is_configured() -> bool:
    return bool(settings.MS_CLIENT_ID)


def _tenant() -> str:
    return settings.MS_TENANT_ID or "consumers"


def _load_token() -> Optional[dict]:
    if not os.path.exists(MS_TOKEN_FILE):
        return None
    try:
        with open(MS_TOKEN_FILE, "r") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _save_token(token: dict) -> None:
    os.makedirs(MS_CREDENTIALS_DIR, exist_ok=True)
    with open(MS_TOKEN_FILE, "w") as f:
        json.dump(token, f, indent=2)


async def start_device_flow() -> dict:
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
    _pending_flows[settings.MS_CLIENT_ID] = {
        "device_code": data.get("device_code"),
        "expires_at": time.time() + data.get("expires_in", 900),
        "interval": data.get("interval", 5),
    }
    return {
        "user_code": data.get("user_code"),
        "verification_uri": data.get("verification_uri"),
        "message": data.get("message"),
        "expires_in": data.get("expires_in"),
    }


async def poll_device_flow() -> dict:
    if not is_configured():
        raise TeamsAuthError("Microsoft not configured. Set MS_CLIENT_ID in .env")
    flow = _pending_flows.get(settings.MS_CLIENT_ID)
    if not flow:
        return {"status": "no_pending_flow"}
    if time.time() > flow["expires_at"]:
        _pending_flows.pop(settings.MS_CLIENT_ID, None)
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
        _pending_flows.pop(settings.MS_CLIENT_ID, None)
        token = {
            "access_token": data["access_token"],
            "refresh_token": data.get("refresh_token"),
            "expires_at": time.time() + data.get("expires_in", 3600),
            "scope": data.get("scope", ""),
            "account": (await _me(data["access_token"])).get("userPrincipalName"),
        }
        _save_token(token)
        return {"status": "success", "account": token["account"]}
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


async def _access_token() -> str:
    token = _load_token()
    if not token:
        raise TeamsAuthError(
            "Microsoft account not connected yet. Call /api/teams/connect to sign in."
        )
    if token.get("expires_at", 0) > time.time() + 60:
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
    _save_token(token)
    return token["access_token"]


async def status() -> dict:
    if not is_configured():
        return {"configured": False, "connected": False, "reason": "not_configured"}
    token = _load_token()
    if not token:
        return {"configured": True, "connected": False, "reason": "not_connected"}
    try:
        await _access_token()
        return {
            "configured": True,
            "connected": True,
            "account": token.get("account"),
            "reason": "ok",
        }
    except TeamsAuthError as e:
        return {"configured": True, "connected": False, "reason": str(e)}


async def fetch_events(limit: int = 100) -> list[dict]:
    token = await _access_token()
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
        "id": f"ms:{item.get('id')}",
        "title": item.get("subject") or "(No subject)",
        "description": item.get("bodyPreview"),
        "location": location,
        "start_time": start_dt,
        "end_time": end_dt,
        "is_all_day": bool(item.get("isAllDay")),
    }


async def sync_events(db: Session = None) -> dict:
    owns_db = db is None
    if owns_db:
        db = SessionLocal()
    events = await fetch_events(100)
    created = 0
    skipped = 0
    try:
        existing_ids = {
            e.calendar_event_id for e in db.query(Event).filter(Event.source == "teams").all()
        }
        for item in events:
            parsed = _to_event_item(item)
            if not parsed:
                continue
            if parsed["id"] in existing_ids:
                skipped += 1
                continue
            db.add(Event(source="teams", **parsed))
            created += 1
        db.commit()
    finally:
        if owns_db:
            db.close()
    return {"fetched": len(events), "created": created, "skipped": skipped}
