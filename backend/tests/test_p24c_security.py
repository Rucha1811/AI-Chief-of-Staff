"""P2.4C regression tests (immediate security fixes).

Covers:
- P24B-06: first-user owner bootstrap requires operator authorization in
  production, is single-use, cannot be reactivated, and is concurrency-safe;
  Google login can no longer create the first (owner) user.
- P24B-08: credential-derived files and directories are gitignored, while
  tracked templates and source stay trackable.
- P24B-07: backup artifacts are created owner-only; the obsolete legacy
  token.pickle copy is gone.
- P24B-04: every /api/integrations/ route has an explicit credential policy and
  unlisted routes fail closed; the public Google callback still works and still
  validates its state.
- P24B-16: the OAuth state cookie is HttpOnly + SameSite=Lax + Secure
  (production always; elsewhere only via explicit opt-out for plain HTTP).

Tests that need the repository tree (gitignore, backup script) skip in the
backend-only Docker image, exactly like the existing repo-tree tests in
test_config_hardening.py; they run on a host checkout.
"""

import os
import stat
import subprocess
import threading
from pathlib import Path

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from app.api import auth as auth_module
from app.core import access_policy, config as config_module
from app.core.config import normalize_request_path, settings
from app.core.database import SessionLocal
from app.main import app
from app.models.app_config import AppConfig
from app.models.user import User
from app.services import bootstrap

# A high-entropy-shaped value. Never a real deployment secret.
BOOTSTRAP_TOKEN = "test-bootstrap-token-4f8a1c9d2e7b6053"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _prod(monkeypatch, bootstrap_token=BOOTSTRAP_TOKEN):
    """Run the request path as production, with/without a bootstrap token."""
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    if bootstrap_token is None:
        monkeypatch.delenv("BOOTSTRAP_TOKEN", raising=False)
    else:
        monkeypatch.setenv("BOOTSTRAP_TOKEN", bootstrap_token)


def _register(client, email, password="ownerpassword123", token=None, bootstrap_token=None):
    """POST /api/auth/register.

    ``token`` is a bearer session token (the conftest convention); ``bootstrap_token``
    is the one-time owner-bootstrap header. They are different credentials and
    must never be conflated.
    """
    hdrs = {}
    if token:
        hdrs["Authorization"] = f"Bearer {token}"
    if bootstrap_token:
        hdrs["X-Bootstrap-Token"] = bootstrap_token
    return client.post(
        "/api/auth/register", json={"email": email, "password": password}, headers=hdrs
    )


def _mock_google_flow(monkeypatch, email):
    """Google login callback succeeds, without any real Google traffic."""
    from app.services import google_auth
    from app.services.auth_state import AuthStateStore

    class _FakeFlow:
        credentials = type("Creds", (), {"expiry": None})()

        def __init__(self, *a, **k):
            pass

        def fetch_token(self, code):
            return None

    monkeypatch.setattr(google_auth, "_oauth_flow", lambda *a, **k: _FakeFlow())
    monkeypatch.setattr(
        google_auth,
        "_account_identity",
        lambda creds, id_token=None: {"email": email, "name": "Google User"},
    )
    monkeypatch.setattr(google_auth, "save_credentials_for_user", lambda *a, **k: None)
    monkeypatch.setattr(AuthStateStore, "renew_expiry", staticmethod(lambda **k: None))


# ---------------------------------------------------------------------------
# P24B-06 - first-owner bootstrap
# ---------------------------------------------------------------------------


def test_production_bootstrap_denied_without_token(client, db, monkeypatch, caplog):
    """Empty production DB, no BOOTSTRAP_TOKEN: nobody may claim ownership."""
    _prod(monkeypatch, bootstrap_token=None)

    with caplog.at_level("WARNING"):
        r = _register(client, "first@aichief.test")

    assert r.status_code == 403
    assert db.query(User).count() == 0
    assert bootstrap.bootstrap_state(db) == "available"
    # The refusal must be generic and must not leak the configured state.
    assert BOOTSTRAP_TOKEN not in r.text


def test_production_bootstrap_denied_with_invalid_token(client, db, monkeypatch):
    _prod(monkeypatch)

    r = _register(client, "first@aichief.test", bootstrap_token="wrong-token-value")

    assert r.status_code == 403
    assert db.query(User).count() == 0
    assert bootstrap.bootstrap_state(db) == "available"


