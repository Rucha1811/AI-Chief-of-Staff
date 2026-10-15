from datetime import timedelta

from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.timeutil import utcnow
from app.models.assignment import Assignment
from app.models.email import Email
from app.models.event import Event
from app.models.exam import Exam
from app.models.notification import Notification
from app.models.task import Task

_REGEN_MIN_INTERVAL = timedelta(minutes=5)
_last_regen = None


def _regen_due(force: bool) -> bool:
    global _last_regen
    if force:
        _last_regen = utcnow()
        return True
    now = utcnow()
    if _last_regen is not None and now - _last_regen < _REGEN_MIN_INTERVAL:
        return False
    _last_regen = now
    return True


def _human(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    if minutes < 1:
        return "now"
    if minutes < 60:
        return f"in {minutes}m"
    hours = minutes // 60
    return f"in {hours}h"


def _add(session, ntype, title, message, entity_type, entity_id, priority, fingerprint, user_id):
    existing = session.execute(
        select(Notification).where(
            Notification.user_id == user_id,
            (
                (Notification.fingerprint == fingerprint)
                | (
                    (Notification.type == ntype)
                    & (Notification.title == title)
                    & (Notification.message == message)
                )
            ),
        )
    ).scalar_one_or_none()
    if existing:
        return None
    session.add(
        Notification(
            type=ntype,
            title=title,
            message=message,
            entity_type=entity_type,
            entity_id=entity_id,
            priority=priority,
            fingerprint=fingerprint,
            user_id=user_id,
        )
    )
    return True


def generate_reminders(force: bool = False) -> int:
    if not _regen_due(force):
        return 0
    # Reminder windows and the read-notification retention purge both compare
    # against created_at / due fields. Those are written either by the
    # database clock (func.now(), UTC) or by this same UTC-convention, so the
    # comparison clock must be UTC too - a host-local now() would skew every
    # window by the host's offset (D5).
    now = utcnow()
    created = 0
    with SessionLocal() as session:
        events = session.execute(
            select(Event).where(
                Event.is_all_day.is_(False),
                Event.start_time <= now + timedelta(hours=24),
                Event.start_time >= now - timedelta(minutes=30),
            )
        ).scalars().all()
        for ev in events:
            location = f" in {ev.location}" if ev.location else ""
            n = _add(
                session,
                "event",
                f"{ev.title} {_human(ev.start_time - now)}",
                f"{ev.title} starts at {ev.start_time.strftime('%H:%M')}{location}",
                "event",
                ev.id,
                "normal",
                f"event:{ev.id}:{ev.start_time.strftime('%Y-%m-%d')}",
                ev.user_id,
            )
            created += int(n is not None)

        tasks = session.execute(
            select(Task).where(
                Task.status != "completed",
                Task.due_date.isnot(None),
                Task.due_date <= now + timedelta(hours=48),
            )
        ).scalars().all()
        for t in tasks:
            overdue = t.due_date < now
            n = _add(
                session,
                "task",
                f"{'Overdue' if overdue else 'Due soon'}: {t.title}",
                f"Due {t.due_date.strftime('%a %d %b %H:%M')}",
                "task",
                t.id,
                "high" if overdue else "normal",
                f"task:{t.id}:{t.due_date.strftime('%Y-%m-%d')}",
                t.user_id,
            )
            created += int(n is not None)

        emails = session.execute(
            select(Email).where(
                Email.has_deadline.is_(True),
                Email.deadline_at.isnot(None),
                Email.deadline_at <= now + timedelta(hours=48),
            )
        ).scalars().all()
        for em in emails:
            overdue = em.deadline_at < now
            n = _add(
                session,
                "email_deadline",
                f"{'Overdue' if overdue else 'Deadline'}: {em.subject or 'Email'}",
                f"{em.deadline_description or 'A deadline'} is due {em.deadline_at.strftime('%a %d %b %H:%M')}",
                "email",
                em.id,
                "high" if overdue else "normal",
                f"email:{em.id}:{em.deadline_at.strftime('%Y-%m-%d')}",
                em.user_id,
            )
            created += int(n is not None)

        exams = session.execute(
            select(Exam).where(
                Exam.status == "upcoming",
                Exam.exam_date <= now + timedelta(days=7),
            )
        ).scalars().all()
        for ex in exams:
            days_left = (ex.exam_date - now).days
            if days_left < 0:
                continue
            n = _add(
                session,
                "exam",
                f"Exam: {ex.title} in {days_left}d" if days_left else f"Exam today: {ex.title}",
                f"{ex.title} on {ex.exam_date.strftime('%a %d %b %H:%M')}"
                + (f" in {ex.location}" if ex.location else ""),
                "exam",
                ex.id,
                "high" if days_left <= 3 else "normal",
                f"exam:{ex.id}:{ex.exam_date.strftime('%Y-%m-%d')}",
                ex.user_id,
            )
            created += int(n is not None)

        assignments = session.execute(
            select(Assignment).where(
                Assignment.status.in_(["todo", "in_progress"]),
                Assignment.due_date.isnot(None),
                Assignment.due_date <= now + timedelta(hours=48),
            )
        ).scalars().all()
        for asg in assignments:
            overdue = asg.due_date < now
            n = _add(
                session,
                "assignment",
                f"{'Overdue' if overdue else 'Assignment due'}: {asg.title}",
                f"Due {asg.due_date.strftime('%a %d %b %H:%M')}"
                + (f" · {asg.course}" if asg.course else ""),
                "assignment",
                asg.id,
                "high" if overdue else "normal",
                f"assignment:{asg.id}:{asg.due_date.strftime('%Y-%m-%d')}",
                asg.user_id,
            )
            created += int(n is not None)

        old = session.execute(
            select(Notification).where(
                Notification.is_read.is_(True),
                Notification.created_at < now - timedelta(days=14),
            )
        ).scalars().all()
        for o in old:
            session.delete(o)

        session.commit()
    return created
