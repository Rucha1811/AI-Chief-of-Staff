"""Phase F security regression tests.

Covers the controls added or analysed in Phase F:

* NF-1 - session revocation: ``POST /api/auth/logout`` bumps
  ``users.token_version`` (migration 0011) and every previously issued token
  stops authenticating, across /me, /status and the register admin path.
  Legacy tokens (no ``ver`` claim) stay valid while the epoch is 0, so the
  control is opt-in via an explicit logout.
* NF-6 - bounded pagination: caller-supplied ``limit``/``offset`` are clamped
  so a single request cannot materialise an unbounded result set.
* NF-6 - upload size cap: an oversized document upload is rejected with 413
  before the body is written to disk.
"""

import pytest

from app.core.config import settings
from app.core.limits import clamp_limit, clamp_offset
from app.core.security import create_token
from tests.conftest import auth


# ---------------------------------------------------------------------------
# NF-1 - session revocation
# ---------------------------------------------------------------------------


def test_logout_revokes_the_presented_token(client, owner):
    h = auth(owner["token"])
    assert client.get("/api/auth/me", headers=h).status_code == 200

    r = client.post("/api/auth/logout", headers=h)
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True}

    # The same token is now rejected: the epoch moved underneath it.
    assert client.get("/api/auth/me", headers=h).status_code == 401


def test_logout_requires_authentication(client):
    assert client.post("/api/auth/logout").status_code == 401
    assert client.post(
        "/api/auth/logout", headers={"Authorization": "Bearer nope.nope"}
    ).status_code == 401


def test_relogin_after_logout_yields_a_working_token(client, owner):
    old = auth(owner["token"])
    assert client.post("/api/auth/logout", headers=old).status_code == 200

    r = client.post(
        "/api/auth/login",
        json={"email": owner["email"], "password": "ownerpassword123"},
    )
    assert r.status_code == 200, r.text
    new_token = r.json()["token"]

    # New token works; the pre-logout token does not.
    assert client.get("/api/auth/me", headers=auth(new_token)).status_code == 200
    assert client.get("/api/auth/me", headers=old).status_code == 401


def test_status_reports_unauthenticated_after_logout(client, owner):
    h = auth(owner["token"])
    before = client.get("/api/auth/status", headers=h).json()
    assert before["authenticated"] is True

    assert client.post("/api/auth/logout", headers=h).status_code == 200

    after = client.get("/api/auth/status", headers=h).json()
    assert after["authenticated"] is False
    assert after["user"] is None


def test_revoked_admin_token_cannot_register(client, owner):
    admin = auth(owner["token"])
    assert client.post("/api/auth/logout", headers=admin).status_code == 200

    r = client.post(
        "/api/auth/register",
        json={"email": "late@aichief.test", "password": "latepassword123"},
        headers=admin,
    )
    assert r.status_code == 401, r.text


def test_legacy_token_without_ver_claim_still_valid_at_epoch_zero(client, owner):
    # Minted exactly like a pre-migration token: no ``ver`` claim.
    legacy = create_token(settings.SECRET_KEY, sub=owner["id"])
    assert client.get("/api/auth/me", headers=auth(legacy)).status_code == 200


def test_token_with_stale_version_is_rejected(client, owner):
    stale = create_token(settings.SECRET_KEY, sub=owner["id"], ver=999)
    assert client.get("/api/auth/me", headers=auth(stale)).status_code == 401


def test_logout_is_idempotent_but_invalidates_each_prior_epoch(client, owner):
    first = auth(owner["token"])
    assert client.post("/api/auth/logout", headers=first).status_code == 200

    r = client.post(
        "/api/auth/login",
        json={"email": owner["email"], "password": "ownerpassword123"},
    )
    second = auth(r.json()["token"])
    assert client.get("/api/auth/me", headers=second).status_code == 200

    # Logging out again bumps again; the second token dies with it.
    assert client.post("/api/auth/logout", headers=second).status_code == 200
    assert client.get("/api/auth/me", headers=second).status_code == 401
    # The very first token remains dead too.
    assert client.get("/api/auth/me", headers=first).status_code == 401


# ---------------------------------------------------------------------------
# NF-6 - bounded pagination
# ---------------------------------------------------------------------------


def test_clamp_limit_bounds_values():
    assert clamp_limit(-5) == 1
    assert clamp_limit(0) == 1
    assert clamp_limit(1) == 1
    assert clamp_limit(50) == 50
    assert clamp_limit(10**9) == settings.API_MAX_PAGE_SIZE
    assert clamp_limit(10**9, maximum=10) == 10
    # A nonsensical maximum still yields a usable page.
    assert clamp_limit(5, maximum=0) == 1


def test_clamp_offset_never_negative():
    assert clamp_offset(-100) == 0
    assert clamp_offset(0) == 0
    assert clamp_offset(7) == 7


