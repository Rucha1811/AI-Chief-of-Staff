"""P2.4A regression tests.

Covers:
- B2: OAuth login handoff never exposes the long-lived token in a URL;
  exchange codes are single-use and expire.
- B3: X-Forwarded-For is trusted only from configured proxies.
- B5: API error responses never contain raw exception text.
- B1: .dockerignore keeps secrets out of the backend image context.
"""

import time
from pathlib import Path

import pytest

from app.api import auth as auth_module
from app.core import config as config_module
from app.core.config import settings
from app.services import login_exchange


# ---------------------------------------------------------------------------
# B2 - OAuth exchange-code handoff
# ---------------------------------------------------------------------------


def test_exchange_code_roundtrip_returns_the_token_once():
    token = "session-token-value"
    code = login_exchange.create_exchange_code(token)
    assert code != token
    assert login_exchange.consume_exchange_code(code) == token


def test_exchange_code_is_single_use():
    code = login_exchange.create_exchange_code("session-token-value")
    assert login_exchange.consume_exchange_code(code) is not None
    assert login_exchange.consume_exchange_code(code) is None  # replay


def test_exchange_code_expires(monkeypatch):
    from types import SimpleNamespace

    code = login_exchange.create_exchange_code("session-token-value")
    real_time = time.time
    # Patch only the module's reference to the time module, not time itself.
    monkeypatch.setattr(
        login_exchange,
        "time",
        SimpleNamespace(time=lambda: real_time() + login_exchange._TTL_SECONDS + 1),
    )
    assert login_exchange.consume_exchange_code(code) is None


def test_unknown_and_empty_exchange_codes_are_rejected():
    assert login_exchange.consume_exchange_code("not-a-real-code") is None
    assert login_exchange.consume_exchange_code("") is None
    assert login_exchange.consume_exchange_code(None) is None


def test_exchange_endpoint_issues_token_and_forbids_replay(client, db, owner):
    from app.core.security import create_token

    real_token = create_token(settings.SECRET_KEY, 3600, sub=owner["id"])
    code = login_exchange.create_exchange_code(real_token)

    ok = client.post("/api/auth/exchange", json={"code": code})
    assert ok.status_code == 200
    assert ok.json()["token"] == real_token

    replay = client.post("/api/auth/exchange", json={"code": code})
    assert replay.status_code == 401

    bogus = client.post("/api/auth/exchange", json={"code": "x" * 43})
    assert bogus.status_code == 401


def _mock_google_login_flow(monkeypatch, email):
    """Make the Google login callback succeed without real Google traffic."""
    from app.services import google_auth

    class _FakeFlow:
        credentials = type("Creds", (), {"expiry": None})()

        def __init__(self, *args, **kwargs):
            pass

        def fetch_token(self, code):
            return None

    monkeypatch.setattr(google_auth, "_oauth_flow", lambda *a, **k: _FakeFlow())
    monkeypatch.setattr(
        google_auth,
        "_account_identity",
        lambda creds, id_token=None: {"email": email, "name": "Owner"},
    )
    monkeypatch.setattr(
        google_auth, "save_credentials_for_user", lambda *a, **k: None
    )
    from app.services.auth_state import AuthStateStore

    monkeypatch.setattr(
        AuthStateStore, "renew_expiry", staticmethod(lambda **k: None)
    )


def test_google_login_callback_redirects_with_code_not_token(client, db, owner, monkeypatch):
    """B2: the callback Location must carry ?code= and never the token."""
    _mock_google_login_flow(monkeypatch, owner["email"])

    state = "login_" + "a" * 32
    res = client.get(
        "/api/integrations/google/callback",
        params={"code": "google-auth-code", "state": state},
        cookies={"oauth_state": state},
        follow_redirects=False,
    )
    assert res.status_code in (302, 307)
    location = res.headers["location"]

    assert "/?code=" in location
    assert "token=" not in location
    # The Location must not contain a signed session token shape (payload.sig).
    exchange_code = location.split("/?code=", 1)[1]
    assert "." not in exchange_code  # tokens contain a dot; exchange codes don't

    # End-to-end: the code from the URL exchanges for a working session.
    traded = client.post("/api/auth/exchange", json={"code": exchange_code})
    assert traded.status_code == 200
    session_token = traded.json()["token"]
    assert session_token not in location  # the token was NEVER in the URL
    me = client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {session_token}"}
    )
    assert me.status_code == 200


