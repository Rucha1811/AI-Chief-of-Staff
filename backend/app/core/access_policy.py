"""Central, explicit authorization policy for API routes (P24B-04).

AuthMiddleware used to skip *every* path under ``/api/integrations/`` and let
each handler check something by hand. That is safe only as long as nobody ever
forgets: a route added under that prefix later inherits "public" silently, and
the only thing standing between it and the internet is the next person's
memory. This module replaces the prefix with an explicit per-route policy.

Design notes
------------
* The policy is keyed by (HTTP method, registered path *template*), so
  parameterized routes such as ``/api/integrations/user/{provider}`` are
  classified, not pattern-guessed. The template comes from the router itself
  (``Route.matches``), i.e. from the same table that will handle the request -
  never from string surgery on the incoming URL.
* Every route under the prefix must be listed. Anything unlisted falls into
  ``DEFAULT_INTEGRATION_CREDENTIAL``, which is deliberately strict, so a new
  route fails closed even if the classifying test is skipped or removed.
* The credential *requirements* are enforced here; the handlers keep their own
  checks unchanged (``_check_api_key``, ``get_current_user``,
  ``require_permission``). Same decision, same comparison
  (``security.verify_integration_key``), evaluated twice - not two different
  notions of what "authorized" means.
"""

from typing import Optional

from fastapi.routing import APIRoute
from starlette.routing import Match

from app.core.config import normalize_request_path
from app.core.security import verify_integration_key

INTEGRATIONS_PREFIX = "/api/integrations/"
INTEGRATIONS_ROOT = "/api/integrations"

# Credential classes.
CREDENTIAL_PUBLIC = "public"
CREDENTIAL_USER = "user"
CREDENTIAL_API_KEY = "api_key"
CREDENTIAL_BOTH = "both"

# Unclassified integration routes require BOTH a user session and the ingest
# API key. Rationale: such a route is unreachable anonymously (the bug this
# replaces) *and* unreachable server-to-server without a session, so adding one
# can never quietly publish data. Classifying it explicitly (a one-line entry
# above) is what makes it usable.
DEFAULT_INTEGRATION_CREDENTIAL = CREDENTIAL_BOTH

# Every registered /api/integrations/ route, with the credential it needs.
# The exhaustive walk is asserted by
# tests/test_p24c_security.py::test_every_integration_route_has_an_explicit_policy.
_EXPLICIT_INTEGRATION_POLICY: dict[tuple[str, str], str] = {
    # Public only because the caller is Google's redirect and cannot present a
    # credential; it authenticates with the OAuth state value itself.
    ("GET", "/api/integrations/google/callback"): CREDENTIAL_PUBLIC,
    # User-scoped Google connection: session required (handler: get_current_user).
    ("GET", "/api/integrations/google/authorize"): CREDENTIAL_USER,
    ("POST", "/api/integrations/google/disconnect"): CREDENTIAL_USER,
    # Per-user integration connections (handler: get_current_user and, for
    # writes, require_permission("manage_integrations")).
    ("GET", "/api/integrations/user"): CREDENTIAL_USER,
    ("POST", "/api/integrations/user"): CREDENTIAL_USER,
    ("DELETE", "/api/integrations/user/{provider}"): CREDENTIAL_USER,
    # n8n / Agent Sync server-to-server endpoints. These must stay
    # API-key-only: a user session must NOT be enough to drive them, which is
    # why they are classified as CREDENTIAL_API_KEY and not CREDENTIAL_BOTH.
    ("GET", "/api/integrations/config"): CREDENTIAL_API_KEY,
    ("POST", "/api/integrations/emails/ingest"): CREDENTIAL_API_KEY,
    ("POST", "/api/integrations/retention/purge"): CREDENTIAL_API_KEY,
    ("GET", "/api/integrations/agent/accounts"): CREDENTIAL_API_KEY,
    ("POST", "/api/integrations/agent/sync"): CREDENTIAL_API_KEY,
}


def is_integration_path(path: str) -> bool:
    return path == INTEGRATIONS_ROOT or path.startswith(INTEGRATIONS_PREFIX)


# Read-only view of the policy, for the startup log and the regression tests.
POLICY = _EXPLICIT_INTEGRATION_POLICY


def integration_credential(method: str, template: Optional[str]) -> str:
    """The credential class for an integration route template.

    Unknown templates (a route added without a policy entry, or a path that
    matches no route at all) resolve to the strict default.
    """
    if not template:
        return DEFAULT_INTEGRATION_CREDENTIAL
    return _EXPLICIT_INTEGRATION_POLICY.get(
        (method.strip().upper(), template), DEFAULT_INTEGRATION_CREDENTIAL
    )


def matched_route_template(app, scope) -> Optional[str]:
    """The registered path template this request is going to be routed to.

    Uses the router's own matcher against a copy of the scope, so the answer is
    exactly what the routing layer will do - including path parameters - and the
    live scope is left untouched.

    Falls back to the slash-normalized path so that a cosmetic trailing slash
    (``/api/integrations/config/``) is classified like the canonical route
    instead of falling through to the strict default; the alternative would turn
    a plain redirect into a spurious 401 for a client that did nothing wrong.
    """
    raw_path = scope.get("path")
    candidates = [scope]
    normalized = normalize_request_path(raw_path)
    if normalized and normalized != raw_path:
        patched = dict(scope)
        patched["path"] = normalized
        candidates.append(patched)

    for candidate in candidates:
        for route in getattr(app, "routes", []):
            if not isinstance(route, APIRoute):
                continue
            try:
                match, _child_scope = route.matches(dict(candidate))
            except Exception:  # pragma: no cover - defensive: never fail open
                continue
            if match == Match.FULL:
                return route.path
    return None


def has_valid_api_key(headers) -> bool:
    """True when the request carries the ingest API key (constant-time)."""
    from app.core.config import settings

    return verify_integration_key(headers.get("x-api-key"), settings.INGEST_API_KEY)


def unclassified_integration_routes(app) -> list[str]:
    """Registered integration routes with no explicit policy entry.

    Reported at startup and asserted by the regression tests: the runtime
    default already fails closed, so this is an early-warning signal rather
    than the enforcement itself.
    """
    missing: list[str] = []
    for route in getattr(app, "routes", []):
        if not isinstance(route, APIRoute):
            continue
        if not is_integration_path(route.path):
            continue
        for method in sorted(route.methods or ()):
            if (method.upper(), route.path) not in _EXPLICIT_INTEGRATION_POLICY:
                missing.append(f"{method.upper()} {route.path}")
    return missing
