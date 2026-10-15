"""P2.3: secret and default hardening.

Two guarantees are pinned here:

1. PRODUCTION fails fast. ``ENVIRONMENT=production`` refuses to start unless
   every security-critical secret is explicitly configured and strong, so a
   missing or weak value can never silently sign tokens, gate login, encrypt
   OAuth credentials, or open the database.
2. DEVELOPMENT still works. Unset ``SECRET_KEY`` / ``INGEST_API_KEY`` are
   auto-generated once and persisted to a gitignored 0600 file, which is what
   keeps ``docker compose up`` and the test suite working with no setup.

No real secret appears in this file; every value is generated or synthetic.
"""

import os
import re
import stat
from pathlib import Path

import pytest

from app.core import config as config_module
from app.core.config import (
    InsecureConfigurationError,
    Settings,
    enforce_settings,
    secret_problem,
    whatsapp_provider_problem,
)

# Synthetic values that satisfy the production policy. Not secrets.
GOOD_SECRET = "synthetic-" + "K7f2Qx9ZpL4mNb8vRc3TwY6dH1JgA5eU0" * 2
GOOD_PASSPHRASE = "correct-horse-battery-staple-42"
GOOD_DB_URL = "postgresql://aichief:s3l3ct-p4ssw0rd@postgres:5432/ai_chief"

# Every production-only argument the gate needs, so each test only overrides
# the one thing it is about.
_PROD_BASE = dict(
    environment="production",
    secret_key=GOOD_SECRET,
    ingest_api_key=GOOD_SECRET,
    auth_password=GOOD_PASSPHRASE,
    database_url=GOOD_DB_URL,
    whatsapp_provider="none",
)


def _prod(**overrides) -> None:
    enforce_settings(**{**_PROD_BASE, **overrides})


def _problems(**overrides) -> list[str]:
    """Return the refusal message's bullet lines instead of raising."""
    with pytest.raises(InsecureConfigurationError) as excinfo:
        _prod(**overrides)
    return [
        line.strip()[2:].strip()
        for line in str(excinfo.value).splitlines()
        if line.strip().startswith("- ")
    ]


# ---------------------------------------------------------------------------
# 1. production configuration fails when SECRET_KEY is missing
# ---------------------------------------------------------------------------


def test_production_refuses_to_start_without_secret_key():
    problems = _problems(secret_key=None)
    assert any("SECRET_KEY is not set" in p for p in problems)


def test_production_refuses_placeholder_secret_key():
    """The shipped default is a placeholder and must never be accepted."""
    problems = _problems(secret_key=config_module._FALLBACK_SECRET_KEY)
    assert any("SECRET_KEY" in p for p in problems)


def test_missing_secret_key_is_reported_even_when_dev_fallback_exists(monkeypatch):
    """The gate must judge what was CONFIGURED, not the auto-generated value.

    ``_resolve_secret_key`` fills an unset SECRET_KEY with a generated key.
    If the gate read ``settings.SECRET_KEY`` it would see that generated key
    and wrongly conclude production was configured.
    """
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.setattr(config_module, "_ENV_FILE", Path("/nonexistent/.env"))
    generated = config_module._resolve_secret_key(None)
    assert len(generated) >= 32  # dev fallback really did produce a key

    problems = _problems(secret_key=config_module.configured_value("SECRET_KEY"))
    assert any("SECRET_KEY" in p for p in problems)


def test_production_refuses_blank_secret_key():
    assert any("SECRET_KEY" in p for p in _problems(secret_key="   "))


# ---------------------------------------------------------------------------
# 2. weak / default SECRET_KEY is rejected in production
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "weak",
    [
        "changeme",
        "secret",
        "password",
        "your-secret-here",
        "change-this-to-a-random-secret-key",
        "CHANGE-ME-PLEASE",
        "<your-secret-key-here>",
        "admin",
        "changeme" * 6,          # long enough to pass a length check, no entropy
        "abcabcabcabcabcabcabcabcabcabc",  # repeated fragment
    ],
)
def test_production_rejects_weak_secret_keys(weak):
    assert secret_problem("SECRET_KEY", weak, config_module._MIN_SECRET_LENGTH) is not None
    with pytest.raises(InsecureConfigurationError):
        _prod(secret_key=weak)


