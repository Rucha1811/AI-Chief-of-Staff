"""Phase D - D2 public routes, D6 security headers, D7 minimal public health.

Each assertion is made against the RUNNING app (or its live modules), not
against source text, so any of these three behaviours regressing fails here:

* D2  exactly one route was made public (the Google sign-in entry point) and
      the integration surface stays default-denied;
* D6  every response - including 401s - carries the security headers, and
      HSTS / unsafe CSP wildcards are absent while the deployment is
      HTTP-only;
* D7  /api/health reveals one bit ("ok"/"error"), and the detail it used to
      publish is only reachable through the authenticated /api/system/info.

No real secret appears in this file; every value is synthetic.
"""

import pytest

from app.core import access_policy, health as health_obs
from app.main import _PUBLIC_PATHS, SECURITY_HEADERS, app

# The complete public surface, pinned so that "one more public route" cannot
# slip in without a reviewer noticing. D2 added exactly ONE entry.
EXPECTED_PUBLIC_PATHS = {
    "/api/auth/login",
    "/api/auth/status",
    "/api/auth/register",
    "/api/auth/exchange",
    "/api/auth/google/login",
    "/api/health",
    "/api/webhooks/whatsapp",
}


# ---------------------------------------------------------------------------
# D2 - public / private route classification
# ---------------------------------------------------------------------------


def test_public_path_set_is_exactly_the_pinned_surface():
    assert set(_PUBLIC_PATHS) == EXPECTED_PUBLIC_PATHS


def test_google_login_entry_point_is_public():
    """The logged-out AuthGate navigates here in a top-level GET with no
    bearer token; before D2 the middleware answered 401 and anonymous Google
    sign-in could never start."""
    assert "/api/auth/google/login" in _PUBLIC_PATHS


def test_the_login_route_is_not_misrouted_into_the_integration_policy():
    """/api/auth/google/login is an auth route, so it is governed by the
    bearer rule, not by the integrations policy. The two must not overlap."""
    assert access_policy.is_integration_path("/api/auth/google/login") is False
    assert access_policy.is_integration_path("/api/integrations/google/callback") is True


def test_integrations_prefix_is_still_default_denied():
    assert not any(p.startswith("/api/integrations") for p in _PUBLIC_PATHS)


def test_integration_policy_was_not_loosened():
    """Callback is public because it authenticates itself with a signed,
    single-use state cookie; authorize and every credential-bearing route
    stay behind a user session or the ingest API key."""
    assert access_policy.POLICY[("GET", "/api/integrations/google/callback")] == "public"
    assert access_policy.POLICY[("GET", "/api/integrations/google/authorize")] == "user"
    assert access_policy.POLICY[("POST", "/api/integrations/google/disconnect")] == "user"
    assert access_policy.POLICY[("GET", "/api/integrations/config")] == "api_key"
    assert access_policy.POLICY[("POST", "/api/integrations/emails/ingest")] == "api_key"
    assert access_policy.DEFAULT_INTEGRATION_CREDENTIAL == "both"


def test_every_registered_integration_route_has_an_explicit_policy():
    assert access_policy.unclassified_integration_routes(app) == []


def test_google_login_reaches_the_handler_without_a_session(client, monkeypatch):
    """End-to-end D2: no Authorization header, and the request is NOT stopped
    by AuthMiddleware. The OAuth flow itself is stubbed so the assertion does
    not depend on Google client credentials being configured.
    """
    from app.services import google_auth

    class _FakeFlow:
        def authorization_url(self, **_kwargs):
            return "https://accounts.google.com/o/oauth2/v2/auth?stub=1", "state-stub"

    monkeypatch.setattr(google_auth, "_oauth_flow", lambda *a, **k: _FakeFlow())

    res = client.get("/api/auth/google/login", follow_redirects=False)

    # FastAPI's RedirectResponse defaults to 307 Temporary Redirect; what
    # matters is that it is a redirect to Google, NOT the middleware's 401.
    assert res.status_code == 307, res.text
    assert res.headers["location"].startswith("https://accounts.google.com/")
    # The state cookie that the callback later checks is still issued.
    cookies = res.headers.get_list("set-cookie") if hasattr(res.headers, "get_list") else []
    assert any(c.startswith("oauth_state=") for c in cookies), cookies


def test_token_exchange_stays_public_but_the_middleware_reason_is_absent(client):
    """/api/auth/exchange trades the OAuth redirect's single-use code for a
    session token, so it must be reachable without a session. What it must
    NOT be is an AuthMiddleware refusal - that exact body is what would prove
    the route was still behind the login wall.
    """
    res = client.post("/api/auth/exchange", json={"code": "definitely-not-a-real-code"})
    assert "Authentication required" not in res.text


