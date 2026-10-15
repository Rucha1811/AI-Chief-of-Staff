"""One-time, operator-authorized creation of the first (owner) user.

P24B-06 - first-user bootstrap privilege escalation.

Why this module exists
----------------------
Creating the first user also grants it the ``owner`` role, and ``owner``
carries ``manage_users``. Any unauthenticated request that happens to arrive
while the ``users`` table is empty can therefore mint itself a full
administrator. On a freshly deployed production instance the *first* request
wins that race - so "whoever is first" becomes "whoever is an attacker", and the
Google-login path made that reachable from a browser with nothing but a Google
account.

The fix has three independent parts, and all three matter:

1. **Authorization.** In production the operator must present a one-time
   ``BOOTSTRAP_TOKEN``. Nothing else is accepted, an unset token means
   "refused", and the comparison is constant time. There is no default token
   anywhere in the codebase.
2. **No alternate door.** Google OAuth login no longer creates the first user
   at all (see ``app/api/auth.py``), so there is no browser-reachable way to
   skip part 1.
3. **Atomicity.** The authorization is consumed by a single conditional INSERT
   executed *inside the same transaction* that creates the user. Two concurrent
   requests cannot both win it, and a request that fails later rolls the
   consumption back - so a transient error does not permanently burn the
   one-shot token.

The consumption marker lives in ``app_config``, i.e. in the database, so it
outlives the process: the bootstrap cannot be re-activated after a restart, and
not even if the owner row is deleted later. That is strictly stronger than
"the users table is non-empty", which is the check that can be undone by an
operator's ``DELETE``.
"""

import hashlib
import json
import logging
import secrets
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import _is_production
from app.models.app_config import AppConfig
from app.models.user import User

logger = logging.getLogger(__name__)

# Durable marker row. Versioned so a future re-provisioning procedure can use a
# new key instead of silently unlocking this one.
BOOTSTRAP_MARKER_KEY = "bootstrap.owner.v1"

# Outcome codes. They are stable strings so logs and tests can assert on the
# reason without depending on the message shown to the client.
AUTHORIZED = "authorized"
AUTHORIZED_DEVELOPMENT = "authorized_development"
REFUSED_NOT_CONFIGURED = "refused_token_not_configured"
REFUSED_INVALID_TOKEN = "refused_invalid_token"
REFUSED_CONSUMED = "refused_already_bootstrapped"
REFUSED_USERS_EXIST = "refused_users_exist"
CLAIM_LOST = "claim_lost"

# The client-visible message is intentionally identical for every refusal so
# the endpoint cannot be used to probe which condition failed.
GENERIC_REFUSAL = "First-owner creation is not available on this server."


@dataclass(frozen=True)
class BootstrapDecision:
    authorized: bool
    reason: str


def _fingerprint(value: str) -> str:
    """Fixed-length digest so the compare below cannot leak length by timing."""
    return hashlib.sha256(value.strip().encode("utf-8")).hexdigest()


def _tokens_match(presented: Optional[str], configured: str) -> bool:
    if not presented:
        return False
    return secrets.compare_digest(_fingerprint(presented), _fingerprint(configured))


def _marker_value(db: Session) -> Optional[str]:
    row = db.execute(
        select(AppConfig.value).where(AppConfig.key == BOOTSTRAP_MARKER_KEY)
    ).scalar()
    return row if isinstance(row, str) else None


def _first_user_exists(db: Session) -> bool:
    return db.execute(select(User.id).limit(1)).first() is not None


def bootstrap_state(db: Session) -> str:
    """Read-only view of the bootstrap: 'available' | 'consumed' | 'closed'."""
    try:
        if _marker_value(db) is not None:
            return "consumed"
    except Exception:  # pragma: no cover - table missing before first migration
        logger.debug("app_config unreadable; treating bootstrap state as unknown")
        return "unknown"
    return "closed" if _first_user_exists(db) else "available"


def evaluate(
    db: Session,
    *,
    presented_token: Optional[str] = None,
    configured_token: Optional[str] = None,
    environment: Optional[str] = None,
) -> BootstrapDecision:
    """Decide - without mutating anything - whether this request may own.

    ``configured_token`` must be the value the operator actually configured
    (see ``config.configured_value``), not a derived or auto-generated one.
    ``environment`` is passed in rather than read from settings so the policy is
    testable in isolation and cannot be talked into "production" by import order.
    """
    if _marker_value(db) is not None:
        return BootstrapDecision(False, REFUSED_CONSUMED)

    if _first_user_exists(db):
        # Not the bootstrap case. Ordinary registration rules (an authenticated
        # caller with `manage_users`) apply in the endpoint; a bootstrap token is
        # neither needed nor accepted here.
        return BootstrapDecision(False, REFUSED_USERS_EXIST)

    if not _is_production(environment):
        # Local development keeps the historical convenience so a fresh checkout
        # provisions itself with zero configuration. Production below is the
        # fail-closed branch.
        return BootstrapDecision(True, AUTHORIZED_DEVELOPMENT)

    token = (configured_token or "").strip()
    if not token:
        # Fail closed: production configuration is incomplete, so nobody - not
        # even a well-meaning operator who forgot the token - gets ownership.
        return BootstrapDecision(False, REFUSED_NOT_CONFIGURED)

    if not _tokens_match(presented_token, token):
        return BootstrapDecision(False, REFUSED_INVALID_TOKEN)

    return BootstrapDecision(True, "authorized_token")


def _insert_marker_ignoring_conflict(db: Session, *, key: str, value: str):
    """``INSERT ... ON CONFLICT DO NOTHING`` for the dialects we support.

    Returns None when the dialect has no such construct, so the caller can fall
    back to a savepoint-guarded plain INSERT.
    """
    dialect = getattr(getattr(db, "bind", None), "dialect", None)
    name = getattr(dialect, "name", "")
    if name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        return pg_insert(AppConfig).values(key=key, value=value).on_conflict_do_nothing(
            index_elements=[AppConfig.key]
        )
    if name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        return sqlite_insert(AppConfig).values(key=key, value=value).on_conflict_do_nothing(
            index_elements=[AppConfig.key]
        )
    return None


def claim(db: Session, *, owner_user_id: Optional[int] = None) -> bool:
    """Consume the one-shot authorization inside the caller's transaction.

    Returns True only for the caller that actually won the race. The caller MUST
    have already inserted (or be about to insert, in the same transaction) the
    owner row and MUST commit afterwards: the claim is deliberately not committed
    here so that a failure rolls back both halves together and leaves the
    one-shot token usable.

    Concurrency: the conditional INSERT is the serialization point. A second
    transaction either blocks until the first commits and then inserts nothing,
    or - if the first rolls back - succeeds itself. Either way exactly one owner
    can be created.
    """
    nonce = secrets.token_urlsafe(16)
    statement = _insert_marker_ignoring_conflict(db, key=BOOTSTRAP_MARKER_KEY, value=nonce)

    if statement is not None:
        db.execute(statement)
    else:  # pragma: no cover - only for dialects without ON CONFLICT
        try:
            with db.begin_nested():
                db.add(AppConfig(key=BOOTSTRAP_MARKER_KEY, value=nonce))
                db.flush()
        except IntegrityError:
            return False

    if _marker_value(db) != nonce:
        return False

    if owner_user_id is not None:
        db.execute(
            update(AppConfig)
            .where(AppConfig.key == BOOTSTRAP_MARKER_KEY, AppConfig.value == nonce)
            .values(value=json.dumps({"owner_user_id": owner_user_id}))
        )
    return True