def test_production_rejects_short_secret_key():
    problem = secret_problem("SECRET_KEY", "a" * 31, config_module._MIN_SECRET_LENGTH)
    assert "too short" in problem


@pytest.mark.parametrize(
    "strong",
    [
        GOOD_SECRET,
        "K7f2Qx9ZpL4mNb8vRc3TwY6dH1JgA5eU0Bf3Hi9Lo2Pq7Rs5",  # random-looking
        "abcxxxdefQ4tR7wE1uI9oP3aS6dF0gH5jK2lZ8xC4vB6nM1",  # contains "xxx"
        "correct horse battery staple nine seven",  # passphrase with spaces
    ],
)
def test_strong_secret_keys_are_accepted(strong):
    assert secret_problem("SECRET_KEY", strong, config_module._MIN_SECRET_LENGTH) is None


def test_single_repeated_character_is_rejected_despite_its_length():
    """40 x's passes a length check but carries ~0 bits of entropy."""
    assert secret_problem("SECRET_KEY", "x" * 40, config_module._MIN_SECRET_LENGTH) is not None


def test_production_rejects_weak_ingest_api_key():
    problems = _problems(ingest_api_key="changeme")
    assert any("INGEST_API_KEY" in p for p in problems)


def test_production_rejects_weak_app_password():
    problems = _problems(auth_password="a" * 7)
    assert any("AUTH_PASSWORD" in p for p in problems)


def test_production_rejects_development_database_password():
    """A local-dev DB password must not reach production."""
    problems = _problems(
        database_url="postgresql://postgres:postgres@postgres:5432/ai_chief"
    )
    assert any("DATABASE_URL" in p and "development password" in p for p in problems)


def test_production_rejects_well_known_database_password():
    assert any(
        "DATABASE_URL" in p
        for p in _problems(
            database_url="postgresql://aichief:password@postgres:5432/ai_chief"
        )
    )


def test_production_accepts_a_fully_hardened_configuration():
    """The happy path must not be over-restrictive: this has to be deployable."""
    _prod()


def test_production_accepts_hashed_app_password():
    """A pbkdf2$... hash is a legitimate production AUTH_PASSWORD."""
    _prod(auth_password="pbkdf2$120000$" + "ab" * 16 + "$" + "cd" * 32)


def test_production_rejects_malformed_password_hash():
    assert any("AUTH_PASSWORD" in p for p in _problems(auth_password="pbkdf2$oops"))


# ---------------------------------------------------------------------------
# 3. required API key missing -> fail closed
# ---------------------------------------------------------------------------


def test_production_requires_ingest_api_key():
    problems = _problems(ingest_api_key=None)
    assert any("INGEST_API_KEY is not set" in p for p in problems)


def test_ingest_endpoints_still_reject_missing_and_wrong_keys(client, db, owner):
    """Fail closed at the request level too, not just at startup."""
    url = "/api/integrations/emails/ingest"
    body = {"emails": []}

    assert client.post(url, json=body).status_code == 401
    assert client.post(url, json=body, headers={"X-API-Key": ""}).status_code == 401
    assert client.post(
        url, json=body, headers={"X-API-Key": "wrong-key"}
    ).status_code == 401

    # The real key still works, so the guard is not simply rejecting everything.
    from app.core.config import settings

    ok = client.post(url, json=body, headers={"X-API-Key": settings.INGEST_API_KEY})
    assert ok.status_code == 200


