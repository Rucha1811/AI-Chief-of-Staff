"""Phase E security regression tests.

Covers the areas audited in Phase E:

* E1/E3 - the caller-supplied Google ``redirect_uri`` override must be a
  loopback *host*, not a string that merely starts with ``http://localhost``.
  The prefix check accepted ``http://localhost.evil.com`` and
  ``http://localhost:3000@evil.com``; the fix parses the URL and matches the
  hostname exactly.
* E1 - bearer-token integrity: tampered signatures, expired tokens and
  malformed tokens are rejected, and the documented legacy (subject-less)
  token still resolves to the bootstrap owner.
* E2 - account status and permission enforcement: a disabled account's token is
  refused, a member cannot manage users or integrations.
* E2 - multi-user data isolation: documents, notifications and timetable rows
  owned by one user are invisible and immutable to another.
* E4/E5 - the n8n / agent endpoints are API-key-only: a user session alone can
  never drive them.

These assert behaviour against the running app, not source text, so any of the
invariants above regressing fails here.
"""

import base64
import hashlib
import hmac
import time

import pytest

from app.core.config import settings
from app.core.security import create_token, verify_token
from app.services import google_auth
from tests.conftest import auth


# ---------------------------------------------------------------------------
# E1/E3 - Google authorise redirect_uri validation
# ---------------------------------------------------------------------------

_REJECTED_REDIRECTS = [
    "http://localhost.evil.com/callback",
    "http://localhost.evil.com:8000/callback",
    "http://127.0.0.1.evil.com/callback",
    "http://localhost:3000@evil.com/callback",
    "http://evil.com/localhost/callback",
    "https://evil.com/callback",
    "ftp://localhost/callback",
    "//localhost/callback",
    "javascript:alert(1)",
    "http://[::2]/callback",
]

_ACCEPTED_REDIRECTS = [
    "http://localhost/callback",
    "http://localhost:8000/api/integrations/google/callback",
    "http://127.0.0.1:3000/cb",
    "https://localhost/cb",
    "http://[::1]:8000/cb",
]


@pytest.fixture
def _stub_authorize_url(monkeypatch):
    """Replace the Google round-trip with a stub.

    The validation under test happens *before* any provider call, so the stub
    only needs to make a valid request succeed without touching Google.
    """

    def _build(db, user_id=None, redirect_uri=None):
        return {
            "url": "https://accounts.google.com/o/oauth2/auth?stub=1",
            "state": "stub-state",
            "redirect_uri": redirect_uri,
        }

    monkeypatch.setattr(google_auth, "build_authorization_url", _build)


@pytest.mark.parametrize("uri", _REJECTED_REDIRECTS)
def test_authorize_rejects_non_loopback_redirect(client, owner, uri):
    r = client.get(
        "/api/integrations/google/authorize",
        params={"redirect_uri": uri},
        headers=auth(owner["token"]),
    )
    assert r.status_code == 400, f"{uri!r} should be rejected, got {r.status_code}"


@pytest.mark.parametrize("uri", _ACCEPTED_REDIRECTS)
def test_authorize_allows_loopback_redirect(client, owner, _stub_authorize_url, uri):
    r = client.get(
        "/api/integrations/google/authorize",
        params={"redirect_uri": uri},
        headers=auth(owner["token"]),
    )
    assert r.status_code == 200, f"{uri!r} should be allowed, got {r.status_code}"
    assert r.json()["redirect_uri"] == uri


def test_authorize_without_redirect_uri_uses_configured(
    client, owner, _stub_authorize_url
):
    r = client.get(
        "/api/integrations/google/authorize", headers=auth(owner["token"])
    )
    assert r.status_code == 200


def test_authorize_requires_a_session(client, owner):
    r = client.get("/api/integrations/google/authorize")
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# E1 - bearer-token integrity
# ---------------------------------------------------------------------------


def test_tampered_signature_is_rejected():
    token = create_token(settings.SECRET_KEY, sub=1)
    payload, signature = token.rsplit(".", 1)
    flipped = "0" if signature[0] != "0" else "1"
    tampered = f"{payload}.{flipped}{signature[1:]}"
    assert verify_token(tampered, settings.SECRET_KEY) is None


def test_token_signed_with_wrong_secret_is_rejected(client, owner):
    forged = create_token("not-the-server-secret", sub=owner["id"])
    r = client.get("/api/auth/me", headers=auth(forged))
    assert r.status_code == 401


def test_expired_token_is_rejected():
    expired = create_token(settings.SECRET_KEY, ttl_seconds=-60, sub=1)
    assert verify_token(expired, settings.SECRET_KEY) is None


@pytest.mark.parametrize("token", ["", "abc", "abc.def", "a.b.c", "..", "eyJ9.bogus"])
def test_malformed_tokens_are_rejected(token):
    assert verify_token(token, settings.SECRET_KEY) is None


def test_legacy_subjectless_token_binds_to_owner(client, owner):
    legacy = create_token(settings.SECRET_KEY)
    r = client.get("/api/auth/me", headers=auth(legacy))
    assert r.status_code == 200
    assert r.json()["email"] == owner["email"]


def test_token_expiry_is_honoured_at_the_api_edge(client, owner):
    almost_expired = create_token(settings.SECRET_KEY, ttl_seconds=1, sub=owner["id"])
    assert verify_token(almost_expired, settings.SECRET_KEY) is not None
    # Rewrite exp into the past while keeping a valid signature.
    payload, _sig = almost_expired.rsplit(".", 1)
    padded = payload + "=" * (-len(payload) % 4)
    import json

    claims = json.loads(base64.urlsafe_b64decode(padded))
    claims["exp"] = int(time.time()) - 1
    encoded = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    sig = hmac.new(
        settings.SECRET_KEY.encode(), encoded.encode(), hashlib.sha256
    ).hexdigest()
    past = f"{encoded}.{sig}"
    assert verify_token(past, settings.SECRET_KEY) is None
    assert client.get("/api/auth/me", headers=auth(past)).status_code == 401


