"""Access-log redaction for OAuth credentials.

Uvicorn's access logger writes the raw request line, so a Google OAuth
callback is logged as::

    "GET /api/integrations/google/callback?code=<one-time-code>&state=<nonce>"

Both ``code`` and ``state`` are credentials while the redirect is in flight:
the code can be exchanged for tokens and the state authorizes binding the
result to a user. They must never be persisted to logs.

This module installs a narrowly-scoped ``logging.Filter`` on the
``uvicorn.access`` logger that rewrites only OAuth endpoint request lines,
leaving the method, path and non-sensitive query parameters intact so request
diagnostics are preserved.
"""

from __future__ import annotations

import logging
import re

# OAuth endpoints whose query strings may carry one-time credentials.
_OAUTH_PATH_PREFIXES = (
    "/api/integrations/google/",
    "/api/auth/google/",
)

# Query parameters that must never appear in access logs. ``scope`` is
# deliberately NOT redacted: it is a list of permission URLs, not a secret, and
# is useful when diagnosing scope-grant problems (e.g. exchange_failed).
_SENSITIVE_PARAMS = (
    "code",
    "state",
    "id_token",
    "access_token",
    "refresh_token",
)

_SENSITIVE_RE = re.compile(
    r"([?&])(" + "|".join(re.escape(p) for p in _SENSITIVE_PARAMS) + r")=[^&\s\"']*",
    re.IGNORECASE,
)


def redact_oauth_query(full_path: str) -> str:
    """Redact sensitive query values, but only on OAuth endpoint paths.

    Non-OAuth paths (and paths without a query string) are returned unchanged.
    """
    if not full_path or "?" not in full_path:
        return full_path
    path = full_path.split("?", 1)[0]
    if not path.startswith(_OAUTH_PATH_PREFIXES):
        return full_path
    return _SENSITIVE_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}=<redacted>", full_path)


class OAuthAccessLogFilter(logging.Filter):
    """Rewrite OAuth credential query parameters in access log records.

    Uvicorn emits ``logger.info('%s - "%s %s HTTP/%s" %d', client, method,
    full_path, http_version, status)`` so ``record.args[2]`` is the path plus
    query string. The dict branch is a defensive fallback in case a different
    server passes structured args.
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003 (stdlib name)
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            redacted = redact_oauth_query(args[2])
            if redacted != args[2]:
                record.args = args[:2] + (redacted,) + args[3:]
        elif isinstance(args, dict):
            value = args.get("full_path") or args.get("path")
            if isinstance(value, str):
                redacted = redact_oauth_query(value)
                if redacted != value:
                    new_args = dict(args)
                    for key in ("full_path", "path"):
                        if key in new_args:
                            new_args[key] = redacted
                    record.args = args = new_args
        return True


def install_access_log_redaction(
    logger_name: str = "uvicorn.access",
) -> logging.Logger:
    """Idempotently attach :class:`OAuthAccessLogFilter` to an access logger."""
    logger = logging.getLogger(logger_name)
    if not any(isinstance(f, OAuthAccessLogFilter) for f in logger.filters):
        logger.addFilter(OAuthAccessLogFilter())
    return logger
