"""P0-1: Teams / Microsoft 365 per-user isolation.

Verifies all /api/teams routes require an authenticated user, that every
route/operation is scoped to the caller's user_id, that Microsoft state and
credentials are stored per user, and that a failure for one user never blocks
another user's sync.
"""

import asyncio
import time

import pytest

from app.core.config import settings
from app.core.security import encrypt_bytes
from app.services import teams_service
from tests.conftest import auth


@pytest.fixture(autouse=True)
def _ms_configured(monkeypatch):
    monkeypatch.setattr(settings, "MS_CLIENT_ID", "test-client-id")


# --------------------------------------------------------------------------- #
# Routes require authentication
# --------------------------------------------------------------------------- #
def test_teams_routes_require_auth(client):
    assert client.get("/api/teams/status").status_code == 401
    assert client.post("/api/teams/connect").status_code == 401
    assert client.post("/api/teams/connect/poll").status_code == 401
    assert client.post("/api/teams/sync").status_code == 401


# --------------------------------------------------------------------------- #
# Routes pass the authenticated user's id into the service
# --------------------------------------------------------------------------- #
def test_teams_routes_use_authenticated_user(client, owner, member, monkeypatch):
    captured = {"status": [], "connect": [], "poll": [], "sync": []}

    async def _fake_status(db, user_id):
        captured["status"].append(user_id)
        return {"configured": False, "connected": False}

    async def _fake_connect(db, user_id):
        captured["connect"].append(user_id)
        return {"user_code": "X", "verification_uri": "https://example.test"}

    async def _fake_poll(db, user_id):
        captured["poll"].append(user_id)
        return {"status": "no_pending_flow"}

    async def _fake_sync(db=None, user_id=None):
        captured["sync"].append(user_id)
        return {"fetched": 0, "created": 0, "skipped": 0}

    monkeypatch.setattr("app.services.teams_service.status", _fake_status)
    monkeypatch.setattr("app.services.teams_service.start_device_flow", _fake_connect)
    monkeypatch.setattr("app.services.teams_service.poll_device_flow", _fake_poll)
    monkeypatch.setattr("app.services.teams_service.sync_events", _fake_sync)

    for path in ("/api/teams/status", "/api/teams/connect", "/api/teams/connect/poll", "/api/teams/sync"):
        method = client.get if path == "/api/teams/status" else client.post
        r = method(path, headers=auth(owner["token"]))
        assert r.status_code == 200, f"{path} -> {r.status_code}"

    # A second user exercises the same routes with their own identity.
    assert client.get("/api/teams/status", headers=auth(member["token"])).status_code == 200
    assert client.post("/api/teams/sync", headers=auth(member["token"])).status_code == 200

    # Every call was resolved to exactly the caller's user id. A never used
    # B's id and vice versa.
    assert captured["status"] == [owner["id"], member["id"]]
    assert captured["connect"] == [owner["id"]]
    assert captured["poll"] == [owner["id"]]
    assert captured["sync"] == [owner["id"], member["id"]]


# --------------------------------------------------------------------------- #
# Status is per user: A sees A's connection, not B's
# --------------------------------------------------------------------------- #
def test_teams_status_user_scoped(client, db, owner, member):
    _seed_owner_ms_token(db, owner["id"], account="alice@contoso.com")

    owner_status = client.get("/api/teams/status", headers=auth(owner["token"])).json()
    assert owner_status["connected"] is True
    assert owner_status["account"] == "alice@contoso.com"

    member_status = client.get("/api/teams/status", headers=auth(member["token"])).json()
    assert member_status["connected"] is False
    assert member_status.get("account") is None


# --------------------------------------------------------------------------- #
# Device-flow state is per user
# --------------------------------------------------------------------------- #
def test_teams_device_flow_state_is_per_user(db, owner, member):
    # A has a pending (but already expired) device flow.
    teams_service._store_device_flow(
        db,
        owner["id"],
        {
            "device_code": "dc-owner",
            "expires_at": time.time() - 10,
            "interval": 5,
        },
    )

    # A can only ever see/handle A's own flow.
    a_poll = asyncio.run(teams_service.poll_device_flow(db, owner["id"]))
    assert a_poll["status"] == "expired"

    # B has no pending flow, and cannot reach A's.
    b_poll = asyncio.run(teams_service.poll_device_flow(db, member["id"]))
    assert b_poll["status"] == "no_pending_flow"