@pytest.mark.parametrize(
    "path",
    [
        "/api/tasks",
        "/api/events",
        "/api/documents",
        "/api/emails",
        "/api/notifications",
        "/api/whatsapp/messages",
    ],
)
def test_list_endpoints_tolerate_extreme_pagination(client, owner, path):
    r = client.get(
        path, params={"limit": 10**9, "offset": -100}, headers=auth(owner["token"])
    )
    assert r.status_code == 200, f"{path}: {r.status_code} {r.text}"
    assert isinstance(r.json(), list)


def test_whatsapp_messages_routes_limit_through_clamp(client, owner, db, monkeypatch):
    """Phase F1: /api/whatsapp/messages was the one list endpoint NF-6 missed.

    Proving the handler calls ``clamp_limit`` (here forced to cap at 1) is the
    only way to catch a regression through the HTTP surface: without the clamp
    a huge ``limit`` still returns every row and the response looks normal."""
    from app.api import whatsapp as whatsapp_api
    from app.models.whatsapp_message import WhatsappMessage

    for i in range(3):
        db.add(
            WhatsappMessage(
                user_id=owner["id"],
                direction="in",
                from_number="+10000000000",
                to_number="+10000000001",
                text=f"message {i}",
                provider="none",
            )
        )
    db.commit()

    monkeypatch.setattr(whatsapp_api, "clamp_limit", lambda *_a, **_k: 1)
    r = client.get(
        "/api/whatsapp/messages",
        params={"limit": 10**9},
        headers=auth(owner["token"]),
    )
    assert r.status_code == 200, r.text
    assert len(r.json()) == 1


# ---------------------------------------------------------------------------
# NF-6 - upload size cap
# ---------------------------------------------------------------------------


def test_upload_over_cap_is_rejected(client, owner, db, monkeypatch):
    monkeypatch.setattr(settings, "MAX_UPLOAD_MB", 0)

    r = client.post(
        "/api/documents/upload",
        files={"file": ("note.txt", b"x" * 32, "text/plain")},
        headers=auth(owner["token"]),
    )
    assert r.status_code == 413, r.text

    from app.models.document import Document

    assert db.query(Document).count() == 0


def test_upload_within_cap_is_accepted(client, owner, db, monkeypatch, tmp_path):
    from app.api import documents as documents_api

    monkeypatch.setattr(settings, "MAX_UPLOAD_MB", 1)
    monkeypatch.setattr(documents_api, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(documents_api, "process_document", lambda path, ext: "hello")

    async def _fake_summary(_text):
        return {"summary": "", "key_points": []}

    monkeypatch.setattr(documents_api.ai_service, "summarize_document", _fake_summary)

    r = client.post(
        "/api/documents/upload",
        files={"file": ("note.txt", b"small body", "text/plain")},
        headers=auth(owner["token"]),
    )
    assert r.status_code == 200, r.text
    assert r.json()["filename"] == "note.txt"
    assert r.json()["file_size"] == len(b"small body")


# ---------------------------------------------------------------------------
# NF-3 - a stored credential with no expiry must not be reported healthy
# ---------------------------------------------------------------------------


def test_missing_expiry_is_not_treated_as_valid(monkeypatch):
    from types import SimpleNamespace

    from app.core.security import encrypt_bytes
    from app.services import google_auth

    monkeypatch.setattr(
        google_auth,
        "_client_config",
        lambda: {
            "client_id": "test.apps.googleusercontent.com",
            "client_secret": "test-secret",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        },
    )
    row = SimpleNamespace(
        refresh_token_encrypted=encrypt_bytes(b"refresh-token", settings.SECRET_KEY).decode(),
        access_token_encrypted=encrypt_bytes(b"access-token", settings.SECRET_KEY).decode(),
        token_expires_at=None,
        scopes="https://www.googleapis.com/auth/gmail.readonly",
    )

    creds = google_auth._creds_from_row(row)

    assert creds is not None
    # A NULL expiry is materialised as an epoch, never left as None (which
    # google.auth reads as "valid forever" and would report false health).
    assert creds.expiry is not None
    assert creds.valid is False


def test_ensure_refreshed_exercises_refresh_when_expiry_unknown(monkeypatch, db, owner):
    from datetime import datetime, timedelta, timezone

    from google.oauth2.credentials import Credentials

    from app.services import google_auth

    calls: list[int] = []

    def _fake_refresh(creds):
        calls.append(1)
        creds.token = "refreshed-access-token"
        creds.expiry = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)

    monkeypatch.setattr(google_auth, "_refresh_creds_only", _fake_refresh)
    monkeypatch.setattr(
        google_auth.AuthStateStore, "renew_expiry", staticmethod(lambda **k: None)
    )

    creds = Credentials(
        token="stale-access-token",
        refresh_token="refresh-token",
        token_uri="https://oauth2.googleapis.com/token",
        client_id="test.apps.googleusercontent.com",
        client_secret="test-secret",
        scopes=["https://www.googleapis.com/auth/gmail.readonly"],
    )
    creds.expiry = datetime(1970, 1, 1)  # as _creds_from_row now yields

    import asyncio

    asyncio.run(google_auth.ensure_refreshed_for_user(db, owner["id"], creds))

    # The refresh was actually attempted (not short-circuited as "valid").
    assert calls == [1]
    assert creds.token == "refreshed-access-token"
