import logging
from datetime import datetime, timedelta
import re

from app.core.database import SessionLocal
from app.core.config import settings
from app.core.db_operations import run_with_retry
from app.models.assignment import Assignment
from app.models.email import Email
from app.models.event import Event
from app.models.exam import Exam
from app.models.task import Task
from app.services.ai_service import ai_service

logger = logging.getLogger(__name__)


class AITemporaryError(RuntimeError):
    """Raised when Ollama is temporarily unavailable and the email should be
    retried later (not permanently marked failed)."""


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


def _heuristic_schedule(subject: str, body: str, received_at=None) -> dict:
    """Extract common explicit date/time patterns without the optional AI."""
    text = f"{subject or ''}\n{body or ''}"
    lower = text.lower()
    reference = received_at.date() if received_at else datetime.now().date()
    target_date = None

    relative = re.search(r"\b(today|tomorrow)\b", lower)
    if relative:
        target_date = reference + timedelta(days=1 if relative.group(1) == "tomorrow" else 0)

    date_match = re.search(r"\b(\d{1,2})[-/](\d{1,2})[-/](\d{4})\b", text)
    if date_match:
        try:
            target_date = datetime(
                int(date_match.group(3)), int(date_match.group(2)), int(date_match.group(1))
            ).date()
        except ValueError:
            pass
    if target_date is None:
        month_match = re.search(
            r"\b(?:on\s+)?(\d{1,2})\s+"
            r"(January|February|March|April|May|June|July|August|September|October|November|December)"
            r"(?:\s+(\d{4}))?\b",
            text,
            re.IGNORECASE,
        )
        if month_match:
            try:
                parsed = datetime.strptime(
                    f"{month_match.group(1)} {month_match.group(2)} "
                    f"{month_match.group(3) or reference.year}",
                    "%d %B %Y",
                )
                target_date = parsed.date()
            except ValueError:
                pass

    time_match = re.search(
        r"\b(?:at|from)\s+(\d{1,2})(?::(\d{2}))?\s*(AM|PM)?\b",
        text,
        re.IGNORECASE,
    )
    if not target_date or not time_match:
        return {"items": []}
    hour = int(time_match.group(1))
    minute = int(time_match.group(2) or 0)
    meridiem = (time_match.group(3) or "").lower()
    if meridiem == "pm" and hour < 12:
        hour += 12
    elif meridiem == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return {"items": []}

    kind = "exam" if re.search(r"\b(exam|examination|test|midterm|cie)\b", lower) else "meeting"
    return {
        "items": [{
            "kind": kind,
            "title": subject.strip() or "Scheduled item",
            "date": target_date.isoformat(),
            "time": f"{hour:02d}:{minute:02d}",
            "end_time": None,
            "location": None,
            "link": None,
            "notes": "Extracted from email without AI.",
        }]
    }


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


def _event_exists(db, title, start_time, user_id):
    from sqlalchemy import func

    day = start_time.date()
    return any(
        _titles_match(e.title, title)
        for e in db.query(Event)
        .filter(func.date(Event.start_time) == day.isoformat(), Event.user_id == user_id)
        .all()
    )


def _exam_exists(db, title, exam_date, user_id):
    from sqlalchemy import func

    day = exam_date.date()
    return any(
        _titles_match(e.title, title)
        for e in db.query(Exam)
        .filter(func.date(Exam.exam_date) == day.isoformat(), Exam.user_id == user_id)
        .all()
    )


def _assignment_exists(db, title, due_date, user_id):
    from sqlalchemy import func

    day = due_date.date()
    return any(
        _titles_match(a.title, title)
        for a in db.query(Assignment)
        .filter(func.date(Assignment.due_date) == day.isoformat(), Assignment.user_id == user_id)
        .all()
    )


def _email_task_exists(db, email_id: int, user_id) -> bool:
    if (
        db.query(Task)
        .filter(
            Task.user_id == user_id,
            Task.source == "email",
            Task.source_id == email_id,
        )
        .first()
        is not None
    ):
        return True
    return False


async def _ensure_deadline_task(
    email_id: int, user_id, subject: str, deadline_at, description: str | None
) -> bool:
    if deadline_at is None:
        return False

    def _write():
        with SessionLocal() as db:
            if _email_task_exists(db, email_id, user_id):
                return False
            task = Task(
                title=f"Complete: {subject or 'Email deadline'}",
                description=description or "Action required from email.",
                priority="high",
                status="pending",
                source="email",
                source_id=email_id,
                due_date=deadline_at,
                estimated_minutes=30,
                user_id=user_id,
            )
            db.add(task)
            db.flush()
            db.query(Email).filter(Email.id == email_id).update(
                {"created_task_id": task.id}, synchronize_session=False
            )
            db.commit()
            return True

    return run_with_retry(_write, description=f"create deadline task email {email_id}")