# --------------------------------------------------------------------------- #
# Sync scopes created events to each user and dedupes per user
# --------------------------------------------------------------------------- #
def _ms_event(ev_id, subject, day="2026-03-10", start="10:00:00", end="11:00:00"):
    return {
        "id": ev_id,
        "subject": subject,
        "start": {"dateTime": f"{day}T{start}Z", "timeZone": "UTC"},
        "end": {"dateTime": f"{day}T{end}Z", "timeZone": "UTC"},
        "location": {"displayName": "Room 1"},
        "isAllDay": False,
        "bodyPreview": "notes",
    }


def test_teams_sync_stamps_user_and_dedupes_per_user(db, owner, member, monkeypatch):
    from app.models.event import Event

    async def _fake_fetch(db=None, user_id=None, limit=100):
        return [_ms_event("ms-ev-1", "Sync A")]

    monkeypatch.setattr("app.services.teams_service.fetch_events", _fake_fetch)

    res_a = asyncio.run(teams_service.sync_events(db=db, user_id=owner["id"]))
    assert res_a["created"] == 1

    res_b = asyncio.run(teams_service.sync_events(db=db, user_id=member["id"]))
    assert res_b["created"] == 1

    events = db.query(Event).filter(Event.source == "teams").all()
    assert len(events) == 2
    assert {e.user_id for e in events} == {owner["id"], member["id"]}
    assert {e.calendar_event_id for e in events} == {"ms:ms-ev-1"}

    # Re-sync per user is idempotent and never touches the other user's rows.
    res_a2 = asyncio.run(teams_service.sync_events(db=db, user_id=owner["id"]))
    assert res_a2["created"] == 0
    assert res_a2["skipped"] == 1
    assert db.query(Event).filter(Event.source == "teams").count() == 2


# --------------------------------------------------------------------------- #
# One user's failure never blocks another user's sync
# --------------------------------------------------------------------------- #
def test_teams_sync_failure_isolated_per_user(client, db, owner, member, monkeypatch):
    from app.models.event import Event

    async def _fake_fetch(db=None, user_id=None, limit=100):
        if user_id == owner["id"]:
            raise teams_service.TeamsAuthError("owner's Microsoft auth expired")
        return [_ms_event("ms-ev-2", "Member sync")]

    monkeypatch.setattr("app.services.teams_service.fetch_events", _fake_fetch)

    owner_resp = client.post("/api/teams/sync", headers=auth(owner["token"]))
    assert owner_resp.status_code == 503
    # P2.4A (B5): the client gets a generic message; the raw provider error
    # ("owner's Microsoft auth expired") is logged server-side, never leaked.
    assert "expired" not in owner_resp.json()["detail"]
    assert owner_resp.json()["detail"] == (
        "Teams authorization failed. Check Microsoft configuration or reconnect."
    )

    member_resp = client.post("/api/teams/sync", headers=auth(member["token"]))
    assert member_resp.status_code == 200
    assert member_resp.json()["created"] == 1

    events = db.query(Event).filter(Event.source == "teams").all()
    assert len(events) == 1
    assert events[0].user_id == member["id"]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _seed_owner_ms_token(db, user_id, account):
    from app.models.user_integration import UserIntegration

    row = UserIntegration(
        user_id=user_id,
        provider="ms_teams",
        status="active",
        account_id=account,
        access_token_encrypted=encrypt_bytes(b"access-token", settings.SECRET_KEY).decode(),
        refresh_token_encrypted=encrypt_bytes(b"refresh-token", settings.SECRET_KEY).decode(),
        scopes=teams_service.MS_SCOPES,
        meta={"token_expires_at": time.time() + 3600},
    )
    db.add(row)
    db.commit()
    return row