def test_production_bootstrap_denied_when_the_variable_is_empty(client, db, monkeypatch):
    """The compose default (`BOOTSTRAP_TOKEN: ${BOOTSTRAP_TOKEN:-}`) is an empty
    string, not an unset variable - it must fail closed too, not slip through."""
    _prod(monkeypatch)
    monkeypatch.setenv("BOOTSTRAP_TOKEN", "")

    r = _register(client, "first@aichief.test", bootstrap_token=BOOTSTRAP_TOKEN)

    assert r.status_code == 403
    assert db.query(User).count() == 0
    assert bootstrap.bootstrap_state(db) == "available"


def test_production_bootstrap_with_valid_token_creates_exactly_one_owner(client, db, monkeypatch):
    _prod(monkeypatch)

    r = _register(client, "first@aichief.test", bootstrap_token=BOOTSTRAP_TOKEN)

    assert r.status_code == 201, r.text
    body = r.json()
    assert body["roles"] == ["owner"]
    assert "manage_users" in body["permissions"]
    assert db.query(User).count() == 1
    # The one-shot authorization is durably consumed.
    assert bootstrap.bootstrap_state(db) == "consumed"
    assert db.query(AppConfig).filter(
        AppConfig.key == bootstrap.BOOTSTRAP_MARKER_KEY
    ).count() == 1


def test_bootstrap_token_is_not_replayable_for_a_second_owner(client, db, monkeypatch):
    _prod(monkeypatch)
    first = _register(client, "first@aichief.test", bootstrap_token=BOOTSTRAP_TOKEN)
    assert first.status_code == 201

    # Same token, second account: the bootstrap header confers nothing once the
    # one-shot is spent - ordinary registration rules apply, and this request
    # carries no session.
    replay = _register(client, "second@aichief.test", bootstrap_token=BOOTSTRAP_TOKEN)
    assert replay.status_code == 401
    assert db.query(User).filter(User.email == "second@aichief.test").count() == 0

    # And even with a real session it cannot mint a second owner.
    owner_token = client.post(
        "/api/auth/login",
        json={"email": "first@aichief.test", "password": "ownerpassword123"},
    ).json()["token"]
    with_session = _register(
        client, "second@aichief.test", token=owner_token, bootstrap_token=BOOTSTRAP_TOKEN
    )
    assert with_session.status_code == 201
    assert with_session.json()["roles"] == ["member"]
    assert db.query(User).count() == 2


def test_bootstrap_cannot_be_reactivated_after_the_owner_row_disappears(client, db, owner, monkeypatch):
    """An empty users table alone must not re-open ownership."""
    _prod(monkeypatch)

    db.query(User).delete()
    db.commit()
    assert db.query(User).count() == 0
    assert bootstrap.bootstrap_state(db) == "consumed"

    r = _register(client, "attacker@aichief.test", bootstrap_token=BOOTSTRAP_TOKEN)

    assert r.status_code == 403
    assert db.query(User).count() == 0


def test_failed_registration_does_not_consume_the_one_shot_token(client, db, monkeypatch):
    """A client-side error must not burn the operator's single use."""
    _prod(monkeypatch)

    invalid = _register(client, "not-an-email", password="short", bootstrap_token=BOOTSTRAP_TOKEN)
    assert invalid.status_code == 422

    ok = _register(client, "first@aichief.test", bootstrap_token=BOOTSTRAP_TOKEN)
    assert ok.status_code == 201
    assert ok.json()["roles"] == ["owner"]


def test_google_login_cannot_promote_an_arbitrary_first_account(client, db, monkeypatch):
    """The OAuth path must not be a way around the bootstrap token."""
    _prod(monkeypatch)
    _mock_google_flow(monkeypatch, "attacker@gmail.com")

    res = client.get(
        "/api/integrations/google/callback",
        params={"code": "google-code", "state": "login_" + "d" * 32},
        cookies={"oauth_state": "login_" + "d" * 32},
        follow_redirects=False,
    )

    assert res.status_code in (302, 307)
    assert "error=access_denied" in res.headers["location"]
    assert "reason=not_invited" in res.headers["location"]
    assert db.query(User).count() == 0