def test_google_login_callback_still_rejects_bad_state(client, db, owner, monkeypatch):
    _mock_google_login_flow(monkeypatch, owner["email"])
    res = client.get(
        "/api/integrations/google/callback",
        params={"code": "x", "state": "login_" + "b" * 32},
        cookies={"oauth_state": "login_" + "c" * 32},  # mismatch
        follow_redirects=False,
    )
    assert res.headers["location"].endswith("/?error=invalid_state")


# ---------------------------------------------------------------------------
# B3 - X-Forwarded-For trust
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    auth_module._attempts.clear()
    auth_module._fail_streak.clear()
    auth_module._locked_until.clear()
    yield
    auth_module._attempts.clear()
    auth_module._fail_streak.clear()
    auth_module._locked_until.clear()


def _failed_login(client, xff=None):
    headers = {"X-Forwarded-For": xff} if xff else {}
    return client.post(
        "/api/auth/login", json={"password": "wrong-password"}, headers=headers
    )


def test_spoofed_xff_does_not_bypass_rate_limit(client, db):
    """Untrusted peer (default): every request shares one bucket despite
    rotating spoofed XFF values, so the lockout still triggers."""
    monkeypatch_trusted(proxies="")
    statuses = [
        _failed_login(client, xff=f"10.0.0.{i}").status_code for i in range(8)
    ]
    assert 429 in statuses  # locked out even though each XFF was "unique"


def test_trusted_proxy_xff_is_honored(client, db):
    """When the peer IS a configured proxy, its XFF sets the bucket: two
    different forwarded IPs get independent counters."""
    # TestClient's direct peer host is "testclient".
    monkeypatch_trusted(proxies="testclient")
    for _ in range(auth_module._MAX_ATTEMPTS - 1):
        assert _failed_login(client, xff="203.0.113.1").status_code == 401
    # A different forwarded client still gets its own allowance.
    assert _failed_login(client, xff="203.0.113.2").status_code == 401


def test_spoofed_xff_lockout_applies_to_the_real_bucket(client, db):
    """After lockout, further spoofed-XFF attempts stay locked out (same
    real bucket), and the message is the lockout response."""
    monkeypatch_trusted(proxies="")
    for _ in range(auth_module._MAX_ATTEMPTS):
        _failed_login(client, xff="198.51.100.7")
    r = _failed_login(client, xff="198.51.100.99")
    assert r.status_code == 429
    assert "Too many" in r.json()["detail"]


def test_normal_login_still_works_below_the_limit(client, db, owner):
    monkeypatch_trusted(proxies="")
    r = client.post("/api/auth/login", json={"password": "aichief"})
    assert r.status_code == 200


def monkeypatch_trusted(proxies: str):
    """Settings instance is shared; adjust the parsed property's source."""
    settings.TRUSTED_PROXY_IPS = proxies


@pytest.fixture(autouse=True)
def _restore_trusted_proxies():
    original = settings.TRUSTED_PROXY_IPS
    yield
    settings.TRUSTED_PROXY_IPS = original


# ---------------------------------------------------------------------------
# B5 - no raw exception text in client responses
# ---------------------------------------------------------------------------


def test_classroom_error_is_generic(client, db, owner, monkeypatch):
    from app.api import classroom as classroom_api
    from app.services import classroom_service

    def _boom(**kwargs):
        raise classroom_service.ClassroomUnavailableError(
            "psycopg2.connect(password=hunter2) failed at /srv/app/x.py"
        )

    monkeypatch.setattr(classroom_service, "list_assignments", _boom)
    r = client.get(
        "/api/classroom/assignments",
        headers={"Authorization": f"Bearer {owner['token']}"},
    )
    assert r.status_code == 502
    detail = r.json()["detail"]
    assert "hunter2" not in detail and "/srv" not in detail
    assert detail == "Classroom is temporarily unavailable. Please try again later."


