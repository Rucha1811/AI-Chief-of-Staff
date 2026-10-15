"""UTC clock for naive-datetime comparisons (Phase D5).

The database records timestamps with PostgreSQL's clock (``func.now()`` /
``server_default``), which is UTC in every container in this project. Code that
compared those values against a host-local ``datetime.now()`` skewed every
window (push delivery, reminder generation, notification retention purge) by
the host's UTC offset: on a +05:30 host a notification stamped "now" in UTC
looked 5.5 hours old and was never pushed.

Every call site returns an *naive* UTC datetime on purpose: all columns these
values are compared against (``created_at``, ``updated_at``, ``start_time``,
``due_date``, ``deadline_at``, ``exam_date``) are naive timestamps, so
converting to an aware datetime would raise ``TypeError`` at the comparison.
"""

from datetime import datetime


def utcnow() -> datetime:
    """Naive UTC "now", the same clock ``func.now()`` runs on in Postgres."""
    return datetime.utcnow()