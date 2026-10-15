"""Phase D - D3: production URL / origin correctness.

Development defaults are localhost by design (a laptop has no domain), so the
question this file answers is: does a PRODUCTION process carrying those
defaults get refused, loudly, before it starts signing OAuth redirects?

Guarantees pinned here:

1. ``enforce_settings`` rejects a localhost/0.0.0.0 FRONTEND_URL,
   OAUTH_REDIRECT_URI or CORS_ORIGINS in production, an empty CORS list, and
   an explicitly-insecure OAuth cookie flag.
2. Validation messages name the VARIABLE. They never echo the value, so an
   origin carrying a token in its query string, or credentials in its
   authority, cannot leak into a log, a CI transcript or a crash report.
3. ``CORS_ORIGINS`` accepts both spellings a human would write (comma
   separated and JSON), because pydantic-settings JSON-decoding of a complex
   field otherwise fails with an opaque parse error at import time.
4. Development is untouched: the same localhost defaults it has always had
   still start.

No real secret appears in this file; every value is synthetic.
"""

import pytest
from pydantic import ValidationError

from app.core import config as config_module
from app.core.config import InsecureConfigurationError, enforce_settings

# Synthetic values that satisfy the production policy. Not secrets.
GOOD_SECRET = "synthetic-" + "K7f2Qx9ZpL4mNb8vRc3TwY6dH1JgA5eU0" * 2
GOOD_PASSPHRASE = "correct-horse-battery-staple-42"
GOOD_DB_URL = "postgresql://aichief:s3l3ct-p4ssw0rd@db.example.internal:5432/ai_chief"

# What a correctly-deployed instance looks like (the domain is a reserved
# .internal name, not a guess at a real future hostname).
PROD_FRONTEND = "https://app.example.internal"
PROD_REDIRECT = "https://api.example.internal/api/integrations/google/callback"

_PROD_BASE = dict(
    environment="production",
    secret_key=GOOD_SECRET,
    ingest_api_key=GOOD_SECRET,
    auth_password=GOOD_PASSPHRASE,
    database_url=GOOD_DB_URL,
    whatsapp_provider="none",
    frontend_url=PROD_FRONTEND,
    oauth_redirect_uri=PROD_REDIRECT,
    cors_origins=[PROD_FRONTEND],
    oauth_cookie_secure=True,
)


def _prod(**overrides) -> None:
    enforce_settings(**{**_PROD_BASE, **overrides})


def _problems(**overrides) -> list[str]:
    """The refusal message's bullet lines, instead of the exception."""
    with pytest.raises(InsecureConfigurationError) as excinfo:
        _prod(**overrides)
    return [
        line.strip()[2:].strip()
        for line in str(excinfo.value).splitlines()
        if line.strip().startswith("- ")
    ]


def _settings(**overrides):
    """A Settings instance that reads no .env file and no process env.

    Only declared pydantic fields may be passed: secret_key / ingest_api_key
    are resolved by the app (file-backed in development), they are not model
    fields, so the module-level singleton resolves them - this helper must
    not try to hand them in.
    """
    return config_module.Settings(_env_file=None, **overrides)


# ---------------------------------------------------------------------------
# 1. the production posture that MUST be accepted
# ---------------------------------------------------------------------------


def test_production_accepts_a_fully_specified_origin_set():
    _prod()  # must not raise


def test_the_policy_is_production_only():
    """Development keeps localhost; that is the whole point of a dev default."""
    enforce_settings(
        **{
            **_PROD_BASE,
            "environment": "development",
            "frontend_url": "http://localhost:3000",
            "oauth_redirect_uri": "http://localhost:8000/api/integrations/google/callback",
            "cors_origins": ["http://localhost:3000"],
            "oauth_cookie_secure": False,
        }
    )


def test_callers_that_validate_a_subset_skip_the_url_checks():
    """The URL arguments are optional so the gate composes: an existing
    caller that only cares about secrets still works unchanged. The
    import-time call in this module always supplies them, which is what
    makes a real production boot strict.
    """
    enforce_settings(
        environment="production",
        secret_key=GOOD_SECRET,
        ingest_api_key=GOOD_SECRET,
        auth_password=GOOD_PASSPHRASE,
        database_url=GOOD_DB_URL,
        whatsapp_provider="none",
    )


# ---------------------------------------------------------------------------
# 2. what production must refuse
# ---------------------------------------------------------------------------


def test_production_refuses_a_localhost_frontend_url():
    problems = _problems(frontend_url="http://localhost:3000")
    assert any(p.startswith("FRONTEND_URL") for p in problems), problems


def test_production_refuses_a_localhost_redirect_uri():
    problems = _problems(oauth_redirect_uri="http://localhost:8000/api/integrations/google/callback")
    assert any(p.startswith("OAUTH_REDIRECT_URI") for p in problems), problems


@pytest.mark.parametrize(
    "value",
    ["http://localhost:3000", "http://127.0.0.1:3000", "http://0.0.0.0:3000"],
)
def test_production_refuses_every_local_host_spelling(value):
    problems = _problems(frontend_url=value)
    assert any("only works on this machine" in p for p in problems), problems


def test_production_refuses_localhost_cors_origins():
    problems = _problems(cors_origins=["http://localhost:3000"])
    assert any(p.startswith("CORS_ORIGINS") for p in problems), problems


def test_production_refuses_an_empty_cors_list():
    problems = _problems(cors_origins=[])
    assert any("CORS_ORIGINS is empty" in p for p in problems), problems


def test_production_refuses_a_cors_origin_with_a_path():
    problems = _problems(cors_origins=[f"{PROD_FRONTEND}/app"])
    assert any("origin only" in p for p in problems), problems