def test_teams_error_is_generic(client, db, owner, monkeypatch):
    from app.services import teams_service

    async def _boom(**kwargs):
        raise teams_service.TeamsAuthError(
            "AADSTS7000215: Invalid client secret provided: abc123-secret"
        )

    monkeypatch.setattr(teams_service, "sync_events", _boom)
    r = client.post(
        "/api/teams/sync", headers={"Authorization": f"Bearer {owner['token']}"}
    )
    assert r.status_code == 503
    detail = r.json()["detail"]
    assert "abc123-secret" not in detail and "AADSTS" not in detail


def test_events_calendar_error_is_generic(client, db, owner, monkeypatch):
    # The endpoint imports push_events_to_calendar lazily from the service
    # module, so patch it at the source.
    from app.services import calendar_service

    async def _boom(db, events, user_id):
        raise RuntimeError("refresh_token=ya29.leaked-value file=/home/u/.secrets")

    monkeypatch.setattr(calendar_service, "push_events_to_calendar", _boom)
    r = client.post(
        "/api/events/sync-to-google",
        params={"date_from": "2026-01-01"},
        headers={"Authorization": f"Bearer {owner['token']}"},
    )
    assert r.status_code == 502
    detail = r.json()["detail"]
    assert "ya29.leaked-value" not in detail and "/home/u" not in detail


def test_google_authorize_error_is_generic(client, db, owner, monkeypatch):
    from app.services import google_auth

    def _boom(db, user_id, redirect_uri=None):
        raise google_auth.GoogleAuthError(
            "Could not read Google credentials.json: client_secret=GOCSPX-leaked"
        )

    monkeypatch.setattr(google_auth, "build_authorization_url", _boom)
    r = client.get(
        "/api/integrations/google/authorize",
        headers={"Authorization": f"Bearer {owner['token']}"},
    )
    assert r.status_code == 503
    detail = r.json()["detail"]
    assert "GOCSPX-leaked" not in detail and "credentials.json" not in detail


def test_google_callback_error_reason_is_generic(client, db, owner, monkeypatch):
    """The connect-flow callback must not put provider error text in the URL."""
    from app.api import google_oauth as google_oauth_api
    from app.services import google_auth
    from app.models.user_integration import UserIntegration

    row = UserIntegration(
        user_id=owner["id"],
        provider="google",
        account_id=owner["email"],
        status="pending",
        meta={"oauth_state": "state-123"},
    )
    db.add(row)
    db.commit()

    def _boom(db, user_id, code, state):
        raise google_auth.GoogleAuthError("invalid_grant: secret trace 12345")

    monkeypatch.setattr(google_auth, "exchange_authorization_code", _boom)
    res = client.get(
        "/api/integrations/google/callback",
        params={"code": "abc", "state": "state-123"},
        follow_redirects=False,
    )
    location = res.headers["location"]
    assert "secret trace 12345" not in location
    assert "reason=exchange_failed" in location


# ---------------------------------------------------------------------------
# B1 - .dockerignore keeps secrets out of the image context
# ---------------------------------------------------------------------------


def _repo_root() -> Path | None:
    for candidate in [Path(config_module.__file__).resolve()] + list(
        Path(config_module.__file__).resolve().parents
    ):
        if (candidate / "frontend").is_dir():
            return candidate
    return None


_REPO_ROOT = _repo_root()
requires_repo = pytest.mark.skipif(
    _REPO_ROOT is None, reason="repository root not present (backend-only image)"
)


@requires_repo
def test_backend_dockerignore_covers_every_secret_path():
    patterns = (_REPO_ROOT / "backend" / ".dockerignore").read_text()
    for entry in (
        ".env",
        ".secret_key",
        ".ingest_key",
        "credentials/",
        "vapid/",
        "venv/",
        "__pycache__/",
        "*.db",
        "tests/",
        ".git/",
    ):
        assert entry in patterns, f"backend/.dockerignore missing {entry}"


@requires_repo
def test_frontend_dockerignore_excludes_local_env_and_artifacts():
    patterns = (_REPO_ROOT / "frontend" / ".dockerignore").read_text()
    for entry in ("node_modules/", ".next/", ".env"):
        assert entry in patterns, f"frontend/.dockerignore missing {entry}"