async def _read_email_for_processing(email_id: int):
    """Read an email's fields, returning the (email, dead) tuple.

    Uses a fresh session and NEVER holds it open while AI work happens.
    Re-raises recoverable connection errors so callers can retry.
    """
    def _read():
        with SessionLocal() as db:
            email = db.query(Email).filter(Email.id == email_id).first()
            if not email:
                return None, False, None
            # Snapshot the fields we need BEFORE any slow external call.
            snap = {
                "id": email.id,
                "user_id": email.user_id,
                "subject": email.subject,
                "body_text": email.body_text,
                "category": email.category,
                "summary": email.summary,
                "has_deadline": email.has_deadline,
                "deadline_at": email.deadline_at,
                "deadline_description": email.deadline_description,
                "suggested_reply": email.suggested_reply,
                "from_email": email.from_email,
                "received_at": email.received_at,
            }
            return email.id, True, snap

    try:
        return run_with_retry(_read, description=f"read email {email_id}")
    except Exception:
        raise


async def _save_analysis_results(email_id: int, updates: dict) -> dict:
    """Persist AI analysis results in a fresh session (no slow calls).

    ``updates`` holds the new category/summary/deadline fields; we only set the
    keys present so we never overwrite unrelated state. This is a short,
    idempotent-ish write that is safe to retry.
    """
    def _write():
        with SessionLocal() as db:
            email = db.query(Email).filter(Email.id == email_id).first()
            if not email:
                return {"email_id": email_id, "error": "not found"}
            for key, value in updates.items():
                if hasattr(email, key) and value is not None:
                    setattr(email, key, value)
            db.commit()
            return {"email_id": email_id}

    return run_with_retry(_write, description=f"save email {email_id} analysis")


async def _apply_schedule_and_save(
    email_id: int, subject: str, body: str, user_id, received_at=None
) -> list:
    """Run schedule extraction (external AI) then persist resulting items."""
    try:
        schedule = await ai_service.extract_schedule(subject or "", body or "")
        if not schedule.get("items"):
            heuristic = _heuristic_schedule(subject, body, received_at)
            if heuristic["items"]:
                logger.info(
                    "AI returned no schedule for email %s; using local parser",
                    email_id,
                )
                schedule = heuristic
    except Exception as exc:
        logger.warning(
            "AI schedule extraction unavailable for email %s; using local parser: %s",
            email_id,
            exc,
        )
        schedule = _heuristic_schedule(subject, body, received_at)
    outcomes = []
    for item in schedule.get("items", []):
        out = await _apply_schedule_item_row(email_id, item, user_id)
        outcomes.append(out)
    return outcomes


async def _apply_schedule_item_row(email_id: int, item: dict, user_id) -> dict:
    """Create exam/assignment/event rows for one schedule item using a fresh
    session so the transaction is not held open across any external call."""
    def _apply():
        with SessionLocal() as db:
            kind = str(item.get("kind", "")).lower()
            title = str(item.get("title", "")).strip()
            if not title:
                return {"kind": kind, "action": "skipped", "reason": "no title"}
            date = _parse_ymd(item.get("date"))
            if date is None:
                return {"kind": kind, "action": "skipped", "reason": "no date"}
            time_hm = _parse_hm(item.get("time")) or _parse_hm(item.get("end_time"))
            if time_hm is None:
                time_hm = datetime.strptime(settings.DEFAULT_SCHEDULE_TIME, "%H:%M").time()
            start = datetime.combine(date, time_hm)
            end = start + timedelta(minutes=settings.DEFAULT_SCHEDULE_DURATION_MINUTES)
            end_time = _parse_hm(item.get("end_time"))
            if end_time is not None:
                end = datetime.combine(date, end_time)
                if end <= start:
                    end = end + timedelta(days=1)
            location = item.get("location") or None
            link = item.get("link") or None
            notes = item.get("notes") or None

            email = db.query(Email).filter(Email.id == email_id).first()
            subject = email.subject if email else ""
            from_email = email.from_email if email else ""
            base = f"From email '{subject}' ({from_email})"
            note_parts = [n for n in (notes, link, base) if n]
            note_text = " | ".join(note_parts)

            if kind == "exam":
                if _exam_exists(db, title, start, user_id):
                    exam_created = False
                else:
                    db.add(
                        Exam(
                            title=title,
                            subject=item.get("course") or subject or None,
                            exam_date=start,
                            duration_minutes=settings.DEFAULT_EXAM_DURATION_MINUTES,
                            location=location,
                            notes=note_text,
                            status="upcoming",
                            user_id=user_id,
                        )
                    )
                    exam_created = True
                if not _email_task_exists(db, email_id, user_id):
                    task = Task(
                        title=f"Study for {title}",
                        description=f"Prepare for {title} scheduled on {start.isoformat()}. {note_text}",
                        priority="high",
                        status="pending",
                        source="email",
                        source_id=email_id,
                        due_date=start,
                        estimated_minutes=120,
                        user_id=user_id,
                    )
                    db.add(task)
                    db.flush()
                    db.query(Email).filter(Email.id == email_id).update(
                        {"created_task_id": task.id}, synchronize_session=False
                    )
                    task_created = True
                else:
                    task_created = False
                db.commit()
                return {
                    "kind": kind,
                    "action": "created" if exam_created or task_created else "skipped",
                    "task_created": task_created,
                }

            if kind in ("assignment",):
                if _assignment_exists(db, title, start, user_id):
                    assignment_created = False
                else:
                    db.add(
                        Assignment(
                            title=title,
                            course=item.get("course") or None,
                            due_date=start,
                            status="todo",
                            priority="high",
                            source="email",
                            external_url=link,
                            user_id=user_id,
                        )
                    )
                    assignment_created = True
                if not _email_task_exists(db, email_id, user_id):
                    task = Task(
                        title=f"Complete {title}",
                        description=f"Complete the assignment by {start.isoformat()}. {note_text}",
                        priority="high",
                        status="pending",
                        source="email",
                        source_id=email_id,
                        due_date=start,
                        estimated_minutes=60,
                        user_id=user_id,
                    )
                    db.add(task)
                    db.flush()
                    db.query(Email).filter(Email.id == email_id).update(
                        {"created_task_id": task.id}, synchronize_session=False
                    )
                    task_created = True
                else:
                    task_created = False
                db.commit()
                return {
                    "kind": kind,
                    "action": "created" if assignment_created or task_created else "skipped",
                    "task_created": task_created,
                }

            if _event_exists(db, title, start, user_id):
                return {"kind": kind, "action": "skipped", "reason": "duplicate"}
            db.add(
                Event(
                    title=title,
                    description=note_text,
                    location=location,
                    start_time=start,
                    end_time=end,
                    source="email",
                    user_id=user_id,
                )
            )
            db.commit()
            return {"kind": kind, "action": "created"}

    try:
        return run_with_retry(_apply, description=f"schedule item email {email_id}")
    except Exception as exc:
        logger.exception("Failed to apply schedule item for email %s", email_id)
        return {"kind": item.get("kind"), "action": "error", "reason": str(exc)}