def test_google_login_cannot_promote_a_second_account_even_with_an_owner(client, db, owner, monkeypatch):
    _mock_google_flow(monkeypatch, "stranger@gmail.com")

    res = client.get(
        "/api/integrations/google/callback",
        params={"code": "google-code", "state": "login_" + "e" * 32},
        cookies={"oauth_state": "login_" + "e" * 32},
        follow_redirects=False,
    )

    assert "error=access_denied" in res.headers["location"]
    assert db.query(User).count() == 1  # only the owner


def test_google_login_still_admits_an_existing_member(client, db, owner, member, monkeypatch):
    """Guards the fix from over-correcting: provisioned accounts still sign in.

    P24B-06 removed *provisioning*, not *authentication*. An account that was
    created through the operator/invite path must still be able to sign in with
    Google, so this locks in that behaviour next to the denial tests.
    """
    _mock_google_flow(monkeypatch, member["email"])

    res = client.get(
        "/api/integrations/google/callback",
        params={"code": "google-code", "state": "login_" + "f" * 32},
        cookies={"oauth_state": "login_" + "f" * 32},
        follow_redirects=False,
    )

    assert res.status_code in (302, 307)
    location = res.headers["location"]
    assert "error=" not in location
    assert "code=" in location  # a single-use exchange code, not a raw token
    assert db.query(User).count() == 2  # owner + member, no new account


def test_concurrent_bootstrap_attempts_create_at_most_one_owner(monkeypatch):
    """Two racing requests, two real transactions, one winner (P24B-06)."""
    _prod(monkeypatch)

    ready = threading.Barrier(2, timeout=60)
    go = threading.Event()
    statuses: dict[str, int] = {}

    def attempt(name: str) -> None:
        # Own TestClient and NO get_db override: each request must open its own
        # session/transaction, which is what makes the race real.
        client = TestClient(app)
        try:
            ready.wait()
        except threading.BrokenBarrierError:  # pragma: no cover
            pass
        go.wait(timeout=60)
        r = _register(client, f"{name}@aichief.test", bootstrap_token=BOOTSTRAP_TOKEN)
        statuses[name] = r.status_code

    threads = [threading.Thread(target=attempt, args=(n,)) for n in ("racer1", "racer2")]
    for t in threads:
        t.start()
    try:
        ready.wait()
    except threading.BrokenBarrierError:  # pragma: no cover
        pass
    go.set()
    for t in threads:
        t.join(timeout=120)

    assert len(statuses) == 2, "both attempts must complete"
    assert sorted(statuses.values()) == [201, 403], statuses

    from app.models.user import Role, UserRole

    session = SessionLocal()
    try:
        owner_roles = (
            session.query(UserRole)
            .join(Role)
            .filter(Role.code == "owner")
            .count()
        )
        total_users = session.query(User).count()
        markers = (
            session.query(AppConfig)
            .filter(AppConfig.key == bootstrap.BOOTSTRAP_MARKER_KEY)
            .count()
        )
    finally:
        session.rollback()
        session.close()

    assert total_users == 1
    assert owner_roles == 1
    assert markers == 1


def test_development_bootstrap_still_needs_no_token(client, db):
    """Local development keeps its zero-config convenience (this is what the
    rest of the suite relies on); production is the enforced path."""
    assert settings.ENVIRONMENT != "production"

    r = _register(client, "dev-first@aichief.test")

    assert r.status_code == 201
    assert r.json()["roles"] == ["owner"]


def test_ordinary_registration_still_requires_manage_users(client, db, owner, member):
    """After bootstrap, invitation/permission rules are unchanged."""
    denied = _register(client, "third@aichief.test", token=member["token"])
    assert denied.status_code == 403

    allowed = _register(client, "third@aichief.test", token=owner["token"])
    assert allowed.status_code == 201
    assert allowed.json()["roles"] == ["member"]
    assert "manage_users" not in allowed.json()["permissions"]


