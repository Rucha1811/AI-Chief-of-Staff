"""Regression tests for Google OAuth reliability (task sections 5 and 10).

Covers the gaps not already locked by the scope / reconnect / redaction test
modules:

* redirect-URI validation (loopback-only dev override),
* missing token-expiry handling (NF-3 epoch, never "valid forever"),
* a login-only grant is NOT reported as a full integration connection,
* multi-user integration isolation,
* integration OAuth-state binding to the correct user,
* Calendar scope enforcement,
* Gmail sync dedup is user-scoped (pagination/duplicate prevention).
"""

import asyncio
from datetime import datetime, timedelta

import pytest

from app.core.config import settings
from app.core.security import encrypt_bytes
from app.services import calendar_service
from app.services import google_auth as ga
from app.services import gmail_service


def _enc(value: str) -> str:
    return encrypt_bytes(value.encode(), settings.SECRET_KEY).decode()


@pytest.fixture
def _dummy_client_config(monkeypatch):
    """_creds_from_row calls _client_config(); give it a dummy so the tests do
    not depend on real Google client credentials being present."""
    monkeypatch.setattr(
        ga,
        "_client_config",
        lambda: {
            "client_id": "test-client-id",
            "client_secret": "test-client-secret",
            "token_uri": "https://oauth2.googleapis.com/token",
        },
    )


# --------------------------------------------------------------------------- #
# Redirect URI validation
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "uri,expected",
    [
        ("http://localhost:8000/cb", True),
        ("http://127.0.0.1:8000/cb", True),
        ("http://[::1]:8000/cb", True),
        ("https://localhost/cb", True),
        ("http://localhost.evil.com/cb", False),
        ("http://localhost:8000@evil.com/cb", False),
        ("http://evil.com/cb", False),
        ("ftp://localhost/cb", False),
        ("http://user:pass@localhost:8000/cb", False),
        ("", False),
        ("not a url", False),
    ],
)
def test_redirect_uri_validation(uri, expected):
    from app.api.google_oauth import _is_local_redirect

    assert _is_local_redirect(uri) is expected


# --------------------------------------------------------------------------- #
# Missing token expiry -> epoch (NF-3)
# --------------------------------------------------------------------------- #
def test_missing_token_expiry_is_treated_as_expired(db, owner, _dummy_client_config):
    """A row with no token_expires_at must NOT be treated as valid forever."""
    from app.models.user_integration import UserIntegration

    db.add(
        UserIntegration(
            user_id=owner["id"],
            provider="google",
            status="active",
            refresh_token_encrypted=_enc("refresh"),
            access_token_encrypted=_enc("access"),
            scopes="openid",
            # token_expires_at deliberately left NULL
        )
    )
    db.commit()
    row = ga.integration_row(db, user_id=owner["id"])
    creds = ga._creds_from_row(row)
    assert creds is not None
    assert creds.expiry == datetime(1970, 1, 1)
    assert creds.expired is True
    assert creds.valid is False


def test_present_token_expiry_is_preserved(db, owner, _dummy_client_config):
    from app.models.user_integration import UserIntegration

    future = datetime.utcnow() + timedelta(hours=1)
    db.add(
        UserIntegration(
            user_id=owner["id"],
            provider="google",
            status="active",
            refresh_token_encrypted=_enc("refresh"),
            access_token_encrypted=_enc("access"),
            token_expires_at=future,
            scopes="openid",
        )
    )
    db.commit()
    row = ga.integration_row(db, user_id=owner["id"])
    creds = ga._creds_from_row(row)
    assert creds is not None
    assert creds.expiry == future
    assert creds.expired is False


# --------------------------------------------------------------------------- #
# Login-only grant is not reported as an integration connection
# --------------------------------------------------------------------------- #
def test_login_only_grant_not_reported_as_integration(db, owner):
    """A user with no google integration row is 'not_authorized', not connected."""
    result = asyncio.run(gmail_service.auth_status(db=db, user_id=owner["id"]))
    assert result["connected"] is False
    assert result["reason"] == "not_authorized"


