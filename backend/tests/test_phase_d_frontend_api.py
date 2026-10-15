"""Phase D - D1: exactly one place decides which backend the browser calls.

The defect this pins down: `NEXT_PUBLIC_API_URL` is INLINED into the browser
bundle by `next build`, so a runtime `environment:` entry in docker-compose
could never have changed it - the app silently talked to
`http://localhost:8000` no matter what was configured. Phase D moves the dev
fallback and the build-time read into a single module, wires the value through
`build.args`, and this file makes sure it cannot drift apart again.

Everything here is a static read of the frontend tree. No build, no network.
"""

import re
from pathlib import Path

import pytest

from app.core import config as config_module

# Same discovery as the other static suites: the backend Docker image contains
# only /app, so the frontend tree is absent there and these tests skip with an
# explicit reason rather than passing vacuously.
def _repo_root() -> Path | None:
    for candidate in [Path(config_module.__file__).resolve()] + list(
        Path(config_module.__file__).resolve().parents
    ):
        if (candidate / "frontend").is_dir():
            return candidate
    return None


_REPO_ROOT = _repo_root()
FRONTEND_ROOT = _REPO_ROOT / "frontend" if _REPO_ROOT else None
SRC_ROOT = FRONTEND_ROOT / "src" if FRONTEND_ROOT else None

requires_frontend = pytest.mark.skipif(
    SRC_ROOT is None, reason="repository root / frontend tree not present"
)

# The dev backend origin, wherever it is allowed to appear.
_DEV_ORIGIN = re.compile(r"(localhost|127\.0\.0\.1):8000")
# The single module that may name it. (Values are relative to frontend/src.)
CONFIG_MODULE = "lib/config.ts"
# Reading the build-time value. MUST happen in exactly one module.
_BUILD_TIME_READ = re.compile(r"process\.env\.NEXT_PUBLIC_API_URL")


def _source_files():
    return sorted(
        list(SRC_ROOT.rglob("*.ts")) + list(SRC_ROOT.rglob("*.tsx"))
    )


# ---------------------------------------------------------------------------
# the single module
# ---------------------------------------------------------------------------


@requires_frontend
def test_the_single_config_module_exists():
    assert (SRC_ROOT / "lib" / "config.ts").is_file()


@requires_frontend
def test_only_the_config_module_reads_the_build_time_url():
    readers = [str(p.relative_to(SRC_ROOT)) for p in _source_files() if _BUILD_TIME_READ.search(p.read_text(errors="ignore"))]
    assert readers == ["lib/config.ts"], (
        "NEXT_PUBLIC_API_URL must be read in exactly one module, got "
        f"{sorted(readers)}"
    )


@requires_frontend
def test_no_source_file_hardcodes_the_dev_backend_origin():
    offenders = [
        str(p.relative_to(SRC_ROOT))
        for p in _source_files()
        if _DEV_ORIGIN.search(p.read_text(errors="ignore"))
        and str(p.relative_to(SRC_ROOT)) != CONFIG_MODULE
    ]
    assert offenders == [], (
        "the development fallback belongs in lib/config.ts alone; found "
        f"{sorted(offenders)}"
    )


@requires_frontend
def test_the_config_module_exports_both_accessors():
    text = (SRC_ROOT / "lib" / "config.ts").read_text(errors="ignore")
    assert "export function getApiBaseUrl" in text
    assert "export function getGoogleLoginUrl" in text
    # The Google sign-in path lives here too, so a path change is one edit.
    assert "/api/auth/google/login" in text


@requires_frontend
def test_the_api_client_takes_its_base_url_from_the_config_module():
    text = (SRC_ROOT / "lib" / "api.ts").read_text(errors="ignore")
    assert "getApiBaseUrl" in text
    assert not re.search(r'const\s+BASE_URL\s*=\s*["\']https?://', text), (
        "api.ts must not define its own base URL"
    )


@requires_frontend
def test_the_auth_gate_does_not_assemble_the_google_login_url_by_hand():
    text = (SRC_ROOT / "components" / "AuthGate.tsx").read_text(errors="ignore")
    assert "getGoogleLoginUrl" in text
    assert "api/auth/google/login" not in text, (
        "the Google sign-in path must come from lib/config.ts"
    )


# ---------------------------------------------------------------------------
# build-time wiring (this is what made the old runtime env entry useless)
# ---------------------------------------------------------------------------


@requires_frontend
def test_frontend_dockerfile_takes_the_url_as_a_build_argument():
    text = (FRONTEND_ROOT / "Dockerfile").read_text(errors="ignore")
    assert re.search(r"^ARG NEXT_PUBLIC_API_URL\s*$", text, re.M), (
        "the builder stage must accept NEXT_PUBLIC_API_URL as an ARG"
    )
    assert re.search(r"^ENV NEXT_PUBLIC_API_URL=", text, re.M), (
        "the ARG must be promoted to ENV so `next build` inlines it"
    )