async def process_email(email_id: int) -> dict:
    """Analyze one email (category/summary/deadline) and auto-schedule any
    meetings, reviews, exams, or assignments mentioned in it.

    RELIABILITY: the DB session is opened, read, and closed BEFORE any Ollama
    call. Results are then persisted in a fresh short session. This prevents
    an open transaction from being killed by idle_in_transaction_session_timeout
    while waiting on the slow AI model, and never holds a connection across an
    external network call.
    """
    result = {
        "email_id": email_id,
        "user_id": None,
        "category": None,
        "scheduled_items": [],
    }

    email_id, exists, snap = await _read_email_for_processing(email_id)
    if not exists:
        return {"email_id": email_id, "error": "not found"}

    subject = snap["subject"]
    body = snap["body_text"]
    user_id = snap["user_id"]
    result["user_id"] = user_id
    result["category"] = snap["category"]

    updates: dict = {}
    try:
        analysis = await ai_service.analyze_email(subject or "", body or "")
        category = analysis.get("category")
        summary = analysis.get("summary")
        if category:
            updates["category"] = category
        if summary:
            updates["summary"] = summary
        if analysis.get("has_deadline"):
            updates["has_deadline"] = True
            updates["deadline_description"] = analysis.get("deadline_description")
            deadline = analysis.get("deadline")
            if deadline:
                try:
                    updates["deadline_at"] = datetime.fromisoformat(deadline)
                except (ValueError, TypeError):
                    pass
        reply = analysis.get("suggested_reply")
        if reply:
            updates["suggested_reply"] = reply
        result["category"] = updates.get("category", result["category"])

        # Analyze fresh results with a fresh session (short, safe write).
        if updates:
            saved = await _save_analysis_results(email_id, updates)
            if saved.get("error"):
                result["error"] = saved["error"]
                return result
            if updates.get("deadline_at") is not None:
                await _ensure_deadline_task(
                    email_id,
                    user_id,
                    subject,
                    updates["deadline_at"],
                    updates.get("deadline_description"),
                )

        scheduled = await _apply_schedule_and_save(
            email_id, subject, body, user_id, snap["received_at"]
        )
        result["scheduled_items"] = scheduled
        return result

    except Exception as exc:
        logger.warning(
            "AI email analysis unavailable for email %s; continuing with schedule parsing: %s",
            email_id,
            exc,
        )
        scheduled = await _apply_schedule_and_save(
            email_id, subject, body, user_id, snap["received_at"]
        )
        result["scheduled_items"] = scheduled
        return result


def process_email_sync(email_id: int) -> dict:
    """Synchronous wrapper for the background thread."""
    import asyncio

    return asyncio.run(process_email(email_id))
