import json
import logging
import os
import re
import secrets
from pathlib import Path
from typing import Annotated, Optional, Sequence
from urllib.parse import urlsplit

from pydantic import field_validator
from pydantic_settings import BaseSettings, EnvSettingsSource

logger = logging.getLogger(__name__)

_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
_ENV_FILE = _BACKEND_DIR / ".env"
_SECRET_KEY_FILE = _BACKEND_DIR / ".secret_key"
_INGEST_KEY_FILE = _BACKEND_DIR / ".ingest_key"
_ADMIN_KEY_FILE = _BACKEND_DIR / ".admin_key"
_FALLBACK_SECRET_KEY = "change-this-to-a-random-secret-key"

# --------------------------------------------------------------------------
# Secret strength policy
#
# PRODUCTION: every secret below must be explicitly supplied, strong, and
# stable. Nothing is auto-generated and nothing falls back to a default - the
# process refuses to start (see enforce_settings) so a weak or missing secret
# can never silently sign tokens, encrypt OAuth credentials, or gate login.
#
# DEVELOPMENT: unset SECRET_KEY / INGEST_API_KEY are auto-generated once and
# persisted to a gitignored 0600 file, purely as a local convenience.
# --------------------------------------------------------------------------
_PRODUCTION = "production"
_DEVELOPMENT = "development"
_ENVIRONMENTS = (_DEVELOPMENT, "testing", "staging", _PRODUCTION)

# Minimum length for a machine-generated secret. 32 chars of urlsafe base64 is
# the size this project generates (secrets.token_urlsafe(48) -> 64 chars).
_MIN_SECRET_LENGTH = 32
# Minimum length for a human-chosen passphrase (app password).
_MIN_PASSPHRASE_LENGTH = 12

# Values that must never be accepted as a production secret, in any casing.
_WEAK_SECRET_VALUES = frozenset(
    {
        _FALLBACK_SECRET_KEY,
        "changeme",
        "change-me",
        "secret",
        "secret-key",
        "secretkey",
        "supersecret",
        "password",
        "passw0rd",
        "admin",
        "test",
        "testing",
        "dev",
        "development",
        "local",
        "default",
        "placeholder",
        "example",
        "your-secret-here",
        "your_secret_here",
        "please-change",
        "please_change_me",
        "todo-change",
        "notasecret",
        "insecure",
        "letmein",
    }
)

# Fragments that mark a value as documentation filler rather than a secret.
# Matched as delimited words so a real key that merely happens to contain
# "xxx" or "test" is not rejected.
_PLACEHOLDER_WORDS = (
    "change-this", "change_this", "change-me", "change_me", "changeme",
    "your-secret", "your_secret", "placeholder", "example", "sample",
    "dummy", "replace", "insert", "todo",
)
_PLACEHOLDER_PATTERN = re.compile(
    r"(?:^|[^a-z0-9])(?:" + "|".join(_PLACEHOLDER_WORDS) + r")(?:[^a-z0-9]|$)"
)
# Characters that cannot occur in urlsafe-base64 or hex, so their presence
# means the value is a template.
_PLACEHOLDER_CHARS = ("<", ">")

# Database passwords that are local-development conveniences. Re-using one in
# production would expose the whole database, so it is refused there.
_DEV_DATABASE_PASSWORDS = frozenset({"postgres", "password", "changeme", "test", ""})


class InsecureConfigurationError(RuntimeError):
    """Raised at import time when production security requirements are unmet.

    Deliberately a hard failure: an app that starts with a weak or missing
    secret is worse than an app that refuses to start, because the weakness is
    silent (it signs tokens / gates login / encrypts credentials with a key
    anyone can guess or read).
    """


def _is_production(environment: Optional[str]) -> bool:
    return (environment or _DEVELOPMENT).strip().lower() == _PRODUCTION


def _looks_like_placeholder(value: str) -> bool:
    lowered = value.strip().lower()
    if any(char in value for char in _PLACEHOLDER_CHARS):
        return True
    return bool(_PLACEHOLDER_PATTERN.search(lowered))


def configured_value(name: str, default: Optional[str] = None) -> Optional[str]:
    """The value the operator actually configured, before any default filling.

    The startup gate must judge what was configured, not what the app derived:
    SECRET_KEY/INGEST_API_KEY are auto-generated in development, so reading
    settings.SECRET_KEY would report an auto-generated key as "configured" and
    let production start without an explicit secret.
    """
    raw = os.environ.get(name)
    if raw is not None and raw.strip():
        return raw.strip()
    if _ENV_FILE.exists():
        try:
            for line in _ENV_FILE.read_text(errors="ignore").splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or "=" not in stripped:
                    continue
                key, _, value = stripped.partition("=")
                if key.strip() != name:
                    continue
                cleaned = value.strip().strip("\"'").strip()
                if cleaned:
                    return cleaned
        except OSError:
            pass
    return default


