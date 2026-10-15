"""Bounded background worker pool for AI processing of emails.

Runs slow Ollama jobs on a dedicated thread pool so they never block the
FastAPI event loop, and caps concurrency so we don't overload local Ollama.
Jobs are tracked in-process (queue depth, active, failed, processed) for
observability.

DB sessions are opened/closed per-step inside email_processor.process_email;
this queue only marshals work items and does not itself touch the DB.
"""
import logging
import threading
import time
from collections import deque
from queue import Empty, Queue
from typing import Callable, Optional

from app.core.config import settings

logger = logging.getLogger("ai-chief.aiq")


class AIQueueStats:
    def __init__(self):
        self._lock = threading.Lock()
        self.queue_size = 0
        self.active = 0
        self.processed = 0
        self.failed = 0
        self._started_at = time.time()

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "queue_size": self.queue_size,
                "active": self.active,
                "processed": self.processed,
                "failed": self.failed,
                "uptime_seconds": int(time.time() - self._started_at),
            }


class AIQueue:
    """A simple, bounded FIFO queue drained by a fixed set of worker threads."""

    def __init__(
        self,
        num_workers: Optional[int] = None,
        max_size: Optional[int] = None,
    ):
        self._queue: Queue = Queue(maxsize=max_size or settings.AI_QUEUE_MAX_SIZE)
        self._workers = num_workers or settings.AI_WORKER_THREADS
        self._stats = AIQueueStats()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        if self._threads:
            return
        for i in range(self._workers):
            t = threading.Thread(
                target=self._worker_loop,
                name=f"ai-worker-{i}",
                daemon=True,
            )
            t.start()
            self._threads.append(t)
        logger.info(
            "AI worker pool started with %d workers, queue max %d",
            self._workers,
            self._queue.maxsize,
        )

    def stop(self) -> None:
        self._stop.set()

    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            try:
                job_id, fn = self._queue.get(timeout=1.0)
            except Empty:
                continue
            self._stats.active += 1
            self._stats.queue_size = self._queue.qsize()
            try:
                fn()
                self._stats.processed += 1
            except Exception:
                self._stats.failed += 1
                logger.exception("AI worker job %s failed", job_id)
            finally:
                self._stats.active -= 1
                self._stats.queue_size = self._queue.qsize()
                try:
                    self._queue.task_done()
                except ValueError:
                    pass

    def submit(self, job_id: str, fn: Callable[[], None]) -> bool:
        """Enqueue a job. Returns False if the queue is full (caller decides
        how to handle that; the job is never silently dropped)."""
        try:
            self._queue.put_nowait((job_id, fn))
            self._stats.queue_size = self._queue.qsize()
            return True
        except Exception:
            logger.warning("AI queue full; job %s rejected", job_id)
            return False

    def stats(self) -> dict:
        self._stats.queue_size = self._queue.qsize()
        return self._stats.snapshot()


_ai_queue = AIQueue()


def get_ai_queue() -> AIQueue:
    return _ai_queue
