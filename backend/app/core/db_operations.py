"""Safe database operation helpers with graceful recovery from transient
PostgreSQL connection failures.

A database session must NEVER remain inside an open transaction while waiting
for a slow external service (Ollama, network, notifications). The helpers here
ensure a fresh session is used for each read/write and retry safe, recoverable
operations with bounded exponential backoff.
"""
import logging
import random
import time
from typing import Callable, Optional, TypeVar

from sqlalchemy.exc import (
    DBAPIError,
    InterfaceError,
    OperationalError,
    SQLAlchemyError,
)

from app.core.config import settings
from app.core.database import engine, SessionLocal

logger = logging.getLogger("ai-chief.db")

T = TypeVar("T")

# The exact messages PostgreSQL returns when a connection is torn down by the
# server (e.g. idle_in_transaction_session_timeout) or the network drops.
_RECOVERABLE_HINTS = (
    "server closed the connection unexpectedly",
    "connection was killed",
    "idle-in-transaction timeout",
    "connection has been closed",
    "terminating connection",
    "connection is already closed",
    "FATAL:  terminating connection",
)


def _is_recoverable(exc: BaseException) -> bool:
    """Return True if the exception is a recoverable connection-level failure.

    Unrecoverable errors (constraint violations, missing tables, etc.) are
    NOT retried because retrying them would be wasteful or dangerous.
    """
    if isinstance(exc, (InterfaceError, OperationalError)):
        return True
    if isinstance(exc, DBAPIError):
        return True
    msg = str(exc)
    return any(hint in msg.lower() for hint in _RECOVERABLE_HINTS)


class RetryExhaustedError(RuntimeError):
    """Raised when a database operation fails after all configured retries."""


def dispose_pool() -> None:
    """Invalidate/close all pooled connections so fresh ones are created.

    After a server-side connection kill, the pool may still hold stale
    connections that advertise as healthy (pool_pre_ping helps, but forcing
    a full dispose is the most reliable recovery).
    """
    try:
        engine.dispose()
        logger.info("Disposed the SQLAlchemy connection pool for recovery")
    except Exception:  # pragma: no cover - defensive
        logger.warning("Failed to dispose the SQLAlchemy connection pool", exc_info=True)


def run_with_retry(
    fn: Callable[[], T],
    *,
    retries: Optional[int] = None,
    base_delay: Optional[float] = None,
    description: str = "db operation",
) -> T:
    """Execute ``fn`` with bounded exponential-backoff retry on recoverable
    connection failures.

    ``fn`` is expected to be SELF-CONTAINED: it should open (or receive) a
    fresh session and COMMIT its own work. It must be safe/idempotent to
    retry. Do not pass in a long-lived session.
    """
    max_retries = retries if retries is not None else settings.DB_OPERATION_MAX_RETRIES
    delay = base_delay if base_delay is not None else settings.DB_OPERATION_RETRY_BASE_DELAY

    attempt = 0
    while True:
        try:
            result = fn()
            if attempt > 0:
                logger.info("%s recovered after %d retries", description, attempt)
            return result
        except Exception as exc:  # noqa: BLE001 - we intentionally catch broadly
            if not _is_recoverable(exc) or attempt >= max_retries:
                if attempt >= max_retries and _is_recoverable(exc):
                    logger.error(
                        "%s failed after %d attempts: %s",
                        description,
                        attempt + 1,
                        _safe_str(exc),
                    )
                    raise RetryExhaustedError(
                        f"{description} failed after {max_retries} retries"
                    ) from exc
                raise
            attempt += 1
            # Dispose potentially-stale pool connections before retrying.
            dispose_pool()
            sleep = delay * (2 ** (attempt - 1)) + random.uniform(0, delay)
            logger.warning(
                "%s failed (attempt %d/%d), retrying in %.1fs: %s",
                description,
                attempt,
                max_retries,
                sleep,
                _safe_str(exc),
            )
            time.sleep(sleep)


def _safe_str(exc: BaseException) -> str:
    """Return a safe, non-secret error description."""
    try:
        msg = str(exc)
    except Exception:
        msg = type(exc).__name__
    # Truncate to avoid logging huge SQL strings or diagnostics.
    return msg[:300]


def db_session_context():
    """Yield a brand-new session, always rollback+close.

    Prefer this over SessionLocal() directly so recovery is centralized.
    """
    db = SessionLocal()
    try:
        yield db
    except SQLAlchemyError:
        db.rollback()
        raise
    finally:
        try:
            db.rollback()
        except SQLAlchemyError:
            logger.debug("rollback during session close failed", exc_info=True)
        db.close()
