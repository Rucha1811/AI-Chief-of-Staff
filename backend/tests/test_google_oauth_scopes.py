"""Scope-contract regressions for the Google OAuth connect flow.

Google Workspace may return a granted-scope set that omits optional integration
permissions such as Classroom coursework. The callback must persist the scopes
Google actually granted and allow each integration to report missing access.

The coursework scope is genuinely required: ``courses.courseWork.list`` (used by
``classroom_service.list_assignments`` / ``list_submissions`` to read assignment
titles and due dates) is authorized ONLY by a coursework scope. The
``classroom.student-submissions.me.readonly`` scope does NOT cover it (verified
against the Classroom v1 discovery document). These tests lock that contract and
keep scope validation strict.
"""

import json
import os

import pytest

from app.services import google_auth as ga
from app.services.google_auth import SCOPES

COURSEWORK_SCOPE = "https://www.googleapis.com/auth/classroom.coursework.me.readonly"

REQUIRED_SCOPES = {
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/classroom.courses.readonly",
    "https://www.googleapis.com/auth/classroom.student-submissions.me.readonly",
    "https://www.googleapis.com/auth/calendar.events",
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
}


class _FakeCreds:
    def __init__(self, scopes):
        from datetime import datetime, timedelta

        self.scopes = list(scopes)
        self.token = "fake-access-token"
        self.refresh_token = "fake-refresh-token"
        self.expiry = datetime.utcnow() + timedelta(hours=1)


class _FakeFlow:
    """Minimal stand-in for ``google_auth_oauthlib.flow.Flow``."""

    def __init__(self, *, granted_scopes=None, fetch_error=None):
        self._granted = list(granted_scopes or [])
        self._fetch_error = fetch_error
        self.credentials = None
        self.token_response = {}

    def fetch_token(self, code=None):
        if self._fetch_error is not None:
            raise self._fetch_error
        self.credentials = _FakeCreds(self._granted)
        self.token_response = {}


# --------------------------------------------------------------------------- #
# Canonical request contract
# --------------------------------------------------------------------------- #
def test_canonical_scopes_include_every_required_permission():
    assert REQUIRED_SCOPES <= set(SCOPES)


def test_coursework_scope_is_requested_for_assignments():
    """Assignment listing requires the coursework permission."""
    assert COURSEWORK_SCOPE in SCOPES


def test_oauth_flow_requests_canonical_scopes(monkeypatch):
    captured = {}

    class _Flow:
        @classmethod
        def from_client_config(cls, config, scopes=None, **kwargs):
            captured["scopes"] = list(scopes) if scopes else None
            captured["redirect_uri"] = kwargs.get("redirect_uri")
            return cls()

    import google_auth_oauthlib.flow as flow_mod

    monkeypatch.setattr(flow_mod, "Flow", _Flow)
    monkeypatch.setattr(
        ga,
        "_client_config",
        lambda: {
            "client_id": "id",
            "client_secret": "secret",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        },
    )

    ga._oauth_flow(state="s", redirect_uri="http://localhost:8000/cb")
    assert captured["scopes"] == list(SCOPES)
    assert captured["redirect_uri"] == "http://localhost:8000/cb"


# --------------------------------------------------------------------------- #
# Token exchange accepts Google's reduced grant and feature-gates missing scopes
# --------------------------------------------------------------------------- #
def test_exchange_accepts_full_grant_and_persists(db, owner, monkeypatch):
    ga._store_pending_state(db, owner["id"], "state-full")
    flow = _FakeFlow(granted_scopes=list(SCOPES))
    monkeypatch.setattr(ga, "_oauth_flow", lambda **kwargs: flow)
    monkeypatch.setattr(
        ga,
        "_account_identity",
        lambda creds, id_token=None: {
            "email": "student@charusat.edu.in",
            "name": "Student",
            "google_sub": "sub-1",
        },
    )

    result = ga.exchange_authorization_code(
        db, user_id=owner["id"], code="code-1", state="state-full"
    )

    assert result["email"] == "student@charusat.edu.in"
    row = ga.integration_row(db, user_id=owner["id"])
    assert row.status == "active"
    assert row.account_id == "student@charusat.edu.in"
    assert set(row.scopes.split()) == set(SCOPES)


