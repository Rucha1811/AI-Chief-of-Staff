"""Aggregated, safe observability for the system.

Exposes DB pool health, Ollama health, AI queue state, and last-sync times.
Never exposes email content, OAuth tokens, API keys, or passwords.
"""
import logging
import time

from sqlalchemy import text

from app.core.config import settings
from app.core.database import engine
from app.services.ai_service import ai_service
from app.services import ai_processor

logger = logging.getLogger("ai-chief.observability")

_SYNC_MARKERS = {
    "gmail": None,
    "classroom": None,
}


def _set_sync_marker(key: str) -> None:
    _SYNC_MARKERS[key] = time.time()


def record_sync(key: str) -> None:
    _set_sync_marker(key)


def last_sync(key: str):
    ts = _SYNC_MARKERS.get(key)
    if ts is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def db_health() -> dict:
    """Return a safe snapshot of DB pool + a live connectivity probe."""
    result = {
        "status": "ok",
        "pool": {
            "size": settings.DB_POOL_SIZE,
            "overflow": settings.DB_MAX_OVERFLOW,
            "checked_out": None,
            "overflow_used": None,
        },
    }
    try:
        pool = engine.pool
        result["pool"]["checked_out"] = pool.checkedout()
        result["pool"]["overflow_used"] = pool.overflow()
    except Exception as e:
        logger.debug("Could not read DB pool stats: %s", e)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        result["status"] = "ok"
    except Exception as e:
        result["status"] = "error"
        result["detail"] = f"db connectivity failed: {type(e).__name__}"
    return result


def system_health() -> dict:
    """Compose a full, safe health report for /api/health and system checks."""
    return {
        "status": "ok",
        "app": settings.APP_NAME,
        "version": settings.VERSION,
        "database": db_health(),
        # n8n integration status is a passive flag describing whether the
        # ingest endpoints are configured/available.
        "integrations": {
            "n8n_ingest_enabled": bool(settings.INGEST_API_KEY),
            "ingest_batch_max": settings.INGEST_BATCH_MAX,
        },
    }


async def ai_health() -> dict:
    """Async-safe health for the AI (Ollama) subsystem + queue state."""
    from app.services.ai_service import ai_service as svc
    from app.services import ai_processor

    try:
        ollama = await svc.health()
    except Exception as e:
        ollama = {"status": "unavailable", "detail": type(e).__name__}

    return {
        "ollama": ollama,
        "ai_queue": ai_processor.stats(),
        "circuit_breaker": svc.circuit_breaker.state,
    }