# --------------------------------------------------------------------------- #
# Multi-user integration isolation
# --------------------------------------------------------------------------- #
def test_integration_row_isolated_per_user(db, owner, member):
    from app.models.user_integration import UserIntegration

    db.add(
        UserIntegration(
            user_id=owner["id"],
            provider="google",
            status="active",
            account_id="owner@example.com",
        )
    )
    db.commit()
    assert ga.integration_row(db, user_id=owner["id"]) is not None
    assert ga.integration_row(db, user_id=member["id"]) is None
    # member must not be able to load owner's credentials
    assert ga.credentials_for_user(db, member["id"]) is None


# --------------------------------------------------------------------------- #
# Integration OAuth-state binding
# --------------------------------------------------------------------------- #
def test_integration_state_bound_to_correct_user(db, owner, member):
    from app.api.google_oauth import _resolve_user_by_state

    ga._store_pending_state(db, owner["id"], "state-owner-xyz")
    assert _resolve_user_by_state(db, "state-owner-xyz") == owner["id"]
    # a different user's state (or an unknown state) must not resolve
    assert _resolve_user_by_state(db, "state-member-xyz") is None


# --------------------------------------------------------------------------- #
# Calendar scope enforcement
# --------------------------------------------------------------------------- #
def test_calendar_scope_enforced(db, owner, _dummy_client_config):
    """A google integration without the calendar scope is rejected up front."""
    from app.models.user_integration import UserIntegration

    db.add(
        UserIntegration(
            user_id=owner["id"],
            provider="google",
            status="active",
            refresh_token_encrypted=_enc("refresh"),
            access_token_encrypted=_enc("access"),
            token_expires_at=datetime.utcnow() + timedelta(hours=1),
            scopes="openid",  # no calendar scope
        )
    )
    db.commit()
    with pytest.raises(calendar_service.CalendarAuthError):
        asyncio.run(calendar_service._get_user_service(db, owner["id"]))


# --------------------------------------------------------------------------- #
# Gmail sync dedup is user-scoped
# --------------------------------------------------------------------------- #
def _fake_fetch(message_ids):
    async def _fetch(max_results, db, user_id, max_messages=None):
        return [
            {"message_id": mid, "from_email": "a@b.c", "subject": "hi", "is_read": True}
            for mid in message_ids
        ], []

    return _fetch


def test_sync_emails_dedup_per_user(db, owner, monkeypatch):
    from app.models.email import Email

    db.add(Email(message_id="msg-1", user_id=owner["id"], from_email="a@b.c", subject="hi"))
    db.commit()

    monkeypatch.setattr(
        gmail_service, "fetch_recent_emails", _fake_fetch(["msg-1", "msg-2"])
    )
    added_ids, failed_ids = asyncio.run(gmail_service.sync_emails(50, db=db, user_id=owner["id"]))
    # msg-1 already existed for this user -> only msg-2 is new
    assert len(added_ids) == 1
    assert failed_ids == []
    assert db.query(Email).filter(Email.user_id == owner["id"]).count() == 2


def test_sync_emails_dedup_is_user_scoped(db, owner, member, monkeypatch):
    """A message id belonging to another user must not suppress this user's copy."""
    from app.models.email import Email

    db.add(Email(message_id="msg-1", user_id=member["id"], from_email="a@b.c", subject="hi"))
    db.commit()

    monkeypatch.setattr(gmail_service, "fetch_recent_emails", _fake_fetch(["msg-1"]))
    added_ids, failed_ids = asyncio.run(gmail_service.sync_emails(50, db=db, user_id=owner["id"]))
    # owner does not have msg-1, so it is added for owner
    assert len(added_ids) == 1
    assert failed_ids == []
    assert db.query(Email).filter(Email.user_id == owner["id"]).count() == 1
