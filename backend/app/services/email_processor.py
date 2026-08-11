import logging
from datetime import datetime, timedelta

from app.core.database import SessionLocal
from app.models.assignment import Assignment
from app.models.email import Email
from app.models.event import Event
from app.models.exam import Exam
from app.services.ai_service import ai_service

logger = logging.getLogger(__name__)


def _parse_ymd(value):
    if not value:
        return None
    try:
        return datetime.strptime(str(value).strip(), "%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def _parse_hm(value):
    if not value:
        return None
    try:
        return datetime.strptime(str(value).strip(), "%H:%M").time()
    except (ValueError, TypeError):
        return None


def _norm(text: str) -> str:
    import re

    return re.sub(r"[^a-z0-9 ]+", "", " ".join((text or "").lower().split()))


def _titles_match(a: str, b: str) -> bool:
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    short, long = (na, nb) if len(na) <= len(nb) else (nb, na)
    return len(short) >= 8 and long.startswith(short)


def _event_exists(db, title, start_time):
    from sqlalchemy import func

    day = start_time.date()
    return (
        db.query(Event)
        .filter(func.date(Event.start_time) == day.isoformat())
        .all()
    ) and any(_titles_match(e.title, title) for e in db.query(Event).filter(func.date(Event.start_time) == day.isoformat()).all())


def _exam_exists(db, title, exam_date):
    from sqlalchemy import func

    day = exam_date.date()
    return (
        db.query(Exam)
        .filter(func.date(Exam.exam_date) == day.isoformat())
        .all()
    ) and any(_titles_match(e.title, title) for e in db.query(Exam).filter(func.date(Exam.exam_date) == day.isoformat()).all())


def _assignment_exists(db, title, due_date):
    from sqlalchemy import func

    day = due_date.date()
    return (
        db.query(Assignment)
        .filter(func.date(Assignment.due_date) == day.isoformat())
        .all()
    ) and any(_titles_match(a.title, title) for a in db.query(Assignment).filter(func.date(Assignment.due_date) == day.isoformat()).all())


def _apply_schedule_item(db, email, item) -> dict:
    kind = str(item.get("kind", "")).lower()
    title = str(item.get("title", "")).strip()
    if not title:
        return {"kind": kind, "action": "skipped", "reason": "no title"}
    date = _parse_ymd(item.get("date"))
    if date is None:
        return {"kind": kind, "action": "skipped", "reason": "no date"}
    time_hm = _parse_hm(item.get("time")) or _parse_hm(item.get("end_time"))
    if time_hm is None:
        time_hm = datetime.strptime("09:00", "%H:%M").time()
    start = datetime.combine(date, time_hm)
    end = start + timedelta(hours=1)
    end_time = _parse_hm(item.get("end_time"))
    if end_time is not None:
        end = datetime.combine(date, end_time)
        if end <= start:
            end = end + timedelta(days=1)
    location = item.get("location") or None
    link = item.get("link") or None
    notes = item.get("notes") or None

    base = f"From email '{email.subject}' ({email.from_email})"
    note_parts = [n for n in (notes, link, base) if n]
    note_text = " | ".join(note_parts)

    if kind == "exam":
        if _exam_exists(db, title, start):
            return {"kind": kind, "action": "skipped", "reason": "duplicate"}
        db.add(
            Exam(
                title=title,
                subject=item.get("course") or email.subject or None,
                exam_date=start,
                duration_minutes=180,
                location=location,
                notes=note_text,
                status="upcoming",
            )
        )
        return {"kind": kind, "action": "created"}

    if kind in ("assignment",):
        if _assignment_exists(db, title, start):
            return {"kind": kind, "action": "skipped", "reason": "duplicate"}
        db.add(
            Assignment(
                title=title,
                course=item.get("course") or None,
                due_date=start,
                status="todo",
                priority="high",
                source="email",
                external_url=link,
            )
        )
        return {"kind": kind, "action": "created"}

    # meeting / review / event -> calendar event
    if _event_exists(db, title, start):
        return {"kind": kind, "action": "skipped", "reason": "duplicate"}
    db.add(
        Event(
            title=title,
            description=note_text,
            location=location,
            start_time=start,
            end_time=end,
            source="email",
        )
    )
    return {"kind": kind, "action": "created"}


async def process_email(email_id: int) -> dict:
    """Analyze one email (category/summary/deadline) and auto-schedule any
    meetings, reviews, exams, or assignments mentioned in it."""
    db = SessionLocal()
    email = db.query(Email).filter(Email.id == email_id).first()
    if not email:
        db.close()
        return {"email_id": email_id, "error": "not found"}

    result = {
        "email_id": email_id,
        "category": email.category,
        "scheduled_items": [],
    }
    try:
        analysis = await ai_service.analyze_email(email.subject or "", email.body_text or "")
        email.category = analysis.get("category", email.category or "unknown")
        email.summary = analysis.get("summary", email.summary)
        if analysis.get("has_deadline"):
            email.has_deadline = True
            email.deadline_description = analysis.get("deadline_description")
            if analysis.get("deadline"):
                try:
                    email.deadline_at = datetime.fromisoformat(analysis["deadline"])
                except (ValueError, TypeError):
                    pass
        reply = analysis.get("suggested_reply")
        if reply:
            email.suggested_reply = reply
        result["category"] = email.category

        schedule = await ai_service.extract_schedule(email.subject or "", email.body_text or "")
        for item in schedule.get("items", []):
            outcome = _apply_schedule_item(db, email, item)
            result["scheduled_items"].append(outcome)

        db.commit()
    except Exception as exc:
        logger.exception("Failed to process email %s", email_id)
        db.rollback()
        result["error"] = str(exc)
    finally:
        db.close()
    return result


def process_email_sync(email_id: int) -> dict:
    """Synchronous wrapper for the background thread."""
    import asyncio

    return asyncio.run(process_email(email_id))