def test_bootstrap_service_refuses_unconfigured_production(client, db, monkeypatch):
    """Unit-level: the policy itself, independent of the endpoint."""
    decision = bootstrap.evaluate(
        db, presented_token="anything", configured_token=None, environment="production"
    )
    assert decision.authorized is False
    assert decision.reason == bootstrap.REFUSED_NOT_CONFIGURED

    good = bootstrap.evaluate(
        db,
        presented_token=BOOTSTRAP_TOKEN,
        configured_token=BOOTSTRAP_TOKEN,
        environment="production",
    )
    assert good.authorized is True

    # Wrong length, wrong content: both rejected.
    assert (
        bootstrap.evaluate(
            db,
            presented_token="short",
            configured_token=BOOTSTRAP_TOKEN,
            environment="production",
        ).authorized
        is False
    )


def test_bootstrap_claim_is_winnable_only_once():
    """The durable marker is a one-winner mutex across sessions."""
    first = SessionLocal()
    try:
        assert bootstrap.claim(first, owner_user_id=1) is True
        first.commit()
    finally:
        first.rollback()
        first.close()

    second = SessionLocal()
    try:
        assert bootstrap.claim(second, owner_user_id=2) is False
    finally:
        second.rollback()
        second.close()


def test_bootstrap_token_is_never_echoed_in_a_response(client, db, monkeypatch):
    _prod(monkeypatch)

    refused = _register(client, "first@aichief.test", bootstrap_token="nope-not-the-token")
    accepted = _register(client, "first@aichief.test", bootstrap_token=BOOTSTRAP_TOKEN)

    assert BOOTSTRAP_TOKEN not in refused.text
    assert BOOTSTRAP_TOKEN not in accepted.text


# ---------------------------------------------------------------------------
# P24B-04 - integration route authorization
# ---------------------------------------------------------------------------


def test_public_paths_no_longer_exempt_the_integrations_prefix():
    """The prefix exemption is gone; nothing integration-related is blanket-public."""
    from app import main as main_module

    assert not any(p.startswith("/api/integrations") for p in main_module._PUBLIC_PATHS)
    assert access_policy.is_integration_path("/api/integrations/config")


def test_every_integration_route_has_an_explicit_policy():
    """Route-walking invariant: a new integration route must be classified."""
    missing = access_policy.unclassified_integration_routes(app)
    assert missing == [], f"unclassified integration routes: {missing}"

    # And the policy must cover every registered route exactly once.
    registered = set()
    from fastapi.routing import APIRoute

    for route in app.routes:
        if isinstance(route, APIRoute) and access_policy.is_integration_path(route.path):
            for method in route.methods or ():
                registered.add((method.upper(), route.path))
    assert registered <= set(access_policy.POLICY)


def test_integration_policy_credentials_are_the_expected_ones():
    api_key_routes = {
        "/api/integrations/config",
        "/api/integrations/emails/ingest",
        "/api/integrations/retention/purge",
        "/api/integrations/agent/accounts",
        "/api/integrations/agent/sync",
    }
    for (method, template), credential in access_policy.POLICY.items():
        if template in api_key_routes:
            assert credential == access_policy.CREDENTIAL_API_KEY, (method, template)
    assert access_policy.POLICY[("GET", "/api/integrations/google/callback")] == "public"
    for template in (
        "/api/integrations/google/authorize",
        "/api/integrations/google/disconnect",
        "/api/integrations/user",
        "/api/integrations/user/{provider}",
    ):
        for (method, path), credential in access_policy.POLICY.items():
            if path == template:
                assert credential == access_policy.CREDENTIAL_USER, (method, path)

    # Unknown route templates resolve to the strict default, not to public.
    assert (
        access_policy.integration_credential("GET", "/api/integrations/not-registered")
        == access_policy.DEFAULT_INTEGRATION_CREDENTIAL
    )
    assert access_policy.DEFAULT_INTEGRATION_CREDENTIAL == access_policy.CREDENTIAL_BOTH