@requires_frontend
def test_frontend_dockerfile_exposes_no_server_secret_as_an_env_key():
    """Stricter than the pre-existing check in a way that matters: every ENV
    key must be either the build-time public prefix or NODE_ENV (which Next
    itself sets and which names no secret)."""
    allowed = {"NODE_ENV"}
    for line in (FRONTEND_ROOT / "Dockerfile").read_text(errors="ignore").splitlines():
        stripped = line.strip()
        if not stripped.upper().startswith("ENV "):
            continue
        for key in re.findall(r"\b([A-Z][A-Z0-9_]*)\s*=", stripped):
            assert key.startswith("NEXT_PUBLIC_") or key in allowed, key


@requires_frontend
def test_the_runner_stage_runs_unprivileged():
    text = (FRONTEND_ROOT / "Dockerfile").read_text(errors="ignore")
    assert re.search(r"^USER node\s*$", text, re.M)
    # The mountpoint for the production tmpfs must exist in the image.
    assert ".next/cache" in text


# ---------------------------------------------------------------------------
# frontend security headers (D6, frontend side)
# ---------------------------------------------------------------------------


def _next_config_headers(text: str) -> dict:
    """The actual `{ key, value }` objects inside the async headers() list,
    ignoring comments and prose. This is what the server will emit, so the
    assertions are made against structure, not documentation."""
    pairs = re.findall(r'\{\s*key:\s*"([^"]+)",\s*value:\s*"(.*?)",?\s*\}', text)
    return {key: value for key, value in pairs}


@requires_frontend
def test_next_config_declares_security_headers_without_hsts():
    text = (FRONTEND_ROOT / "next.config.js").read_text(errors="ignore")
    assert "async headers()" in text
    headers = _next_config_headers(text)
    for header in (
        "X-Content-Type-Options",
        "X-Frame-Options",
        "Referrer-Policy",
        "Permissions-Policy",
        "Content-Security-Policy",
    ):
        assert header in headers, header
    # HSTS must be absent from what is EMITTED. (Prose explaining that it is
    # deliberately absent may well say the word; the header object must not.)
    assert "Strict-Transport-Security" not in headers


@requires_frontend
def test_next_config_csp_matches_the_backend_policy():
    """The two layers must not disagree: same narrow directives, no
    unsafe-inline, and microphone stays allowed for the voice assistant."""
    headers = _next_config_headers((FRONTEND_ROOT / "next.config.js").read_text(errors="ignore"))
    csp = headers["Content-Security-Policy"]
    assert "frame-ancestors 'none'" in csp
    assert "base-uri 'none'" in csp
    assert "object-src 'none'" in csp
    for forbidden in ("unsafe-inline", "unsafe-eval", "default-src", "script-src", "*"):
        assert forbidden not in csp, f"CSP must not contain {forbidden!r}"
    assert "microphone=(self)" in headers["Permissions-Policy"]


@requires_frontend
def test_next_config_evaluates_and_produces_the_headers():
    """Execute the config rather than pattern-matching it, so a syntax error
    or a broken async function cannot pass as 'the header is present'."""
    import shutil
    import subprocess

    node = shutil.which("node")
    if node is None:  # pragma: no cover - depends on the host
        pytest.skip("node not available")
    script = (
        "const c = require('./next.config.js');"
        "Promise.resolve(c.headers()).then((h) => {"
        "  const hs = Object.fromEntries(h[0].headers.map((x) => [x.key, x.value]));"
        "  console.log(JSON.stringify({ source: h[0].source, hs }));"
        "});"
    )
    proc = subprocess.run(
        [node, "-e", script], cwd=FRONTEND_ROOT, capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, proc.stderr

    import json

    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["source"] == "/(.*)"
    headers = payload["hs"]
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert "strict-transport-security" not in {k.lower() for k in headers}
    assert "unsafe-inline" not in headers["Content-Security-Policy"]
    assert "microphone=(self)" in headers["Permissions-Policy"]


@requires_frontend
def test_the_monitoring_page_reads_detail_from_the_authenticated_payload():
    """D7: the public probe only carries `status`, so every operational number
    on the monitoring page has to come from /api/system/info."""
    text = (SRC_ROOT / "app" / "monitoring" / "page.tsx").read_text(errors="ignore")
    assert "info?.system" in text, "detail must be sourced from SystemInfo.system"
    for banned in (
        "health?.database",
        "health?.integrations",
        "health?.app",
        "health?.version",
    ):
        assert banned not in text, f"{banned} no longer exists on the public probe"
    # The public probe is still consulted - for its one bit.
    assert "health?.status" in text