def test_agent_endpoints_require_the_ingest_key(client, db, owner):
    """The n8n -> backend surface is gated by the same key."""
    assert client.get("/api/integrations/agent/accounts").status_code == 401
    assert client.get(
        "/api/integrations/agent/accounts", headers={"X-API-Key": "not-the-key"}
    ).status_code == 401

    # A schema-valid body, so the 401 comes from the key check and not from
    # request validation.
    sync_body = {"account_id": "someone@example.com"}
    assert client.post(
        "/api/integrations/agent/sync", json=sync_body
    ).status_code == 401
    assert client.post(
        "/api/integrations/agent/sync",
        json=sync_body,
        headers={"X-API-Key": "not-the-key"},
    ).status_code == 401

    # With the real key it gets past authentication (identity then 404s).
    from app.core.config import settings

    authed = client.post(
        "/api/integrations/agent/sync",
        json=sync_body,
        headers={"X-API-Key": settings.INGEST_API_KEY},
    )
    assert authed.status_code == 404


def test_integrations_config_endpoint_requires_the_ingest_key(client, db, owner):
    assert client.get("/api/integrations/config").status_code == 401
    from app.core.config import settings

    assert client.get(
        "/api/integrations/config", headers={"X-API-Key": settings.INGEST_API_KEY}
    ).status_code == 200


def test_development_generated_ingest_key_is_strong_enough_for_production_policy():
    """The dev fallback must not be a value production would reject."""
    generated = config_module._resolve_ingest_key(None)
    assert (
        secret_problem("INGEST_API_KEY", generated, config_module._MIN_SECRET_LENGTH)
        is None
    )


# ---------------------------------------------------------------------------
# 4. WhatsApp provider missing credentials -> fail closed
# ---------------------------------------------------------------------------


def _wa_problems(provider, **overrides) -> list[str]:
    kwargs = dict(
        twilio_account_sid=None,
        twilio_auth_token=None,
        twilio_from=None,
        meta_phone_number_id=None,
        meta_access_token=None,
        meta_app_secret=None,
        meta_verify_token=None,
    )
    kwargs.update(overrides)
    return whatsapp_provider_problem(provider, **kwargs)


def test_whatsapp_disabled_needs_no_credentials():
    assert _wa_problems("none") == []
    assert _wa_problems(None) == []
    assert _wa_problems("") == []


@pytest.mark.parametrize("provider", ["twilio", "twilio_sandbox"])
def test_twilio_provider_requires_every_credential(provider):
    problems = _wa_problems(provider)
    assert any("TWILIO_ACCOUNT_SID" in p for p in problems)
    assert any("TWILIO_AUTH_TOKEN" in p for p in problems)
    assert any("TWILIO_WHATSAPP_FROM" in p for p in problems)

    complete = _wa_problems(
        provider,
        twilio_account_sid="AC" + "0" * 32,
        twilio_auth_token="auth-token-value",
        twilio_from="whatsapp:+10000000000",
    )
    assert complete == []


@pytest.mark.parametrize("provider", ["meta", "whatsapp_cloud_api"])
def test_meta_provider_requires_every_credential(provider):
    problems = _wa_problems(provider)
    assert any("META_WA_PHONE_NUMBER_ID" in p for p in problems)
    assert any("META_WA_ACCESS_TOKEN" in p for p in problems)
    assert any("META_WA_APP_SECRET" in p for p in problems)
    assert any("META_WA_VERIFY_TOKEN" in p for p in problems)

    complete = _wa_problems(
        provider,
        meta_phone_number_id="1234567890",
        meta_access_token="access-token-value",
        meta_app_secret="app-secret-value",
        meta_verify_token="verify-token-value",
    )
    assert complete == []


def test_half_configured_provider_stops_startup_in_every_environment():
    """A half-configured provider is refused even outside production.

    Otherwise the webhook would come up with signature verification
    impossible, which is exactly the fail-open case to avoid.
    """
    with pytest.raises(InsecureConfigurationError):
        enforce_settings(
            environment="development",
            secret_key=GOOD_SECRET,
            ingest_api_key=GOOD_SECRET,
            auth_password=None,
            database_url="sqlite:///./ai_chief.db",
            whatsapp_provider="twilio",
            twilio_account_sid="AC" + "0" * 32,
            twilio_auth_token=None,
            twilio_from=None,
        )


