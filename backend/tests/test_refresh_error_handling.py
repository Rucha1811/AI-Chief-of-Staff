"""D.6 regression: a dead Google refresh token must fail gracefully, not 500.

Root cause: a stored Google integration can hold an access token whose
``expiry`` is unknown (``token_expires_at`` NULL). google-auth then treats the
credentials as valid, so ``ensure_refreshed_for_user`` short-circuits and the
dead refresh token is only discovered during the real API call, where
``google.auth.exceptions.RefreshError`` (``invalid_grant``) is raised from
inside ``request.execute()``.

Before the fix that exception was uncaught and surfaced as an unhandled
HTTP 500 (with a stack trace) from ``POST /api/emails/sync`` and
``POST /api/classroom/sync``. ``_call_with_retry`` now converts it into the
service's auth error, and the API maps that to 503.
"""

import asyncio

import pytest
from google.auth.exceptions import RefreshError

from tests.conftest import auth


class _BoomExec:
    """A Google request object whose execution reveals a dead refresh token."""

    def execute(self):
        raise RefreshError("invalid_grant: Bad Request")


def test_gmail_call_with_retry_maps_refresh_error():
    from app.services.gmail_service import GmailAuthError, _call_with_retry

    with pytest.raises(GmailAuthError):
        asyncio.run(_call_with_retry(lambda: _BoomExec()))


def test_classroom_call_with_retry_maps_refresh_error():
    from app.services.classroom_service import ClassroomAuthError, _call_with_retry

    with pytest.raises(ClassroomAuthError):
        asyncio.run(_call_with_retry(lambda: _BoomExec()))


class _BoomMsgs:
    def list(self, **kwargs):
        return _BoomExec()

    def get(self, **kwargs):
        return _BoomExec()


class _BoomUsers:
    def messages(self):
        return _BoomMsgs()


class _BoomCourses:
    def list(self, **kwargs):
        return _BoomExec()


class _BoomClassroomService:
    def users(self):
        return _BoomUsers()

    def courses(self):
        return _BoomCourses()


async def _fake_gmail_user_service(db=None, user_id=None):
    return _BoomClassroomService()


async def _fake_classroom_user_service(db=None, user_id=None):
    return _BoomClassroomService()


def test_emails_sync_returns_503_on_refresh_error(client, owner, monkeypatch):
    monkeypatch.setattr(
        "app.services.gmail_service._get_user_service", _fake_gmail_user_service
    )
    r = client.post("/api/emails/sync", headers=auth(owner["token"]))
    assert r.status_code == 503
    assert "authorization" in r.json()["detail"].lower()


def test_classroom_sync_returns_503_on_refresh_error(client, owner, monkeypatch, db):
    import types

    from app.services.classroom_service import CLASSROOM_COURSEWORK_SCOPE

    monkeypatch.setattr(
        "app.services.classroom_service._get_user_service",
        _fake_classroom_user_service,
    )
    # Provide creds carrying the coursework scope so the flow reaches the API
    # call (which raises RefreshError) instead of stopping at the scope check.
    creds = types.SimpleNamespace(scopes=[CLASSROOM_COURSEWORK_SCOPE])
    monkeypatch.setattr(
        "app.services.classroom_service.credentials_for_user", lambda *a, **k: creds
    )
    r = client.post("/api/classroom/sync", headers=auth(owner["token"]))
    assert r.status_code == 503