def test_production_refuses_a_cors_origin_that_is_not_a_url():
    problems = _problems(cors_origins=["example-internal"])
    assert any("not an absolute http(s) origin" in p for p in problems), problems


def test_production_refuses_an_explicitly_insecure_oauth_cookie():
    """oauth_cookie_secure is forced on in production anyway, so an explicit
    `false` can never take effect - refusing to start is the only way to
    surface the contradiction rather than logging past it every boot."""
    problems = _problems(oauth_cookie_secure=False)
    assert any("OAUTH_COOKIE_SECURE" in p for p in problems), problems


def test_production_refuses_a_redirect_uri_with_embedded_credentials():
    problems = _problems(oauth_redirect_uri="https://us3r:Sup3rS3cr3t@db.example.internal/cb")
    assert any("credentials" in p for p in problems), problems


def test_every_refusal_names_the_variable():
    """The operator has to be able to tell WHICH setting to fix."""
    for kwargs in (
        dict(frontend_url="http://localhost:3000"),
        dict(oauth_redirect_uri="http://localhost:8000/cb"),
        dict(cors_origins=["http://localhost:3000"]),
        dict(oauth_cookie_secure=False),
    ):
        problems = _problems(**kwargs)
        assert problems, kwargs
        assert any(
            name in " ".join(problems)
            for name in ("FRONTEND_URL", "OAUTH_REDIRECT_URI", "CORS_ORIGINS", "OAUTH_COOKIE_SECURE")
        ), problems


# ---------------------------------------------------------------------------
# 3. messages never echo the value
# ---------------------------------------------------------------------------


def test_no_refusal_message_contains_a_query_string_token():
    value = (
        "http://localhost:8000/api/integrations/google/callback"
        "?state=SUPERSECRETTOKEN123"
    )
    problems = _problems(oauth_redirect_uri=value)
    assert problems
    assert "SUPERSECRETTOKEN123" not in " ".join(problems)


def test_no_refusal_message_contains_embedded_credentials():
    value = "https://us3r:Sup3rS3cr3t@db.example.internal/api/integrations/google/callback"
    problems = _problems(oauth_redirect_uri=value)
    assert problems
    joined = " ".join(problems)
    assert "Sup3rS3cr3t" not in joined
    assert "us3r" not in joined


def test_origin_problem_messages_name_the_variable_only():
    msg = config_module.origin_problem(
        "FRONTEND_URL", "ftp://somehost.internal/path", reject_local=True
    )
    assert msg.startswith("FRONTEND_URL")
    assert "somehost.internal" not in msg


# ---------------------------------------------------------------------------
# 4. CORS_ORIGINS spellings
# ---------------------------------------------------------------------------


def test_cors_origins_accepts_a_comma_separated_list():
    settings = _settings(
        CORS_ORIGINS="https://a.example.internal, https://b.example.internal"
    )
    assert settings.CORS_ORIGINS == [
        "https://a.example.internal",
        "https://b.example.internal",
    ]


def test_cors_origins_accepts_a_json_array():
    settings = _settings(CORS_ORIGINS='["https://a.example.internal"]')
    assert settings.CORS_ORIGINS == ["https://a.example.internal"]


def test_cors_origins_comma_spelling_reads_from_a_real_environment(monkeypatch):
    """D5 runtime finding: the production docker-compose passes CORS_ORIGINS as
    a plain env value. pydantic-settings JSON-decodes complex fields read from
    the environment (json.loads) and aborted BEFORE the field validator could
    run, so a valid comma-separated origin list crashed the process at import.
    The tolerant env source must hand the raw string to the validator."""
    monkeypatch.setenv(
        "CORS_ORIGINS", "https://a.example.internal,https://b.example.internal"
    )
    settings = config_module.Settings(_env_file=None)
    assert settings.CORS_ORIGINS == [
        "https://a.example.internal",
        "https://b.example.internal",
    ]


def test_cors_origins_json_spelling_reads_from_a_real_environment(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", '["https://a.example.internal"]')
    settings = config_module.Settings(_env_file=None)
    assert settings.CORS_ORIGINS == ["https://a.example.internal"]


def test_cors_origins_malformed_json_fails_with_a_named_error():
    with pytest.raises(ValidationError) as excinfo:
        _settings(CORS_ORIGINS="{not valid json")
    assert "CORS_ORIGINS" in str(excinfo.value)


def test_cors_origins_json_that_is_not_an_array_fails():
    with pytest.raises(ValidationError) as excinfo:
        _settings(CORS_ORIGINS='{"origin": "https://a.example.internal"}')
    assert "CORS_ORIGINS" in str(excinfo.value)


def test_cors_origins_empty_string_means_empty_list_not_a_crash():
    assert _settings(CORS_ORIGINS="").CORS_ORIGINS == []


def test_the_validation_helpers_agree_on_a_bare_origin():
    assert config_module.cors_origins_problems(
        ["https://a.example.internal:8443"], reject_local=True
    ) == []
    assert config_module.cors_origins_problems(
        ["https://a.example.internal:8443"], reject_local=False
    ) == []


# ---------------------------------------------------------------------------
# 5. the boolean the cookie check depends on
# ---------------------------------------------------------------------------


def test_configured_bool_reads_the_operator_value(monkeypatch):
    monkeypatch.setenv("OAUTH_COOKIE_SECURE", "false")
    assert config_module._configured_bool("OAUTH_COOKIE_SECURE") is False
    monkeypatch.setenv("OAUTH_COOKIE_SECURE", "true")
    assert config_module._configured_bool("OAUTH_COOKIE_SECURE") is True
    monkeypatch.delenv("OAUTH_COOKIE_SECURE", raising=False)
    assert config_module._configured_bool("OAUTH_COOKIE_SECURE") is None
    assert config_module._configured_bool("NO_SUCH_VARIABLE_AT_ALL") is None