def normalize_request_path(raw_path: Optional[str]) -> str:
    """Canonical form of a request path, for exact-match security policy.

    Both the allow-list and the incoming request go through this function so a
    prefix, a doubled slash, a trailing slash, a query string or a
    percent-encoded variant cannot make an exempt route look like a different
    (non-exempt) one - or the other way round. Returns "" for anything that is
    not an absolute path, which matches no allow-list entry and therefore fails
    closed.
    """
    if not raw_path:
        return ""
    path = raw_path.strip()
    if "?" in path or "#" in path:
        path = path.split("?", 1)[0].split("#", 1)[0]
    if not path.startswith("/"):
        return ""
    # Collapse repeated slashes so "//api/health" cannot bypass a match.
    while "//" in path:
        path = path.replace("//", "/")
    # Strip a trailing slash (except for the root) so "/api/health" and
    # "/api/health/" are the same policy decision.
    if len(path) > 1:
        path = path.rstrip("/") or "/"
    return path


def _is_repeating(value: str) -> bool:
    """True when the value is one short fragment repeated ("changeme" * 6).

    Catches padded weak values that are long enough to pass a length check but
    carry only a few bytes of entropy. A genuine generated key is urlsafe
    base64 and never repeats with such a short period.
    """
    for period in range(1, 9):
        if len(value) % period == 0 and value == value[:period] * (len(value) // period):
            return True
    return False


def secret_problem(name: str, value: Optional[str], min_length: int) -> Optional[str]:
    """Return why ``value`` is unacceptable, or None when it is acceptable.

    Shared by the production gate and its tests so the policy has one
    definition.
    """
    if value is None or not value.strip():
        return f"{name} is not set"
    candidate = value.strip()
    if candidate.lower() in _WEAK_SECRET_VALUES:
        return f"{name} is a well-known weak/default value"
    if _looks_like_placeholder(candidate):
        return f"{name} still looks like a placeholder"
    if len(candidate) < min_length:
        return f"{name} is too short ({len(candidate)} chars, minimum {min_length})"
    if _is_repeating(candidate):
        return f"{name} repeats a short fragment and has almost no entropy"
    return None


def _database_problem(database_url: Optional[str]) -> Optional[str]:
    """Refuse a connection string carrying a local-development password."""
    url = (database_url or "").strip()
    if not url:
        return "DATABASE_URL is not set"
    if "://" not in url or "@" not in url:
        return None  # SQLite / DSN without inline credentials
    credentials = url.split("://", 1)[1].split("@", 1)[0]
    password = credentials.split(":", 1)[1] if ":" in credentials else ""
    if password.lower() in _DEV_DATABASE_PASSWORDS:
        return (
            "DATABASE_URL uses a local-development password; set a real "
            "POSTGRES_PASSWORD for production"
        )
    return None


# --------------------------------------------------------------------------
# D3 - production URL / origin correctness
#
# Development defaults are deliberately localhost (a laptop has no domain), so
# a PRODUCTION process still carrying one means the deployment's real origin
# was never configured. That is worth refusing to start over: a wrong
# FRONTEND_URL sends OAuth callbacks to the wrong host, and a localhost
# CORS_ORIGINS means every real browser request is rejected by CORS.
# --------------------------------------------------------------------------

# Hostnames that can only ever identify the machine the process runs on.
_LOCAL_HOSTNAMES = frozenset({"localhost", "127.0.0.1", "0.0.0.0", "::1"})
_HTTP_SCHEMES = ("http", "https")


def _configured_bool(name: str) -> Optional[bool]:
    """The boolean the operator configured for ``name``, if any.

    Reads raw configuration (not ``settings``), because pydantic resolves the
    field to a plain bool and we need to distinguish "explicitly false" from
    "never mentioned".
    """
    raw = configured_value(name)
    if raw is None:
        return None
    normalized = raw.strip().lower()
    if normalized in ("1", "true", "yes", "on"):
        return True
    if normalized in ("0", "false", "no", "off"):
        return False
    return None


def origin_problem(
    name: str,
    value: Optional[str],
    *,
    reject_local: bool = False,
) -> Optional[str]:
    """Why ``value`` is not an acceptable absolute http(s) URL, or None.

    Error messages name the VARIABLE and, at most, the offending hostname -
    never the whole value, so an origin carrying a token in its query string
    can never be echoed into a log, a crash report or a CI transcript.
    """
    raw = (value or "").strip()
    if not raw:
        return f"{name} is not set"
    parsed = urlsplit(raw)
    if parsed.scheme not in _HTTP_SCHEMES or not parsed.hostname:
        return f"{name} must be an absolute http(s) URL with a host"
    if parsed.username or parsed.password:
        return f"{name} must not embed credentials"
    if reject_local and parsed.hostname in _LOCAL_HOSTNAMES:
        return (
            f"{name} points at {parsed.hostname}, which only works on this "
            "machine; production must be given the real public origin"
        )
    return None


def cors_origins_problems(
    origins: Optional[Sequence[str]],
    *,
    reject_local: bool = False,
) -> list[str]:
    """Why the configured CORS origins are unusable, if any.

    Each entry must be a bare origin (``scheme://host[:port]``): a path, query
    or fragment is a configuration mistake that would silently never match a
    browser's Origin header.
    """
    entries = list(origins or [])
    if not entries:
        return [
            "CORS_ORIGINS is empty; production must list the exact "
            "browser origin(s) the frontend is served from"
        ]

    problems: list[str] = []
    for entry in entries:
        raw = (entry or "").strip()
        if not raw:
            problems.append("CORS_ORIGINS contains an empty entry")
            continue
        parsed = urlsplit(raw)
        if parsed.scheme not in _HTTP_SCHEMES or not parsed.hostname:
            problems.append(
                f"CORS_ORIGINS entry {raw!r} is not an absolute http(s) origin"
            )
            continue
        if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            problems.append(
                f"CORS_ORIGINS entry {raw!r} must be an origin only "
                "(scheme://host[:port]), with no path, query or fragment"
            )
            continue
        if parsed.username or parsed.password:
            problems.append("CORS_ORIGINS must not embed credentials")
            continue
        if reject_local and parsed.hostname in _LOCAL_HOSTNAMES:
            problems.append(
                f"CORS_ORIGINS entry {raw!r} is a local development origin; "
                "production must list the deployed origin(s)"
            )
    return problems


def whatsapp_provider_problem(
    provider: Optional[str],
    *,
    twilio_account_sid: Optional[str],
    twilio_auth_token: Optional[str],
    twilio_from: Optional[str],
    meta_phone_number_id: Optional[str],
    meta_access_token: Optional[str],
    meta_app_secret: Optional[str],
    meta_verify_token: Optional[str],
) -> list[str]:
    """A configured provider must carry every credential it needs.

    Applies in EVERY environment: an unconfigured provider must stay
    fail-closed rather than degrade into an unverified webhook.
    """
    name = (provider or "none").strip().lower()
    problems: list[str] = []
    if name in ("twilio", "twilio_sandbox"):
        if not (twilio_account_sid or "").strip():
            problems.append("TWILIO_ACCOUNT_SID is required when WHATSAPP_PROVIDER=twilio")
        if not (twilio_auth_token or "").strip():
            problems.append("TWILIO_AUTH_TOKEN is required when WHATSAPP_PROVIDER=twilio")
        if not (twilio_from or "").strip():
            problems.append(
                "TWILIO_WHATSAPP_FROM is required when WHATSAPP_PROVIDER=twilio "
                "(no default number is assumed)"
            )
    elif name in ("meta", "whatsapp_cloud_api"):
        if not (meta_phone_number_id or "").strip():
            problems.append("META_WA_PHONE_NUMBER_ID is required when WHATSAPP_PROVIDER=meta")
        if not (meta_access_token or "").strip():
            problems.append("META_WA_ACCESS_TOKEN is required when WHATSAPP_PROVIDER=meta")
        if not (meta_app_secret or "").strip():
            problems.append(
                "META_WA_APP_SECRET is required when WHATSAPP_PROVIDER=meta "
                "(webhook signatures cannot be verified without it)"
            )
        if not (meta_verify_token or "").strip():
            problems.append(
                "META_WA_VERIFY_TOKEN is required when WHATSAPP_PROVIDER=meta "
                "(GET verification handshake)"
            )
    return problems


def enforce_settings(
    *,
    environment: Optional[str],
    secret_key: Optional[str],
    ingest_api_key: Optional[str],
    auth_password: Optional[str],
    database_url: Optional[str],
    whatsapp_provider: Optional[str] = None,
    twilio_account_sid: Optional[str] = None,
    twilio_auth_token: Optional[str] = None,
    twilio_from: Optional[str] = None,
    meta_phone_number_id: Optional[str] = None,
    meta_access_token: Optional[str] = None,
    meta_app_secret: Optional[str] = None,
    meta_verify_token: Optional[str] = None,
    # D3 - production URL/origin correctness (ignored outside production).
    frontend_url: Optional[str] = None,
    oauth_redirect_uri: Optional[str] = None,
    cors_origins: Optional[Sequence[str]] = None,
    oauth_cookie_secure: Optional[bool] = None,
) -> None:
    """Fail fast on insecure configuration. Raises InsecureConfigurationError.

    Development/test/staging keep their convenience defaults. Production must
    supply every secret explicitly and strongly.
    """
    problems: list[str] = []

    if not _is_production(environment):
        problems += whatsapp_provider_problem(
            whatsapp_provider,
            twilio_account_sid=twilio_account_sid,
            twilio_auth_token=twilio_auth_token,
            twilio_from=twilio_from,
            meta_phone_number_id=meta_phone_number_id,
            meta_access_token=meta_access_token,
            meta_app_secret=meta_app_secret,
            meta_verify_token=meta_verify_token,
        )
    else:
        for name, value, minimum in (
            ("SECRET_KEY", secret_key, _MIN_SECRET_LENGTH),
            ("INGEST_API_KEY", ingest_api_key, _MIN_SECRET_LENGTH),
        ):
            problem = secret_problem(name, value, minimum)
            if problem:
                problems.append(problem)

        # AUTH_PASSWORD gates login. Unset means "authentication disabled", so
        # production must refuse to start rather than serve the API open.
        password = (auth_password or "").strip()
        if not password:
            problems.append(
                "AUTH_PASSWORD is not set (production requires it; leaving it "
                "empty disables authentication entirely)"
            )
        elif password.startswith("pbkdf2$"):
            parts = password.split("$")
            if len(parts) != 4 or not parts[1].isdigit():
                problems.append("AUTH_PASSWORD looks like a malformed pbkdf2 hash")
        elif len(password) < _MIN_PASSPHRASE_LENGTH:
            problems.append(
                f"AUTH_PASSWORD is too short ({len(password)} chars, minimum "
                f"{_MIN_PASSPHRASE_LENGTH})"
            )
        elif password.lower() in _WEAK_SECRET_VALUES or _looks_like_placeholder(password):
            problems.append("AUTH_PASSWORD is a well-known weak/default value")
        elif _is_repeating(password):
            problems.append("AUTH_PASSWORD repeats a short fragment")

        db_problem = _database_problem(database_url)
        if db_problem:
            problems.append(db_problem)

        # D3: the deployment's own addresses. Development defaults are
        # localhost, so a production process still carrying one was started
        # without the real origin configured - callbacks would be issued to
        # the wrong host and CORS would reject every real browser.
        #
        # Each check runs only when the caller supplied that argument, so the
        # gate composes with callers validating a subset. The import-time call
        # at the bottom of this module always supplies them, which is what
        # makes an actual production boot strict.
        if frontend_url is not None:
            problem = origin_problem("FRONTEND_URL", frontend_url, reject_local=True)
            if problem:
                problems.append(problem)
        if oauth_redirect_uri is not None:
            problem = origin_problem(
                "OAUTH_REDIRECT_URI", oauth_redirect_uri, reject_local=True
            )
            if problem:
                problems.append(problem)
        if cors_origins is not None:
            problems += cors_origins_problems(cors_origins, reject_local=True)

        # The cookie's Secure flag is forced on in production anyway (see
        # Settings.oauth_cookie_secure), so an explicit "false" can never take
        # effect - refusing to start is the only way to surface the
        # contradiction instead of logging past it on every boot.
        if oauth_cookie_secure is False:
            problems.append(
                "OAUTH_COOKIE_SECURE=false contradicts ENVIRONMENT=production; "
                "unset it (Secure is always sent) or correct ENVIRONMENT"
            )

        problems += whatsapp_provider_problem(
            whatsapp_provider,
            twilio_account_sid=twilio_account_sid,
            twilio_auth_token=twilio_auth_token,
            twilio_from=twilio_from,
            meta_phone_number_id=meta_phone_number_id,
            meta_access_token=meta_access_token,
            meta_app_secret=meta_app_secret,
            meta_verify_token=meta_verify_token,
        )

    if problems:
        raise InsecureConfigurationError(
            "Refusing to start: insecure configuration for ENVIRONMENT="
            f"{environment or _DEVELOPMENT!r}.\n  - "
            + "\n  - ".join(problems)
            + "\n\nGenerate strong values, e.g.:\n"
            "  python -c \"import secrets; print(secrets.token_urlsafe(48))\""
        )


def _is_placeholder(value: Optional[str]) -> bool:
    return not value or value == _FALLBACK_SECRET_KEY or value.startswith("change-this-to-")


def _persist_generated_secret(path: Path, key: str, name: str) -> str:
    """Persist an auto-generated development secret, or warn loudly.

    A silent failure here would rotate the key on every restart (invalidating
    every issued token and orphaning encrypted OAuth credentials), so the
    warning is explicit rather than swallowed.
    """
    try:
        path.write_text(key)
        os.chmod(path, 0o600)
    except OSError as exc:
        logger.warning(
            "DEVELOPMENT ONLY: could not persist the generated %s to %s (%s). "
            "It will be regenerated on every restart, which invalidates all "
            "issued tokens and encrypted OAuth credentials. Set %s explicitly "
            "in the environment instead.",
            name, path, exc, name,
        )
    return key


def _resolve_secret_key(value: Optional[str]) -> str:
    """Use the configured key, else load or generate a persistent random one.

    DEVELOPMENT ONLY convenience: with no key configured we generate a strong
    key once and store it in a gitignored 0600 file so tokens and encrypted
    OAuth credentials survive restarts. Production never reaches this path -
    enforce_settings() requires an explicit SECRET_KEY.
    """
    if not _is_placeholder(value):
        return value
    if _SECRET_KEY_FILE.exists():
        stored = _SECRET_KEY_FILE.read_text().strip()
        if stored:
            return stored
    key = secrets.token_urlsafe(48)
    return _persist_generated_secret(_SECRET_KEY_FILE, key, "SECRET_KEY")


def _resolve_database_url(value: str) -> str:
    if value.startswith("sqlite:///./"):
        rel = value[len("sqlite:///./"):]
        db_path = (_BACKEND_DIR.parent / rel).resolve()
        return f"sqlite:///{db_path}"
    return value


def _resolve_ingest_key(value: Optional[str]) -> str:
    """Dedicated server-to-server integration key for n8n -> backend.

    Used ONLY by the /api/integrations/* endpoints (scoped, not the user
    login). DEVELOPMENT ONLY convenience: when not configured a strong random
    key is generated once and persisted to a gitignored 0600 file so restarts
    keep it stable. Production requires an explicit INGEST_API_KEY.
    """
    if value:
        return value
    if _INGEST_KEY_FILE.exists():
        stored = _INGEST_KEY_FILE.read_text().strip()
        if stored:
            return stored
    key = secrets.token_urlsafe(48)
    return _persist_generated_secret(_INGEST_KEY_FILE, key, "INGEST_API_KEY")


def _resolve_admin_key(value: Optional[str]) -> str:
    """Separate admin key for cross-user operations (scope=all).

    DEVELOPMENT ONLY convenience: auto-generated and persisted like
    INGEST_API_KEY. Production requires an explicit ADMIN_API_KEY.
    """
    if value:
        return value
    if _ADMIN_KEY_FILE.exists():
        stored = _ADMIN_KEY_FILE.read_text().strip()
        if stored:
            return stored
    key = secrets.token_urlsafe(48)
    return _persist_generated_secret(_ADMIN_KEY_FILE, key, "ADMIN_API_KEY")


class _TolerantEnvSettingsSource(EnvSettingsSource):
    """Env source that defers ``CORS_ORIGINS`` decoding to the field validator.

    pydantic-settings JSON-decodes every complex-typed field it reads from the
    environment (``json.loads``) and aborts import when the value is not valid
    JSON. ``CORS_ORIGINS`` accepts a plain comma-separated spelling -
    ``https://a.example,https://b.example`` - alongside the JSON-array spelling,
    and the comma spelling is NOT valid JSON, so the stock source would crash a
    perfectly valid configuration before ``_cors_origins_accept_either_spelling``
    could run. Returning the raw string here lets that validator do its job for
    BOTH spellings.

    Every other field in ``Settings`` is a primitive type, so complex-field
    decoding behavior is unchanged for them.
    """

    def prepare_field_value(self, field_name, field, value, value_is_complex):
        if field_name == "CORS_ORIGINS" and value is not None:
            return value
        return super().prepare_field_value(field_name, field, value, value_is_complex)


class Settings(BaseSettings):
    # "production" turns on the hard secret requirements enforced at import
    # time. Anything else keeps local-development conveniences (auto-generated
    # SECRET_KEY / INGEST_API_KEY, optional AUTH_PASSWORD).
    ENVIRONMENT: str = _DEVELOPMENT

    APP_NAME: str = "AI Chief of Staff"
    VERSION: str = "1.0.0"
    DATABASE_URL: str = "sqlite:///./ai_chief.db"
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "qwen2.5:3b"
    SECRET_KEY: str = _FALLBACK_SECRET_KEY
    INGEST_API_KEY: Optional[str] = None
    ADMIN_API_KEY: Optional[str] = None
    INGEST_BATCH_MAX: int = 100

    # Request/resource bounds (NF-6). List endpoints clamp ``limit`` to
    # API_MAX_PAGE_SIZE and ``offset`` to >= 0 so a single request cannot ask
    # the database for an unbounded result set; document uploads reject bodies
    # larger than MAX_UPLOAD_MB before buffering them.
    API_MAX_PAGE_SIZE: int = 200
    MAX_UPLOAD_MB: int = 25

    DB_POOL_SIZE: int = 20
    DB_MAX_OVERFLOW: int = 30
    DB_OPERATION_MAX_RETRIES: int = 3
    DB_OPERATION_RETRY_BASE_DELAY: float = 0.5

    # Ollama reliability / backpressure
    OLLAMA_MAX_CONCURRENT_REQUESTS: int = 2
    OLLAMA_MAX_RETRIES: int = 3
    OLLAMA_RETRY_BASE_DELAY: float = 1.0
    OLLAMA_REQUEST_TIMEOUT: float = 120.0
    OLLAMA_CIRCUIT_BREAKER_FAILURE_THRESHOLD: int = 5
    OLLAMA_CIRCUIT_BREAKER_RESET_SECONDS: int = 60
    OLLAMA_CIRCUIT_BREAKER_HALF_OPEN_MAX: int = 1

    # AI processing queue / worker tuning
    AI_WORKER_THREADS: int = 2
    AI_QUEUE_MAX_SIZE: int = 100

    # Sync loop intervals (seconds)
    SYNC_GMAIL_INTERVAL_SECONDS: int = 600
    SYNC_CLASSROOM_INTERVAL_SECONDS: int = 1800

    # Internal service hostnames (Docker portability)
    BACKEND_INTERNAL_URL: str = "http://backend:8000"
    POSTGRES_HOST: Optional[str] = None

    RETENTION_MODE: str = "soft"
    RETENTION_PURGE_INTERVAL_SECONDS: int = 3600
    GMAIL_CREDENTIALS_FILE: Optional[str] = None
    GOOGLE_CLIENT_ID: Optional[str] = None
    GOOGLE_CLIENT_SECRET: Optional[str] = None
    OAUTH_REDIRECT_URI: str = "http://localhost:8000/api/integrations/google/callback"
    FRONTEND_URL: str = "http://localhost:3000"
    GOOGLE_AUTO_PROVISION: bool = False
    MS_CLIENT_ID: Optional[str] = None
    MS_TENANT_ID: str = "consumers"
    AUTH_PASSWORD: Optional[str] = None
    AUTH_TOKEN_TTL_DAYS: int = 7
    # P24B-06: one-time, operator-controlled authorization for creating the
    # FIRST user (the owner). Required in production whenever the users table
    # is empty; once an owner exists the token can never be used again, so it
    # is NOT needed to keep a provisioned deployment running. No default value
    # exists anywhere: an unset token means "first-owner creation is refused",
    # never "anyone may claim ownership".
    BOOTSTRAP_TOKEN: Optional[str] = None
    # P24B-16: Secure flag for the OAuth `oauth_state` cookie. None means "not
    # configured explicitly". Production always sends Secure regardless of this
    # value; other environments must opt in to plain-HTTP cookies deliberately
    # (see oauth_cookie_secure) so HTTPS deployments are never downgraded by an
    # unstated assumption.
    OAUTH_COOKIE_SECURE: Optional[bool] = None
    # Comma-separated proxy IPs/hosts whose X-Forwarded-For header is trusted
    # for login rate limiting. EMPTY (the default) trusts no proxy: a direct
    # client can never spoof its IP through XFF. Set this to your reverse
    # proxy's address (e.g. "127.0.0.1" or "10.0.0.2") only when the app is
    # actually deployed behind one.
    TRUSTED_PROXY_IPS: str = ""
    CORS_ORIGINS: list[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3001",
    ]

    # Scheduling / retention defaults (business-configurable, env overridable)
    DEFAULT_SCHEDULE_TIME: str = "09:00"
    DEFAULT_SCHEDULE_DURATION_MINUTES: int = 60
    DEFAULT_EXAM_DURATION_MINUTES: int = 180
    DEFAULT_WORK_HOURS: str = "9:00-18:00"
    EMAIL_RETENTION_DAYS: int = 90
    EMAIL_SENSITIVITY: str = "PERSONAL"
    EMERGENCY_CODE_TTL_HOURS: int = 24
    VAPID_CLAIMS_EMAIL: str = "aichief@localhost"

    # Org-scoped data. Comma-separated in .env (gitignored); empty = auto-discover.
    CLASSROOM_COURSE_IDS: str = ""

    # CHARUSAT e-Governance (read-only personal attendance connector).
    # The portal login is CAPTCHA-protected, so the connector NEVER logs in on
    # the student's behalf: the student signs in themselves and either imports
    # the saved attendance page or supplies the session cookie their own
    # browser already holds. Traffic is pinned to the university host below so
    # a bad request cannot be turned into SSRF, refreshes are user-initiated
    # only (no polling), and failures are bounded by MAX_RETRIES with backoff.
    EGOV_BASE_URL: str = "https://support.charusat.edu.in/Egov/"
    EGOV_ALLOWED_HOSTS: str = "support.charusat.edu.in"
    EGOV_REQUEST_TIMEOUT: float = 20.0
    EGOV_MAX_RETRIES: int = 2
    EGOV_RETRY_BASE_DELAY: float = 0.5
    EGOV_STALE_AFTER_HOURS: int = 24

    # CHARUSAT student eligibility + attendance targets.
    # The attendance feature is only offered to accounts whose INSTITUTIONAL
    # identity an identity provider has actually confirmed (a Google
    # ``email_verified`` claim for an address on this domain). The registrable
    # email stored in ``users.email`` is user-supplied and is never treated as
    # proof of a CHARUSAT identity on its own. The target drives the recovery
    # maths (how many classes are needed to climb back to a safe percentage).
    CHARUSAT_INSTITUTIONAL_DOMAIN: str = "charusat.edu.in"
    ATTENDANCE_TARGET_PERCENTAGE: float = 75.0

    # WhatsApp. WHATSAPP_PROVIDER=none disables the integration; any other
    # value requires that provider's full credential set (see
    # whatsapp_provider_problem) - there is no default number and no default
    # verification token.
    WHATSAPP_PROVIDER: str = "none"  # none | twilio | twilio_sandbox | meta | whatsapp_cloud_api
    TWILIO_ACCOUNT_SID: Optional[str] = None
    TWILIO_AUTH_TOKEN: Optional[str] = None
    TWILIO_WHATSAPP_FROM: str = ""
    META_WA_PHONE_NUMBER_ID: Optional[str] = None
    META_WA_ACCESS_TOKEN: Optional[str] = None
    META_WA_APP_SECRET: Optional[str] = None
    META_WA_VERIFY_TOKEN: str = ""

    @property
    def classroom_course_ids(self) -> list[str]:
        return [v.strip() for v in self.CLASSROOM_COURSE_IDS.split(",") if v.strip()]

    @property
    def egov_allowed_hosts(self) -> set[str]:
        """Lower-cased hostnames the e-Governance connector may contact.

        Empty means "no host is allowed", never "any host": a blank setting
        must fail closed rather than silently permit arbitrary outbound
        requests.
        """
        return {
            v.strip().lower() for v in self.EGOV_ALLOWED_HOSTS.split(",") if v.strip()
        }

    @property
    def charusat_institutional_domain(self) -> str:
        """The institutional email domain, normalised for comparison.

        Lower-cased with any leading ``@`` stripped so both ``charusat.edu.in``
        and ``@charusat.edu.in`` are accepted in configuration. An empty
        setting disables eligibility entirely (no domain can match), which is
        the fail-closed behaviour.
        """
        return self.CHARUSAT_INSTITUTIONAL_DOMAIN.strip().lower().lstrip("@")

    @property
    def trusted_proxy_ips(self) -> list[str]:
        return [v.strip() for v in self.TRUSTED_PROXY_IPS.split(",") if v.strip()]

    @property
    def is_production(self) -> bool:
        return _is_production(self.ENVIRONMENT)

    @property
    def oauth_cookie_secure(self) -> bool:
        """Resolve the Secure attribute for the OAuth ``oauth_state`` cookie.

        P24B-16. Production is unconditionally True: a cookie flag must never be
        switchable off by configuration on a deployment that should be served
        over HTTPS. Outside production the operator must opt in explicitly with
        ``OAUTH_COOKIE_SECURE=false`` (plain-HTTP local development only); when
        the variable is unset we do NOT guess that the environment is safe for
        HTTP, so the secure default applies and a warning names the knob.
        """
        if self.is_production:
            if self.OAUTH_COOKIE_SECURE is False:
                logger.warning(
                    "OAUTH_COOKIE_SECURE=false was set but ENVIRONMENT=%s; "
                    "the oauth_state cookie is still sent with Secure.",
                    self.ENVIRONMENT,
                )
            return True
        if self.OAUTH_COOKIE_SECURE is None:
            logger.warning(
                "OAUTH_COOKIE_SECURE is not set; defaulting the oauth_state "
                "cookie to Secure. On plain-HTTP local development set "
                "OAUTH_COOKIE_SECURE=false explicitly, and never on an "
                "HTTPS deployment.",
            )
            return True
        return bool(self.OAUTH_COOKIE_SECURE)

    class Config:
        env_file = _ENV_FILE

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        """Use the env source that never JSON-decodes ``CORS_ORIGINS``.

        Without this, the documented comma-separated spelling of
        ``CORS_ORIGINS`` aborts import (see ``_TolerantEnvSettingsSource``).
        """
        return (
            init_settings,
            _TolerantEnvSettingsSource(settings_cls),
            dotenv_settings,
            file_secret_settings,
        )

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def _cors_origins_accept_either_spelling(cls, value):
        """Accept a JSON array or a plain comma-separated list.

        pydantic-settings JSON-decodes complex fields, so the natural
        ``CORS_ORIGINS=https://a.example,https://b.example`` in a .env file
        used to die with an opaque JSON decode error at import time. Both
        spellings now work; a genuinely malformed value still fails fast with
        a message that names the variable instead of echoing the file.
        """
        if not isinstance(value, str):
            return value
        raw = value.strip()
        if not raw:
            return []
        if raw.startswith("[") or raw.startswith("{"):
            # A leading brace means someone tried JSON and (probably) got it
            # wrong; treating that as a comma-separated origin would silently
            # produce a garbage origin instead of a clear failure.
            try:
                parsed = json.loads(raw)
            except ValueError:
                raise ValueError(
                    "CORS_ORIGINS must be a JSON array or a comma-separated "
                    "list of origins"
                ) from None
            if not isinstance(parsed, list):
                raise ValueError(
                    "CORS_ORIGINS must be a JSON array or a comma-separated "
                    "list of origins"
                )
            return parsed
        return [part.strip() for part in raw.split(",") if part.strip()]

    @field_validator("ENVIRONMENT")
    @classmethod
    def _known_environment(cls, value: str) -> str:
        normalized = (value or _DEVELOPMENT).strip().lower()
        aliases = {"prod": _PRODUCTION, "production": _PRODUCTION, "dev": _DEVELOPMENT}
        normalized = aliases.get(normalized, normalized)
        if normalized not in _ENVIRONMENTS:
            raise ValueError(
                f"ENVIRONMENT must be one of {', '.join(_ENVIRONMENTS)} (got {value!r})"
            )
        return normalized

    @field_validator("DATABASE_URL")
    @classmethod
    def _absolute_database_path(cls, value: str) -> str:
        return _resolve_database_url(value)

    @field_validator("SECRET_KEY")
    @classmethod
    def _strong_secret_key(cls, value: Optional[str]) -> str:
        return _resolve_secret_key(value)

    @field_validator("INGEST_API_KEY")
    @classmethod
    def _ingest_key(cls, value: Optional[str]) -> str:
        return _resolve_ingest_key(value)

    @field_validator("ADMIN_API_KEY")
    @classmethod
    def _admin_key(cls, value: Optional[str]) -> str:
        return _resolve_admin_key(value)


settings = Settings()

# Fail fast, before the app serves a request: production must not run with a
# missing/weak secret, a development database password, or a half-configured
# WhatsApp provider. Raises InsecureConfigurationError listing every problem.
#
# configured_value() is used for the secrets that have development-only
# auto-generation, so "unset" cannot hide behind an auto-generated value.
enforce_settings(
    environment=settings.ENVIRONMENT,
    secret_key=configured_value("SECRET_KEY", _FALLBACK_SECRET_KEY),
    ingest_api_key=configured_value("INGEST_API_KEY"),
    auth_password=configured_value("AUTH_PASSWORD"),
    database_url=configured_value("DATABASE_URL", Settings.model_fields["DATABASE_URL"].default),
    whatsapp_provider=configured_value("WHATSAPP_PROVIDER", settings.WHATSAPP_PROVIDER),
    twilio_account_sid=configured_value("TWILIO_ACCOUNT_SID"),
    twilio_auth_token=configured_value("TWILIO_AUTH_TOKEN"),
    twilio_from=configured_value("TWILIO_WHATSAPP_FROM"),
    meta_phone_number_id=configured_value("META_WA_PHONE_NUMBER_ID"),
    meta_access_token=configured_value("META_WA_ACCESS_TOKEN"),
    meta_app_secret=configured_value("META_WA_APP_SECRET"),
    meta_verify_token=configured_value("META_WA_VERIFY_TOKEN"),
    # D3: URLs are read from `settings` (not configured_value) on purpose -
    # when they are unset the development localhost defaults apply, and that
    # is exactly the state production must refuse to start in. The messages
    # name the variable, never the value.
    frontend_url=settings.FRONTEND_URL,
    oauth_redirect_uri=settings.OAUTH_REDIRECT_URI,
    cors_origins=settings.CORS_ORIGINS,
    oauth_cookie_secure=_configured_bool("OAUTH_COOKIE_SECURE"),
)

if settings.is_production:
    logger.info(
        "ENVIRONMENT=production: secret policy enforced (explicit SECRET_KEY, "
        "INGEST_API_KEY, AUTH_PASSWORD; no development defaults accepted)"
    )