def test_newly_registered_integration_route_without_policy_fails_closed(client, db, owner):
    """A route added under the prefix inherits nothing."""
    router = APIRouter(prefix="/api/integrations")

    @router.get("/p24c-probe")
    def _probe():
        return {"ok": True}

    app.include_router(router)
    path = "/api/integrations/p24c-probe"
    try:
        assert access_policy.integration_credential("GET", path) == access_policy.CREDENTIAL_BOTH

        anonymous = client.get(path)
        assert anonymous.status_code == 401

        # A user session alone is not enough either.
        only_user = client.get(path, headers={"Authorization": f"Bearer {owner['token']}"})
        assert only_user.status_code == 401

        # An API key alone is not enough either.
        only_key = client.get(path, headers={"X-API-Key": settings.INGEST_API_KEY})
        assert only_key.status_code == 401

        # Both credentials reach the handler - proving the 401s above came from
        # the policy, not from a missing route.
        both = client.get(
            path,
            headers={
                "Authorization": f"Bearer {owner['token']}",
                "X-API-Key": settings.INGEST_API_KEY,
            },
        )
        assert both.status_code == 200
        assert both.json() == {"ok": True}
    finally:
        app.router.routes = [
            r for r in app.routes if getattr(r, "path", None) != path
        ]

    assert access_policy.unclassified_integration_routes(app) == []


def test_google_oauth_callback_stays_public_and_validates_state(client, db, owner, monkeypatch):
    _mock_google_flow(monkeypatch, owner["email"])

    bad_state = client.get(
        "/api/integrations/google/callback",
        params={"code": "c", "state": "login_" + "1" * 32},
        cookies={"oauth_state": "login_" + "2" * 32},
        follow_redirects=False,
    )
    assert bad_state.status_code in (302, 307)
    assert bad_state.headers["location"].endswith("/?error=invalid_state")
    assert "code=" not in bad_state.headers["location"]

    good_state = client.get(
        "/api/integrations/google/callback",
        params={"code": "c", "state": "login_" + "1" * 32},
        cookies={"oauth_state": "login_" + "1" * 32},
        follow_redirects=False,
    )
    assert "/?code=" in good_state.headers["location"]


def test_google_authorize_and_disconnect_require_a_session(client, db, owner):
    authorize = client.get("/api/integrations/google/authorize")
    assert authorize.status_code == 401

    # With a session it reaches the handler (503 = Google not configured here).
    with_session = client.get(
        "/api/integrations/google/authorize", headers={"Authorization": f"Bearer {owner['token']}"}
    )
    assert with_session.status_code != 401

    disconnect = client.post("/api/integrations/google/disconnect")
    assert disconnect.status_code == 401
    ok_disconnect = client.post(
        "/api/integrations/google/disconnect",
        headers={"Authorization": f"Bearer {owner['token']}"},
    )
    assert ok_disconnect.status_code == 200


def test_user_integration_routes_require_a_session_and_permission(client, db, owner, member):
    anon = client.get("/api/integrations/user")
    assert anon.status_code == 401

    listed = client.get("/api/integrations/user", headers={"Authorization": f"Bearer {member['token']}"})
    assert listed.status_code == 200

    # manage_integrations is an owner capability, not a member one.
    denied = client.post(
        "/api/integrations/user",
        json={"provider": "gmail"},
        headers={"Authorization": f"Bearer {member['token']}"},
    )
    assert denied.status_code == 403

    allowed = client.post(
        "/api/integrations/user",
        json={"provider": "gmail"},
        headers={"Authorization": f"Bearer {owner['token']}"},
    )
    assert allowed.status_code in (200, 201)


def test_api_key_routes_require_the_ingest_key_not_a_session(client, db, owner):
    """An API-key endpoint must never become reachable with a bearer token."""
    headers = {"Authorization": f"Bearer {owner['token']}"}

    assert client.get("/api/integrations/config", headers=headers).status_code == 401
    assert client.get("/api/integrations/agent/accounts", headers=headers).status_code == 401
    assert (
        client.post(
            "/api/integrations/agent/sync", json={}, headers=headers
        ).status_code
        == 401
    )

    # Missing and wrong keys are rejected...
    assert client.get("/api/integrations/config").status_code == 401
    wrong = client.get("/api/integrations/config", headers={"X-API-Key": "not-the-key"})
    assert wrong.status_code == 401

    # ...and the real key still works without any session (n8n behaviour).
    ok = client.get("/api/integrations/config", headers={"X-API-Key": settings.INGEST_API_KEY})
    assert ok.status_code == 200


def test_trailing_slash_does_not_bypass_the_integration_policy(client, db):
    """/api/integrations/config/ must not slip past the api_key requirement."""
    assert normalize_request_path("/api/integrations/config/") == "/api/integrations/config"
    assert client.get("/api/integrations/config/").status_code == 401
    assert (
        client.get("/api/integrations/config/", headers={"X-API-Key": settings.INGEST_API_KEY}).status_code
        in (200, 307)
    )