def test_whatsapp_settings_have_no_baked_in_defaults():
    """No provider credential and no phone number ships as a default."""
    fields = Settings.model_fields
    for name in (
        "TWILIO_ACCOUNT_SID",
        "TWILIO_AUTH_TOKEN",
        "TWILIO_WHATSAPP_FROM",
        "META_WA_PHONE_NUMBER_ID",
        "META_WA_ACCESS_TOKEN",
        "META_WA_APP_SECRET",
        "META_WA_VERIFY_TOKEN",
    ):
        assert (fields[name].default or "") == "", name

    # And no real-looking phone number anywhere in the shipped config.
    source = Path(config_module.__file__).read_text()
    assert not re.search(r"\+\d{10,15}", source)


def test_webhook_stays_closed_when_provider_credentials_are_missing(
    client, db, owner, monkeypatch
):
    """With provider=none the webhook refuses every request."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "WHATSAPP_PROVIDER", "none")
    assert client.post("/api/webhooks/whatsapp", content="Body=hi").status_code == 503


def test_twilio_signature_verification_fails_closed_without_a_token(client, db, monkeypatch):
    """An empty auth token must never validate a signature."""
    from app.core.config import settings
    from app.services.whatsapp_service import verify_twilio_signature

    monkeypatch.setattr(settings, "WHATSAPP_PROVIDER", "twilio")
    url = "http://testserver/api/webhooks/whatsapp"
    params = {"MessageSid": "SM1", "From": "whatsapp:+15550000001", "Body": "hi"}

    # Compute a valid signature for a known token, then present it while the
    # server holds no token: verification must fail.
    import base64
    import hashlib
    import hmac

    real_token = "the-real-auth-token"
    payload = url + "".join(f"{k}{v}" for k, v in sorted(params.items()))
    good_sig = base64.b64encode(
        hmac.new(real_token.encode(), payload.encode(), hashlib.sha1).digest()
    ).decode()

    assert verify_twilio_signature("", url, params, good_sig) is False
    assert verify_twilio_signature(None, url, params, good_sig) is False

    monkeypatch.setattr(settings, "TWILIO_AUTH_TOKEN", "")
    res = client.post(
        "/api/webhooks/whatsapp",
        data=params,
        headers={"X-Twilio-Signature": good_sig},
    )
    assert res.status_code == 401


def test_meta_signature_verification_fails_closed_without_an_app_secret():
    from app.services.whatsapp_service import verify_meta_signature

    body = b'{"From":"1555","Body":"hi"}'
    assert verify_meta_signature("", body, "sha256=abc") is False
    assert verify_meta_signature(None, body, "sha256=abc") is False
    assert verify_meta_signature("app-secret", body, None) is False


def test_meta_get_verification_fails_closed_without_a_verify_token(client, db, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "META_WA_VERIFY_TOKEN", "")
    res = client.get(
        "/api/webhooks/whatsapp",
        params={"hub.mode": "subscribe", "hub.verify_token": "anything",
                "hub.challenge": "1234"},
    )
    assert res.status_code == 403


# ---------------------------------------------------------------------------
# 5. development mode still starts with documented development configuration
# ---------------------------------------------------------------------------


def test_development_starts_with_no_secrets_configured(monkeypatch):
    """The documented dev workflow (nothing configured) must keep working."""
    enforce_settings(
        environment="development",
        secret_key=None,
        ingest_api_key=None,
        auth_password=None,
        database_url="sqlite:///./ai_chief.db",
        whatsapp_provider="none",
    )


@pytest.mark.parametrize("environment", ["development", "testing", "staging"])
def test_non_production_environments_keep_their_conveniences(environment):
    enforce_settings(
        environment=environment,
        secret_key=None,
        ingest_api_key=None,
        auth_password=None,
        database_url="sqlite:///./ai_chief.db",
        whatsapp_provider="none",
    )


def test_development_allows_the_documented_dev_database_password():
    enforce_settings(
        environment="development",
        secret_key=GOOD_SECRET,
        ingest_api_key=GOOD_SECRET,
        auth_password=None,
        database_url="postgresql://postgres:postgres@localhost:5432/ai_chief",
        whatsapp_provider="none",
    )


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("production", "production"),
        ("PRODUCTION", "production"),
        (" prod ", "production"),
        ("development", "development"),
        ("dev", "development"),
        ("staging", "staging"),
        ("testing", "testing"),
        (None, "development"),
        ("", "development"),
    ],
)
def test_environment_aliases_normalize(raw, expected):
    import pydantic

    if raw is None:
        os.environ.pop("ENVIRONMENT", None)
    else:
        os.environ["ENVIRONMENT"] = raw
    try:
        assert Settings(_env_file=None).ENVIRONMENT == expected
    finally:
        os.environ.pop("ENVIRONMENT", None)


def test_unknown_environment_is_rejected():
    import pydantic

    os.environ["ENVIRONMENT"] = "prodution"  # typo
    try:
        with pytest.raises(pydantic.ValidationError):
            Settings(_env_file=None)
    finally:
        os.environ.pop("ENVIRONMENT", None)


def test_settings_is_production_property_matches_the_flag():
    assert Settings(_env_file=None, ENVIRONMENT="development").is_production is False
    assert Settings(_env_file=None, ENVIRONMENT="production").is_production is True


def test_generated_secrets_are_persisted_private(monkeypatch, tmp_path):
    """Generated secrets go to a 0600 file, never a world-readable one."""
    key_file = tmp_path / ".secret_key"
    ingest_file = tmp_path / ".ingest_key"
    monkeypatch.setattr(config_module, "_SECRET_KEY_FILE", key_file)
    monkeypatch.setattr(config_module, "_INGEST_KEY_FILE", ingest_file)

    secret = config_module._resolve_secret_key(None)
    ingest = config_module._resolve_ingest_key(None)

    assert key_file.read_text().strip() == secret
    assert ingest_file.read_text().strip() == ingest
    for path in (key_file, ingest_file):
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode == 0o600, f"{path.name} is {oct(mode)}"

    # Stable across calls: a restart must not rotate the key.
    assert config_module._resolve_secret_key(None) == secret
    assert config_module._resolve_ingest_key(None) == ingest


def test_unwritable_secret_location_warns_instead_of_failing_silently(caplog, monkeypatch):
    """A key that cannot be persisted must be loud.

    Swallowing this would silently rotate SECRET_KEY on every restart, which
    invalidates every issued token and orphans encrypted OAuth credentials.
    """
    def _boom(self, *args, **kwargs):
        raise OSError("simulated read-only filesystem")

    monkeypatch.setattr(Path, "write_text", _boom)
    with caplog.at_level("WARNING", logger="app.core.config"):
        value = config_module._persist_generated_secret(
            Path("/tmp/does-not-matter/secret"), "k" * 40, "SECRET_KEY"
        )

    assert value == "k" * 40
    messages = [r.getMessage() for r in caplog.records]
    assert any("SECRET_KEY" in m for m in messages), messages
    assert any("regenerated on every restart" in m for m in messages), messages


def test_explicit_secret_key_wins_over_the_stored_file(monkeypatch, tmp_path):
    key_file = tmp_path / ".secret_key"
    key_file.write_text("stored-key-value")
    monkeypatch.setattr(config_module, "_SECRET_KEY_FILE", key_file)
    assert config_module._resolve_secret_key("explicit-key-value") == "explicit-key-value"


def test_current_app_settings_pass_the_policy():
    """The shipped/test configuration must satisfy the gate as loaded."""
    enforce_settings(
        environment=config_module.settings.ENVIRONMENT,
        secret_key=config_module.settings.SECRET_KEY,
        ingest_api_key=config_module.settings.INGEST_API_KEY,
        auth_password=config_module.settings.AUTH_PASSWORD,
        database_url=config_module.settings.DATABASE_URL,
        whatsapp_provider=config_module.settings.WHATSAPP_PROVIDER,
        twilio_account_sid=config_module.settings.TWILIO_ACCOUNT_SID,
        twilio_auth_token=config_module.settings.TWILIO_AUTH_TOKEN,
        twilio_from=config_module.settings.TWILIO_WHATSAPP_FROM,
        meta_phone_number_id=config_module.settings.META_WA_PHONE_NUMBER_ID,
        meta_access_token=config_module.settings.META_WA_ACCESS_TOKEN,
        meta_app_secret=config_module.settings.META_WA_APP_SECRET,
        meta_verify_token=config_module.settings.META_WA_VERIFY_TOKEN,
    )


# ---------------------------------------------------------------------------
# 6. no server secret is exposed through frontend configuration
# ---------------------------------------------------------------------------

def _repo_root() -> Path | None:
    """Walk up from the backend package looking for the repository root.

    The backend Docker image only contains /app (context ./backend), so the
    frontend tree is absent there. These tests then skip with an explicit
    reason instead of passing vacuously - a scan that reads zero files proves
    nothing.
    """
    for candidate in [Path(config_module.__file__).resolve()] + list(
        Path(config_module.__file__).resolve().parents
    ):
        if (candidate / "frontend").is_dir() or (candidate / ".gitignore").is_file():
            if (candidate / "frontend").is_dir():
                return candidate
    return None


_REPO_ROOT = _repo_root()
_FRONTEND_ROOT = (_REPO_ROOT / "frontend") if _REPO_ROOT else None
_FRONTEND_SOURCE_DIRS = ("src", "public")
_SERVER_SECRET_NAMES = (
    "SECRET_KEY",
    "INGEST_API_KEY",
    "AUTH_PASSWORD",
    "GOOGLE_CLIENT_SECRET",
    "TWILIO_AUTH_TOKEN",
    "TWILIO_ACCOUNT_SID",
    "META_WA_ACCESS_TOKEN",
    "META_WA_APP_SECRET",
    "META_WA_VERIFY_TOKEN",
    "N8N_ENCRYPTION_KEY",
    "POSTGRES_PASSWORD",
    "DATABASE_URL",
)


requires_frontend = pytest.mark.skipif(
    _FRONTEND_ROOT is None,
    reason="frontend tree not present (backend-only Docker image)",
)


@requires_frontend
def test_generated_secret_files_are_gitignored():
    """A generated secret must never become committable."""
    patterns = (_REPO_ROOT / ".gitignore").read_text()
    assert ".secret_key" in patterns
    assert ".ingest_key" in patterns
    assert ".n8n.env" in patterns


def _frontend_files():
    for sub in _FRONTEND_SOURCE_DIRS:
        root = _FRONTEND_ROOT / sub
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.is_file() and path.suffix in {
                ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".json", ".html",
                ".css",
            }:
                yield path


def _assert_frontend_present():
    """Guard against a scan that silently reads nothing."""
    files = list(_frontend_files())
    assert files, f"no frontend source files found under {_FRONTEND_ROOT}"
    return files


@requires_frontend
def test_frontend_only_references_public_configuration():
    """Every NEXT_PUBLIC_* variable is public by design; nothing else may be.

    A non-public server variable referenced in frontend code would be either
    inlined as undefined (a bug) or, worse, supplied as a real value and
    shipped to the browser.
    """
    _assert_frontend_present()
    offenders = []
    for path in _frontend_files():
        text = path.read_text(errors="ignore")
        for match in re.finditer(r"process\.env\.([A-Z0-9_]+)", text):
            name = match.group(1)
            if not name.startswith("NEXT_PUBLIC_"):
                offenders.append(f"{path.name}: {name}")
    assert offenders == [], f"non-public env vars referenced in frontend: {offenders}"


@requires_frontend
def test_frontend_never_names_a_server_secret():
    _assert_frontend_present()
    offenders = []
    for path in _frontend_files():
        text = path.read_text(errors="ignore")
        for secret in _SERVER_SECRET_NAMES:
            if secret in text:
                offenders.append(f"{path.name}: {secret}")
    assert offenders == [], f"server secret names in frontend: {offenders}"


@requires_frontend
def test_frontend_env_files_contain_no_server_secret():
    for path in _FRONTEND_ROOT.glob(".env*"):
        for line in path.read_text(errors="ignore").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key = stripped.split("=", 1)[0].strip()
            assert key.startswith("NEXT_PUBLIC_"), f"{path.name}: {key}"


# The only Dockerfile variable names a frontend image may carry besides the
# build-time NEXT_PUBLIC_* family. NODE_ENV is an inert Next.js runtime flag
# (it names the mode, never a secret value) and only ever gets set to
# "production" here; it is the single permitted exception.
_SAFE_FRONTEND_DOCKERFILE_KEYS = {"NODE_ENV"}


@requires_frontend
def test_frontend_dockerfile_only_exposes_public_config():
    """The image build must not bake a server secret into the bundle.

    NEXT_PUBLIC_* values are inlined into the browser bundle by ``next build``,
    so they are public BY DESIGN and the only values the Dockerfile may carry.
    NODE_ENV is inert and is the only non-NEXT_PUBLIC key allowed. Everything
    else - OAuth client secrets, database credentials, HMAC/signing keys, API
    keys, VAPID private key material - is rejected whether it appears as ENV or
    as a build ARG, because a build argument reaches the baked image exactly
    like an ENV value does.
    """
    dockerfile = _FRONTEND_ROOT / "Dockerfile"
    if not dockerfile.is_file():
        pytest.skip("no frontend Dockerfile")
    offenders = []
    for line in dockerfile.read_text(errors="ignore").splitlines():
        stripped = line.strip()
        directive = stripped.split("=", 1)[0].split()[0].upper() if stripped else ""
        if directive not in ("ENV", "ARG"):
            continue
        for key in re.findall(r"\b([A-Z][A-Z0-9_]*)\s*=", stripped):
            if key.startswith("NEXT_PUBLIC_"):
                continue
            if key in _SAFE_FRONTEND_DOCKERFILE_KEYS:
                continue
            offenders.append(f"{directive} {key}")
    assert offenders == [], (
        "frontend Dockerfile sets non-public build-time variable(s): "
        f"{offenders}"
    )

    # Belt and suspenders: the named server-secret family may never appear in
    # the image definition at all, under any casing of the directive.
    declared = dockerfile.read_text(errors="ignore")
    for secret in _SERVER_SECRET_NAMES:
        assert secret not in declared, (
            f"frontend Dockerfile references server secret {secret}"
        )


def test_health_endpoint_exposes_no_secret_material(client, db):
    """Health output is public; it must describe config, never disclose it."""
    res = client.get("/api/health")
    assert res.status_code == 200
    body = res.text
    for secret in ("SECRET_KEY", "INGEST_API_KEY", "AUTH_PASSWORD", "password="):
        assert secret not in body


@requires_frontend
def test_frontend_bundle_exposes_no_server_secret_values():
    """No live server secret value may appear anywhere in frontend sources."""
    from app.core.config import settings

    files = _assert_frontend_present()
    live = {
        "SECRET_KEY": settings.SECRET_KEY,
        "INGEST_API_KEY": settings.INGEST_API_KEY,
    }
    usable = {n: v for n, v in live.items() if v and len(v) >= 16}
    assert len(usable) >= 2, f"expected both live secrets, got {sorted(usable)}"

    checked = 0
    for path in files:
        text = path.read_text(errors="ignore")
        for name, value in usable.items():
            checked += 1
            assert value not in text, f"{name} value found in {path.name}"
    assert checked >= 2 * len(files)


@requires_frontend
def test_repository_docs_contain_no_working_credentials():
    """Documentation must not publish a usable password or token."""
    suspicious = []
    doc_patterns = ("*.md",)
    for pattern in doc_patterns:
        for path in _REPO_ROOT.rglob(pattern):
            if any(part in {"node_modules", ".git", "venv", ".next"} for part in path.parts):
                continue
            for number, line in enumerate(
                path.read_text(errors="ignore").splitlines(), 1
            ):
                # A JSON login example with a literal password value.
                for match in re.finditer(r'"password"\s*:\s*"([^"]+)"', line):
                    suspicious.append(f"{path.name}:{number} password example")
                # A pgpass/DSN with an inline password.
                if re.search(r"://[^\s:/@]+:[^\s:/@]+@", line) and "YOUR" not in line:
                    suspicious.append(f"{path.name}:{number} inline DSN password")
    assert suspicious == [], f"documentation exposes credentials: {suspicious}"


@requires_frontend
def test_no_real_phone_number_is_committed():
    """A real phone number must never be baked into a tracked file."""
    # E.164-shaped numbers, excluding synthetic test/doc fixtures.
    offenders = []
    tracked = [
        "docker-compose.yml", "docker-compose.prod.yml", "docker-compose.n8n.yml",
        "docker-compose.env.example", "backend/.env.example",
        "backend/app/core/config.py",
    ]
    pattern = re.compile(r"(?<![\w.])\+\d{10,15}(?![\w.])")
    for rel in tracked:
        path = _REPO_ROOT / rel
        if not path.is_file():
            continue
        for number, line in enumerate(path.read_text(errors="ignore").splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{rel}:{number}")
    assert offenders == [], f"real-looking phone numbers committed: {offenders}"


@requires_frontend
def test_example_files_use_placeholders_for_secrets():
    """Shipped examples must be inert, never usable credentials."""
    env_example = _REPO_ROOT / "backend" / ".env.example"
    text = env_example.read_text(errors="ignore")

    for literal in (
        "change-this-to-a-random-secret-key",
        "whatsapp:+YOUR_NUMBER_HERE",
    ):
        assert literal in text, f".env.example should carry {literal}"

    # No phone-shaped default in the example config.
    assert not re.search(r"TWILIO_WHATSAPP_FROM=whatsapp:\+\d", text)
    # The default local DSN is allowed but must be labelled development-only.
    assert "DEVELOPMENT-ONLY" in text


@requires_frontend
def test_gitignore_covers_generated_and_real_secret_files():
    """Directory-level rules (P24B-08), not an enumeration of file names.

    A filename list has to be extended every time something new lands in a
    credentials directory; excluding the directories themselves cannot be
    forgotten.
    """
    patterns = (_REPO_ROOT / ".gitignore").read_text()
    for entry in (
        ".env", ".n8n.env", ".secret_key", ".ingest_key",
        "backend/credentials/", "backend/vapid/", "backend/.secret_key",
        "backend/.ingest_key", "backups/", "*.db",
    ):
        assert entry in patterns, f".gitignore should cover {entry}"


@requires_frontend
def test_secret_files_on_disk_are_not_world_readable():
    """Real secrets present on the host must not be mode 0644."""
    import stat as stat_module

    targets = [
        _REPO_ROOT / ".n8n.env",
        _REPO_ROOT / "backend" / "credentials" / "credentials.json",
        _REPO_ROOT / "backend" / "vapid" / "private.pem",
        _REPO_ROOT / "backend" / ".secret_key",
        _REPO_ROOT / "backend" / ".ingest_key",
    ]
    checked = 0
    for path in targets:
        if not path.is_file():
            continue
        checked += 1
        mode = stat_module.S_IMODE(path.stat().st_mode)
        assert not mode & (stat_module.S_IRGRP | stat_module.S_IROTH), (
            f"{path.name} is {oct(mode)}; group/other must not read it"
        )
    assert checked, "no secret files present to check"
