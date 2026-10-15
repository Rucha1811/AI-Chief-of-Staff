"""Coordinates AI processing of emails through the bounded worker queue.

Provides:
  - enqueue_email(email_id): submit an email for AI processing (bounded).
  - a function that classifies an email and retries temporary failures rather
    than permanently marking them failed.
"""
import asyncio
import logging

from app.services.ai_queue import get_ai_queue
from app.services.email_processor import process_email

logger = logging.getLogger("ai-chief.aiproc")


def _run_job(email_id: int) -> None:
    """Synchronous job run on a worker thread."""
    try:
        asyncio.run(process_email(email_id))
    except Exception:
        logger.exception("AI processing job failed for email %s", email_id)


def enqueue_email(email_id: int) -> bool:
    """Enqueue an email for AI processing. Returns True if queued, False if the
    bounded queue is full (the email stays 'unknown' and will be picked up by
    a later backfill pass)."""
    return get_ai_queue().submit(f"email:{email_id}", lambda: _run_job(email_id))


def start() -> None:
    get_ai_queue().start()


def stop() -> None:
    get_ai_queue().stop()


def stats() -> dict:
    return get_ai_queue().stats()