# ---------------------------------------------------------------------------
# P24B-16 - OAuth state cookie attributes
# ---------------------------------------------------------------------------


def _mock_authorize_flow(monkeypatch):
    from app.services import google_auth

    class _FakeFlow:
        def __init__(self, *a, **k):
            pass

        def authorization_url(self, **kwargs):
            return "https://accounts.google.com/o/oauth2/v2/auth?stub=1", "state-stub"

    monkeypatch.setattr(google_auth, "_oauth_flow", lambda *a, **k: _FakeFlow())


def _set_cookie_headers(response):
    headers = response.headers
    getter = getattr(headers, "get_list", None)
    if getter is not None:
        return getter("set-cookie")
    return [v for _k, v in headers.multi_items() if _k.lower() == "set-cookie"]


def _google_login(client, owner):
    """Start the login OAuth flow.

    /api/auth/google/login is in the middleware's public list (Phase D2), so
    it is reachable anonymously - that is exactly how the logged-out AuthGate
    reaches it in a browser navigation, which previously got a 401. The bearer
    token is still sent here because it must keep working for an already
    signed-in caller too; the route ignores it either way.
    """
    return client.get(
        "/api/auth/google/login",
        headers={"Authorization": f"Bearer {owner['token']}"},
        follow_redirects=False,
    )


def test_oauth_state_cookie_in_production_is_secure_httponly_lax(client, db, owner, monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "OAUTH_COOKIE_SECURE", False)  # must be ignored
    _mock_authorize_flow(monkeypatch)

    res = _google_login(client, owner)
    cookie = next(c for c in _set_cookie_headers(res) if c.startswith("oauth_state="))

    lowered = cookie.lower()
    assert "secure" in lowered, cookie
    assert "httponly" in lowered, cookie
    assert "samesite=lax" in lowered, cookie
    assert "max-age=3600" in lowered, cookie


def test_oauth_state_cookie_plain_http_requires_explicit_optin(client, db, owner, monkeypatch, caplog):
    """Non-production must not be assumed to be plain HTTP, nor assumed safe."""
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    _mock_authorize_flow(monkeypatch)

    # Unset: the secure default applies, with a warning naming the knob.
    monkeypatch.setattr(settings, "OAUTH_COOKIE_SECURE", None)
    with caplog.at_level("WARNING"):
        default = _google_login(client, owner)
    default_cookie = next(c for c in _set_cookie_headers(default) if c.startswith("oauth_state="))
    assert "secure" in default_cookie.lower()
    assert "OAUTH_COOKIE_SECURE" in caplog.text

    # Explicit opt-out for a documented plain-HTTP local development setup.
    monkeypatch.setattr(settings, "OAUTH_COOKIE_SECURE", False)
    opted_out = _google_login(client, owner)
    opted_out_cookie = next(
        c for c in _set_cookie_headers(opted_out) if c.startswith("oauth_state=")
    )
    assert "secure" not in opted_out_cookie.lower()
    assert "httponly" in opted_out_cookie.lower()
    assert "samesite=lax" in opted_out_cookie.lower()


