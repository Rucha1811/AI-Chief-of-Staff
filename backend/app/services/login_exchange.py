"""Single-use, short-lived login exchange codes.

The Google login callback must never place the long-lived authentication
token in a redirect URL (browser history, proxy/access logs, Referer). The
callback instead mints a cryptographically random exchange code, redirects
the browser with THAT code, and the frontend trades it for the real token
over a direct POST to /api/auth/exchange.

Properties:
- random:        secrets.token_urlsafe(32)
- short-lived:   _TTL_SECONDS
- single-use:    a code is deleted the moment it is consumed
- server-side:   the long-lived token never leaves the backend in a URL
"""

import secrets
import time
from typing import Optional

_TTL_SECONDS = 90

# code -> (token, expires_at). In-memory is sufficient: the code lives for
# seconds within one authentication transaction on the single uvicorn worker,
# exactly like the OAuth state cookie it follows.
_store: dict[str, tuple[str, float]] = {}


def _prune(now: float) -> None:
    expired = [code for code, (_, exp) in _store.items() if exp <= now]
    for code in expired:
        _store.pop(code, None)


def create_exchange_code(token: str) -> str:
    """Store ``token`` server-side and return its single-use exchange code."""
    now = time.time()
    _prune(now)
    code = secrets.token_urlsafe(32)
    _store[code] = (token, now + _TTL_SECONDS)
    return code


def consume_exchange_code(code: str) -> Optional[str]:
    """Return the stored token for ``code`` exactly once, else None.

    A code that is unknown, already used, or expired returns None in every
    case so callers cannot distinguish them.
    """
    if not code:
        return None
    entry = _store.pop(code, None)  # pop => single-use, even when expired
    if entry is None:
        return None
    token, expires_at = entry
    if expires_at <= time.time():
        return None
    return token
