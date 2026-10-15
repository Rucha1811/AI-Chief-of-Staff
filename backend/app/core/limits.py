"""Bounded pagination helpers (NF-6).

List endpoints used to pass a caller-supplied ``limit`` straight into
``.limit(limit)``. A client (or a compromised token) could ask for an
arbitrarily large page and make the process materialise a huge result set in
memory. These helpers clamp the value to a configurable ceiling
(``settings.API_MAX_PAGE_SIZE``) without changing behaviour for requests that
ask for a page size within the bound.
"""

from app.core.config import settings


def clamp_limit(limit: int, maximum: int | None = None) -> int:
    """Return ``limit`` constrained to ``1..maximum``.

    ``maximum`` defaults to ``settings.API_MAX_PAGE_SIZE``. Values below 1 are
    raised to 1 so a query is never served with an unbounded/zero window.
    """
    cap = int(maximum) if maximum is not None else int(settings.API_MAX_PAGE_SIZE)
    if cap < 1:
        cap = 1
    try:
        value = int(limit)
    except (TypeError, ValueError):
        return cap
    if value < 1:
        return 1
    return min(value, cap)


def clamp_offset(offset: int) -> int:
    """Return ``offset`` constrained to ``>= 0``."""
    try:
        value = int(offset)
    except (TypeError, ValueError):
        return 0
    return max(value, 0)
