"""OAuth authorization codes / state must never reach the access log.

Uvicorn's access logger writes the raw request line, which for the Google OAuth
callback includes the one-time ``code`` and the ``state`` nonce. The filter
redacts only those values on OAuth endpoint paths, keeping method/path/status and
non-sensitive query parameters for diagnostics.
"""

import logging

from app.core.logging_filters import (
    OAuthAccessLogFilter,
    install_access_log_redaction,
    redact_oauth_query,
)


def _access_record(full_path):
    """Build a LogRecord shaped like uvicorn's access logger emits."""
    return logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("127.0.0.1:1234", "GET", full_path, "1.1", 307),
        exc_info=None,
    )


def test_oauth_callback_request_line_is_redacted():
    line = "/api/integrations/google/callback?code=4/0AX-longcode&state=nonce-abc&scope=email"
    record = _access_record(line)

    OAuthAccessLogFilter().filter(record)

    redacted = record.args[2]
    assert "code=<redacted>" in redacted
    assert "state=<redacted>" in redacted
    assert "4/0AX" not in redacted
    assert "state_nonce" not in redacted
    assert "state-nonce" not in redacted
    # Non-sensitive diagnostics are preserved.
    assert "scope=email" in redacted
    assert "/api/integrations/google/callback" in redacted


def test_redact_helper_covers_login_path_and_sensitive_params():
    out = redact_oauth_query(
        "/api/auth/google/callback?code=abc&state=def&refresh_token=ghi&scope=email"
    )
    assert "code=abc" not in out
    assert "state=def" not in out
    assert "refresh_token=ghi" not in out
    assert "code=<redacted>" in out
    assert "state=<redacted>" in out
    assert "refresh_token=<redacted>" in out
    assert "scope=email" in out  # not a secret; kept for diagnostics


def test_non_oauth_paths_are_untouched():
    # A ``state`` query param on a non-OAuth route must not be rewritten.
    record = _access_record("/api/emails?state=page2&limit=20")
    OAuthAccessLogFilter().filter(record)
    assert record.args[2] == "/api/emails?state=page2&limit=20"


def test_path_without_query_is_untouched():
    record = _access_record("/api/integrations/google/callback")
    OAuthAccessLogFilter().filter(record)
    assert record.args[2] == "/api/integrations/google/callback"


def test_install_is_idempotent():
    name = "uvicorn.access.test-install"
    logger = install_access_log_redaction(name)
    install_access_log_redaction(name)
    assert sum(isinstance(f, OAuthAccessLogFilter) for f in logger.filters) == 1