# ---------------------------------------------------------------------------
# E2 - account status and permission enforcement
# ---------------------------------------------------------------------------


def test_disabled_account_token_is_refused(client, owner, db):
    from app.models.user import User

    row = db.query(User).filter(User.id == owner["id"]).first()
    row.status = "disabled"
    db.commit()

    r = client.get("/api/auth/me", headers=auth(owner["token"]))
    assert r.status_code == 403


def test_member_cannot_manage_integrations(client, member):
    r = client.post(
        "/api/integrations/user",
        json={"provider": "google", "account_id": "x@y.test"},
        headers=auth(member["token"]),
    )
    assert r.status_code == 403


def test_member_cannot_create_users(client, member):
    r = client.post(
        "/api/auth/register",
        json={"email": "sneaky@aichief.test", "password": "sneakypassword1"},
        headers=auth(member["token"]),
    )
    assert r.status_code == 403


@pytest.mark.parametrize(
    "path",
    ["/api/emails", "/api/events", "/api/tasks", "/api/notifications", "/api/documents"],
)
def test_protected_endpoints_require_a_token(client, path):
    assert client.get(path).status_code == 401


# ---------------------------------------------------------------------------
# E2 - multi-user data isolation
# ---------------------------------------------------------------------------


def test_document_is_isolated_between_users(client, owner, member, db):
    from app.models.document import Document

    doc = Document(
        filename="owner-secret.txt",
        file_type=".txt",
        file_size=5,
        storage_path="/tmp/does-not-exist",
        user_id=owner["id"],
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    # The member cannot read, query or delete it.
    assert (
        client.get(
            f"/api/documents/{doc.id}", headers=auth(member["token"])
        ).status_code
        == 404
    )
    assert (
        client.delete(
            f"/api/documents/{doc.id}", headers=auth(member["token"])
        ).status_code
        == 404
    )
    # The owner still can.
    assert (
        client.get(
            f"/api/documents/{doc.id}", headers=auth(owner["token"])
        ).status_code
        == 200
    )


def test_notifications_are_scoped_to_their_owner(client, owner, member, db):
    from app.models.notification import Notification

    n = Notification(
        user_id=owner["id"],
        type="test",
        title="owner-only",
        message="private",
        priority="normal",
    )
    db.add(n)
    db.commit()
    db.refresh(n)

    # Not visible in the member's list.
    listed = client.get("/api/notifications", headers=auth(member["token"])).json()
    assert all(item["id"] != n.id for item in listed)

    # The member's "mark read" is a no-op for someone else's row.
    r = client.post(
        f"/api/notifications/{n.id}/read", headers=auth(member["token"])
    )
    assert r.status_code == 200
    assert r.json()["ok"] is False

    db.refresh(n)
    assert n.is_read is False


def test_timetable_is_isolated_between_users(client, owner, member):
    created = client.post(
        "/api/timetable",
        json={
            "title": "owner-lecture",
            "day_of_week": 0,
            "start_time": "09:00",
            "end_time": "10:00",
        },
        headers=auth(owner["token"]),
    )
    assert created.status_code == 200, created.text
    entry_id = created.json()["id"]

    member_list = client.get("/api/timetable", headers=auth(member["token"])).json()
    assert all(e["id"] != entry_id for e in member_list)

    assert (
        client.put(
            f"/api/timetable/{entry_id}",
            json={"title": "hijacked"},
            headers=auth(member["token"]),
        ).status_code
        == 404
    )
    assert (
        client.delete(
            f"/api/timetable/{entry_id}", headers=auth(member["token"])
        ).status_code
        == 404
    )


# ---------------------------------------------------------------------------
# E4/E5 - server-to-server endpoints are API-key-only
# ---------------------------------------------------------------------------


def test_session_alone_cannot_drive_api_key_endpoint(client, owner):
    r = client.get("/api/integrations/config", headers=auth(owner["token"]))
    assert r.status_code == 401


def test_api_key_alone_can_read_config(client):
    key = settings.INGEST_API_KEY
    assert key, "INGEST_API_KEY must be configured for the test environment"
    r = client.get("/api/integrations/config", headers={"x-api-key": key})
    assert r.status_code == 200
    body = r.json()
    # Config is operational metadata only - never a secret.
    assert "ingest_endpoint" in body
    assert key not in r.text


# ---------------------------------------------------------------------------
# E2 - operational detail is pinned to a live, active user
# ---------------------------------------------------------------------------


def test_system_info_is_refused_for_a_disabled_user(client, owner, db):
    """A valid signature is not enough: the account must still be active."""
    from app.models.user import User

    assert client.get("/api/system/info", headers=auth(owner["token"])).status_code == 200

    row = db.query(User).filter(User.id == owner["id"]).first()
    row.status = "disabled"
    db.commit()

    r = client.get("/api/system/info", headers=auth(owner["token"]))
    assert r.status_code == 403


def test_system_info_is_refused_for_a_deleted_user(client, owner, db):
    from app.models.user import User

    row = db.query(User).filter(User.id == owner["id"]).first()
    db.delete(row)
    db.commit()

    r = client.get("/api/system/info", headers=auth(owner["token"]))
    assert r.status_code == 401

