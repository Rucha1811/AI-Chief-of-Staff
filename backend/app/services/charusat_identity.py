"""CHARUSAT institutional identity and attendance eligibility.

Why this exists
---------------
The attendance feature is a *student* feature for CHARUSAT. Two things must be
true before it is offered:

1. The account really is a CHARUSAT institutional identity - proven by an
   identity provider's verified-email claim, NOT by the string the user typed
   into ``users.email`` at registration (that field is user-editable).

2. The identity is looked up against the authenticated user, so one student can
   never read another student's attendance.

This module owns rule (1). It records a provider-confirmed address on the user
(:func:`record_verified_identity`) and answers whether an account is eligible
(:func:`eligibility`). Rule (2) is enforced by the connector querying only the
authenticated ``user_id``.

A verified institutional address is necessary but not sufficient to *prove* the
person is a student: any staff/faculty member may hold such an address. The
authorized source decides that, so the connector additionally reports whether an
authorized retrieval actually returned attendance (``student_verified`` in the
status payload). Nothing is inferred from the address alone.
"""

from __future__ import annotations

from typing import Optional

from app.core.config import settings

# Eligibility outcomes. The API surfaces these as a machine-readable ``reason``
# so the client can explain precisely why the feature is unavailable instead of
# showing a bare "access denied".
REASON_OK = "ok"
REASON_ACCOUNT_INACTIVE = "account_inactive"
REASON_NOT_VERIFIED = "not_verified"
REASON_WRONG_DOMAIN = "wrong_domain"


def normalize_email(email: Optional[str]) -> str:
    return (email or "").strip().lower()


def email_domain(email: Optional[str]) -> str:
    normalized = normalize_email(email)
    if "@" not in normalized:
        return ""
    return normalized.rsplit("@", 1)[1]


def is_institutional_email(email: Optional[str]) -> bool:
    """True only when ``email`` is on the configured institutional domain.

    An unset/blank domain matches nothing (fail closed).
    """
    domain = settings.charusat_institutional_domain
    if not domain:
        return False
    return email_domain(email) == domain


def mask_email(email: Optional[str]) -> Optional[str]:
    """Mask the local part for display, e.g. ``24dcs025@…`` -> ``24d***@…``."""
    normalized = normalize_email(email)
    if not normalized or "@" not in normalized:
        return None
    local, _sep, domain = normalized.partition("@")
    if not local:
        head = "***"
    elif len(local) <= 3:
        head = local[0] + "***"
    else:
        head = local[:3] + "***"
    return f"{head}@{domain}"


def record_verified_identity(
    db,
    user,
    *,
    verified_email: Optional[str],
    email_verified: bool,
) -> bool:
    """Store a provider-confirmed address on ``user``.

    The address is written ONLY when the provider asserted the claim
    (``email_verified``) and supplied an address. Returns whether the user now
    holds a verified address.

    This is intentionally the single write path for ``email_verified`` /
    ``verified_email``: no request body, query parameter or other user-editable
    input may set them.
    """
    # Use getattr with fallbacks for deployments where the database columns
    # (email_verified, verified_email) have not yet been added migration 0013.
    _ev = getattr(user, "email_verified", False)
    _ve = getattr(user, "verified_email", None)

    if not _ev:
        return False
    email = normalize_email(verified_email)
    if not email or "@" not in email:
        return False

    if (not _ev) or normalize_email(_ve) != email:
        user.email_verified = True
        user.verified_email = email
        db.commit()
    return True


def eligibility(user) -> dict:
    """Describe whether ``user`` may use the CHARUSAT attendance feature.

    Returns a JSON-safe dict. ``verified_email`` is the user's own confirmed
    address (used server-side to label the connection); ``masked_email`` is the
    only form the API should return to the client.
    """
    verified = normalize_email(getattr(user, "verified_email", None))
    has_verified = bool(getattr(user, "email_verified", False)) and bool(verified)

    info = {
        "eligible": False,
        "reason": REASON_NOT_VERIFIED,
        "domain": settings.charusat_institutional_domain or None,
        "verified_email": verified or None,
        "masked_email": mask_email(verified) if verified else None,
    }

    if user is None:
        return info
    if getattr(user, "status", None) != "active":
        info["reason"] = REASON_ACCOUNT_INACTIVE
        return info
    if not has_verified:
        # Covers both "never signed in with a verifying provider" and
        # "provider said the address is unverified".
        return info
    if not is_institutional_email(verified):
        info["reason"] = REASON_WRONG_DOMAIN
        return info

    info["eligible"] = True
    info["reason"] = REASON_OK
    return info


def public_eligibility(user) -> dict:
    """Eligibility shaped for a client response (no raw address, no internals)."""
    info = eligibility(user)
    return {
        "eligible": info["eligible"],
        "reason": info["reason"],
        "domain": info["domain"],
        "account": info["masked_email"],
    }