def test_exchange_accepts_reduced_grant_and_persists_granted_scopes(db, owner, monkeypatch):
    ga._store_pending_state(db, owner["id"], "state-reduced")
    granted = [scope for scope in SCOPES if scope != COURSEWORK_SCOPE]
    flow = _FakeFlow(granted_scopes=granted)
    monkeypatch.setattr(ga, "_oauth_flow", lambda **kwargs: flow)
    monkeypatch.setattr(
        ga,
        "_account_identity",
        lambda creds, id_token=None: {
            "email": "reduced@example.com",
            "name": "X",
            "google_sub": "1",
        },
    )

    result = ga.exchange_authorization_code(
        db, user_id=owner["id"], code="code-2", state="state-reduced"
    )

    row = ga.integration_row(db, user_id=owner["id"])
    assert result["email"] == "reduced@example.com"
    assert row.status == "active"
    assert set(row.scopes.split()) == set(granted)


def test_scope_relaxation_is_scoped_to_token_exchange(monkeypatch):
    monkeypatch.delenv("OAUTHLIB_RELAX_TOKEN_SCOPE", raising=False)
    with ga._accept_google_granted_scopes():
        assert os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] == "1"
    assert "OAUTHLIB_RELAX_TOKEN_SCOPE" not in os.environ


# --------------------------------------------------------------------------- #
# Login flow: identity-only scopes (fixes ``?error=oauth_failed``)
# --------------------------------------------------------------------------- #
def test_login_flow_requests_identity_scopes_only(client, db, owner, monkeypatch):
    """Google login must request only identity scopes.

    Login authenticates the user; it does not need Gmail/Classroom/Calendar.
    Requesting the full SCOPES makes sign-in fail whenever Google declines
    any integration scope (oauthlib raises on the scope reduction).
    """
    from app.services.auth_state import AuthStateStore

    captured = {}

    class _Flow:
        credentials = type("Creds", (), {"expiry": None})()

        def __init__(self, *args, **kwargs):
            captured["scopes"] = kwargs.get("scopes")

        def fetch_token(self, code):
            return None

    monkeypatch.setattr(ga, "_oauth_flow", lambda *a, **k: _Flow(*a, **k))
    monkeypatch.setattr(
        ga,
        "_account_identity",
        lambda creds, id_token=None: {"email": owner["email"], "name": "Owner"},
    )
    monkeypatch.setattr(AuthStateStore, "renew_expiry", staticmethod(lambda **k: None))

    state = "login_" + "a" * 32
    res = client.get(
        "/api/integrations/google/callback",
        params={"code": "google-auth-code", "state": state},
        cookies={"oauth_state": state},
        follow_redirects=False,
    )
    assert res.status_code in (302, 307)
    assert "/?code=" in res.headers["location"]
    assert captured["scopes"] == ga.IDENTITY_SCOPES
    assert COURSEWORK_SCOPE not in captured["scopes"]


def test_login_does_not_overwrite_integration_credentials(client, db, owner, monkeypatch):
    """Login must not persist identity-only creds over existing integration grants."""
    from app.services.auth_state import AuthStateStore

    ga.save_credentials_for_user(
        db, owner["id"], _FakeCreds(SCOPES),
        account_id=owner["email"], metadata={"email": owner["email"]},
    )
    db.commit()
    before = ga.integration_row(db, user_id=owner["id"])
    assert set(before.scopes.split()) == set(SCOPES)

    class _Flow:
        credentials = type("Creds", (), {"expiry": None})()

        def __init__(self, *a, **k):
            pass

        def fetch_token(self, code):
            return None

    monkeypatch.setattr(ga, "_oauth_flow", lambda *a, **k: _Flow())
    monkeypatch.setattr(
        ga,
        "_account_identity",
        lambda creds, id_token=None: {"email": owner["email"], "name": "Owner"},
    )
    monkeypatch.setattr(AuthStateStore, "renew_expiry", staticmethod(lambda **k: None))

    state = "login_" + "b" * 32
    res = client.get(
        "/api/integrations/google/callback",
        params={"code": "google-auth-code", "state": state},
        cookies={"oauth_state": state},
        follow_redirects=False,
    )
    assert res.status_code in (302, 307)
    after = ga.integration_row(db, user_id=owner["id"])
    assert set(after.scopes.split()) == set(SCOPES)  # unchanged