def test_oauth_state_cookie_is_cleared_with_compatible_attributes(client, db, owner, monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    _mock_google_flow(monkeypatch, owner["email"])
    state = "login_" + "9" * 32

    res = client.get(
        "/api/integrations/google/callback",
        params={"code": "c", "state": state},
        cookies={"oauth_state": state},
        follow_redirects=False,
    )
    clearing = next(c for c in _set_cookie_headers(res) if c.startswith('oauth_state=""'))

    lowered = clearing.lower()
    assert "max-age=0" in lowered, clearing
    assert "secure" in lowered, clearing
    assert "samesite=lax" in lowered, clearing
    assert "httponly" in lowered, clearing


def test_invalid_oauth_state_issues_no_code(client, db, owner, monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    _mock_google_flow(monkeypatch, owner["email"])

    res = client.get(
        "/api/integrations/google/callback",
        params={"code": "c", "state": "login_" + "3" * 32},
        cookies={"oauth_state": "login_" + "4" * 32},
        follow_redirects=False,
    )
    location = res.headers["location"]
    assert location.endswith("/?error=invalid_state")
    assert "code=" not in location


def test_normalize_request_path_canonicalizes():
    assert normalize_request_path("/api/health/") == "/api/health"
    assert normalize_request_path("//api//health") == "/api/health"
    assert normalize_request_path("/api/health?x=1") == "/api/health"
    assert normalize_request_path("api/health") == ""
    assert normalize_request_path("") == ""
    assert normalize_request_path(None) == ""


# ---------------------------------------------------------------------------
# Repository-tree tests (skip in the backend-only image)
# ---------------------------------------------------------------------------


def _repo_root() -> Path | None:
    for candidate in [Path(config_module.__file__).resolve()] + list(
        Path(config_module.__file__).resolve().parents
    ):
        if (candidate / "frontend").is_dir() and (candidate / ".gitignore").is_file():
            return candidate
    return None


_REPO_ROOT = _repo_root()
requires_repo = pytest.mark.skipif(
    _REPO_ROOT is None or not (_REPO_ROOT / ".git").exists(),
    reason="repository tree not present (backend-only Docker image)",
)


def _ignored(root: Path, rel: str) -> bool:
    proc = subprocess.run(
        ["git", "-C", str(root), "check-ignore", "-q", "--", rel],
        capture_output=True,
    )
    assert proc.returncode in (0, 1), proc.stderr
    return proc.returncode == 0


@requires_repo
@pytest.mark.parametrize(
    "rel",
    [
        "backend/credentials/token.pickle.legacy",
        "backend/credentials/token.pickle",
        "backend/credentials/credentials.json",
        "backend/credentials/ms_token.json",
        "backend/credentials/some-future-secret.json",
        "backend/vapid/private.pem",
        "backups/whatever.sql",
        "backend/.secret_key",
        "backend/.ingest_key",
        "backend/.env",
        "backend/.env.local",
        ".env",
        ".n8n.env",
    ],
)
def test_credential_derived_paths_are_ignored(rel):
    """P24B-08: directory-level rules, so a new file name cannot leak."""
    assert _ignored(_REPO_ROOT, rel), f"{rel} must be gitignored"


@requires_repo
@pytest.mark.parametrize(
    "rel",
    [
        "backend/.env.example",
        "docker-compose.env.example",
        ".env.example",
        "backend/app/main.py",
        "backend/app/core/config.py",
        "backend/alembic/versions/0010_app_config_bootstrap_marker.py",
        "scripts/backup.sh",
        "docker-compose.yml",
        "frontend/src/lib/api.ts",
    ],
)
def test_templates_and_source_remain_trackable(rel):
    assert not _ignored(_REPO_ROOT, rel), f"{rel} must stay trackable"


@requires_repo
def test_no_credential_file_is_already_tracked():
    """Report-only: tracked secrets would need an operator decision, never a
    silent history rewrite, so this fails loudly instead of acting."""
    tracked = subprocess.run(
        [
            "git",
            "-C",
            str(_REPO_ROOT),
            "ls-files",
            "backend/credentials",
            "backend/vapid",
            "backups",
        ],
        capture_output=True,
        text=True,
    )
    # A broken/unavailable git must not read as "nothing tracked".
    assert tracked.returncode == 0, tracked.stderr
    assert tracked.stdout.strip() == "", f"credential files are tracked: {tracked.stdout}"


# ---------------------------------------------------------------------------
# P24B-07 - backup script
# ---------------------------------------------------------------------------


@requires_repo
def _script_code() -> str:
    """Executable lines of backup.sh, comments removed.

    The static checks below are about *behaviour*, and the script documents its
    reasoning in comments that legitimately mention the very things the checks
    forbid (e.g. why the legacy token.pickle copy is gone). Comments must not be
    able to fail - or pass - a behavioural assertion.
    """
    script = (_REPO_ROOT / "scripts" / "backup.sh").read_text()
    return "\n".join(
        line for line in script.splitlines() if not line.lstrip().startswith("#")
    )


@requires_repo
def test_backup_script_sets_umask_before_creating_anything():
    code = _script_code()
    assert "umask 077" in code

    umask_at = code.index("umask 077")
    first_creation = min(
        i for i in (code.find("mkdir"), code.find("> \"$PG_DIR")) if i != -1
    )
    assert umask_at < first_creation, "umask must be set before any artifact is created"
    # Restrictive modes are asserted behaviourally below, not just by umask.
    assert "chmod 700" in code and "chmod 600" in code


@requires_repo
def test_backup_script_no_longer_copies_the_obsolete_legacy_token():
    code = _script_code()
    # No executable code may reference the legacy token file any more.
    assert "pickle" not in code
    assert "GOOGLE_DIR" not in code
    # Existing behaviour that must be preserved.
    assert "pg_dump" in code
    assert "n8n_$STAMP.sqlite" in code
    assert "KEEP=14" in code
    assert 'prune "$PG_DIR" "*.sql.gz"' in code
    assert 'prune "$N8N_DIR" "*.sqlite"' in code
    # Retention must actually be able to run: a quoted pattern would make `ls`
    # fail and abort the script under `set -e`.
    prune_body = code[code.index("prune()") :]
    assert '"$dir"/$pattern' in prune_body, "the prune glob must expand"


@requires_repo
def test_backup_script_is_valid_bash():
    proc = subprocess.run(["bash", "-n", str(_REPO_ROOT / "scripts" / "backup.sh")], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def _run_backup_copy(tmp_path):
    """Run a COPY of scripts/backup.sh with a stubbed docker.

    The copy resolves its own ROOT to tmp_path, so the real backups/ directory
    cannot be touched; the stub means no real container or database is involved.
    Returns the completed process.
    """
    script_dir = tmp_path / "scripts"
    script_dir.mkdir()
    target = script_dir / "backup.sh"
    target.write_text((_REPO_ROOT / "scripts" / "backup.sh").read_text())

    stub_bin = tmp_path / "bin"
    stub_bin.mkdir()
    docker = stub_bin / "docker"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        'case "$1" in\n'
        '  compose) echo "stub sql dump"; exit 0 ;;\n'
        '  ps) echo "n8n-n8n-1"; exit 0 ;;\n'
        '  exec) echo "stub sqlite bytes"; exit 0 ;;\n'
        "esac\n"
        "exit 0\n"
    )
    docker.chmod(0o755)

    env = dict(os.environ)
    env["PATH"] = f"{stub_bin}:{env['PATH']}"
    return subprocess.run(
        ["bash", str(target)],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        # Start from a permissive umask so the script is what makes it safe.
        preexec_fn=lambda: os.umask(0o022),
    )


@requires_repo
def test_backup_artifacts_are_created_owner_only(tmp_path):
    """The real permission behaviour, with no real data and no real containers."""
    proc = _run_backup_copy(tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert "backup complete" in proc.stdout

    pg_dir = tmp_path / "backups" / "postgres"
    n8n_dir = tmp_path / "backups" / "n8n"
    assert stat.S_IMODE(pg_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(n8n_dir.stat().st_mode) == 0o700

    dumps = list(pg_dir.glob("ai_chief_*.sql.gz"))
    sqlite_files = list(n8n_dir.glob("n8n_*.sqlite"))
    assert len(dumps) == 1 and len(sqlite_files) == 1
    for artifact in dumps + sqlite_files:
        mode = stat.S_IMODE(artifact.stat().st_mode)
        assert mode == 0o600, f"{artifact.name} is {oct(mode)}, expected 0o600"
        assert not mode & (stat.S_IRGRP | stat.S_IROTH)


@requires_repo
def test_backup_retention_keeps_the_newest_keeper_only(tmp_path):
    """KEEP=14 must actually prune, and the run must not fail while doing so."""
    pg_dir = tmp_path / "backups" / "postgres"
    n8n_dir = tmp_path / "backups" / "n8n"
    pg_dir.mkdir(parents=True)
    n8n_dir.mkdir(parents=True)
    for i in range(20):
        for directory, name in (
            (pg_dir, f"ai_chief_20200101_0000{i:02d}.sql.gz"),
            (n8n_dir, f"n8n_20200101_0000{i:02d}.sqlite"),
        ):
            old = directory / name
            old.write_text("old")
            old.chmod(0o600)

    proc = _run_backup_copy(tmp_path)
    assert proc.returncode == 0, proc.stderr

    assert len(list(pg_dir.glob("ai_chief_*.sql.gz"))) == 14
    assert len(list(n8n_dir.glob("n8n_*.sqlite"))) == 14