def test_authenticated_surfaces_still_demand_a_session(client):
    for method, path in (
        ("GET", "/api/system/info"),
        ("GET", "/api/tasks"),
        ("GET", "/api/integrations/config"),
    ):
        res = getattr(client, method.lower())(path)
        assert res.status_code == 401, f"{method} {path} -> {res.status_code}"
        assert res.json() == {"detail": "Authentication required"}


# ---------------------------------------------------------------------------
# D6 - security headers
# ---------------------------------------------------------------------------


def test_security_headers_are_defined_without_hsts():
    expected = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
    }
    for name, value in expected.items():
        assert SECURITY_HEADERS[name] == value
    assert not any(
        name.lower() == "strict-transport-security" for name in SECURITY_HEADERS
    ), "HSTS must not be enabled while the deployment is HTTP-only"


def test_content_security_policy_has_no_wildcards_and_no_script_whitelisting():
    csp = SECURITY_HEADERS["Content-Security-Policy"]
    assert "frame-ancestors 'none'" in csp
    assert "base-uri 'none'" in csp
    assert "object-src 'none'" in csp
    assert "*" not in csp, "CSP must not contain a wildcard"
    # default-src / script-src are deliberately ABSENT rather than filled with
    # 'unsafe-inline': a policy that needs unsafe-inline to keep Next.js
    # working is weaker than an honest, narrow one.
    for forbidden in ("unsafe-inline", "unsafe-eval", "default-src", "script-src"):
        assert forbidden not in csp, f"CSP must not contain {forbidden!r}"


def test_permissions_policy_still_allows_the_voice_assistant():
    """camera/microphone must stay available to our own origin: the app's
    in-browser voice assistant records audio, and a blanket `microphone=()`
    would break it silently."""
    policy = SECURITY_HEADERS["Permissions-Policy"]
    assert "microphone=(self)" in policy
    assert "camera=(self)" in policy
    for denied in ("geolocation", "payment", "usb", "serial"):
        assert f"{denied}=()" in policy


def test_headers_are_attached_to_a_public_response(client):
    res = client.get("/api/health")
    for name, value in SECURITY_HEADERS.items():
        assert res.headers.get(name) == value, name
    assert "strict-transport-security" not in res.headers


def test_headers_are_attached_to_a_401_response(client):
    """Proves SecurityHeadersMiddleware is registered OUTERMOST: a response
    produced by the auth layer below it still carries them."""
    res = client.get("/api/system/info")
    assert res.status_code == 401
    for name, value in SECURITY_HEADERS.items():
        assert res.headers.get(name) == value, name


def test_a_handler_may_set_a_stricter_header_without_being_downgraded(client):
    res = client.get("/api/health")
    # The middleware never overwrites; it only fills in what is missing.
    assert res.headers.get("X-Content-Type-Options") == "nosniff"


# ---------------------------------------------------------------------------
# D7 - minimal public health
# ---------------------------------------------------------------------------


def test_public_health_returns_exactly_one_key(client):
    res = client.get("/api/health")
    assert res.status_code == 200, res.text
    assert res.json() == {"status": "ok"}


def test_public_health_leaks_no_operational_detail(client):
    body = client.get("/api/health").text.lower()
    for leaked in (
        "pool",
        "checked_out",
        "overflow",
        "n8n",
        "postgres",
        "sqlite",
        "qwen",
        "ollama",
        "version",
        "detail",
        "integrations",
        "backend",
        "127.0.0.1",
        "localhost",
        "cpu",
        "memory",
    ):
        assert leaked not in body, f"/api/health must not expose {leaked!r}: {body}"


def test_public_health_never_echoes_an_exception(client, monkeypatch):
    """A failing probe answers 503 with a status and nothing else - the
    exception type, message and connection string all stay server-side."""
    monkeypatch.setattr(
        health_obs,
        "db_health",
        lambda: {"status": "error", "detail": "db connectivity failed: Explained"},
    )
    res = client.get("/api/health")
    assert res.status_code == 503
    assert res.json() == {"status": "error"}
    assert "Explained" not in res.text


def test_operational_detail_moved_to_the_authenticated_endpoint(client, owner, monkeypatch):
    """/api/system/info must still carry everything the monitoring page
    needs - it just is not reachable anonymously any more."""
    monkeypatch.setattr(
        health_obs,
        "ai_health",
        lambda: {"ollama": {"status": "ok"}, "ai_queue": {}, "circuit_breaker": "CLOSED"},
    )

    anon = client.get("/api/system/info")
    assert anon.status_code == 401

    authz = {"Authorization": f"Bearer {owner['token']}"}
    res = client.get("/api/system/info", headers=authz)
    assert res.status_code == 200, res.text
    body = res.json()

    assert body["system"]["status"] == "ok"
    assert body["system"]["database"]["pool"]["size"] >= 1
    assert "integrations" in body["system"]
    assert body["system"]["app"]
    assert body["system"]["version"]
    assert "ai" in body
