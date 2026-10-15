from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime, date, timedelta
from typing import Optional
import logging
import time

logger = logging.getLogger(__name__)

from app.core.config import settings
from app.core.database import get_db
from app.models.event import Event
from app.models.exam import Exam
from app.models.task import Task
from app.models.user import User
from app.schemas.event import EventResponse, EventCreate, EventUpdate, FreeSlotsResponse, FreeSlot
from app.services.recurrence import expand_recurrences
from app.api.deps import get_current_user
from app.core.limits import clamp_limit, clamp_offset

router = APIRouter(prefix="/api/events", tags=["events"])

_free_slots_cache: dict[str, tuple[float, list]] = {}
_FREE_SLOTS_TTL = 1800


def _invalidate_free_slots_cache():
    _free_slots_cache.clear()


def _ev(e, key):
    return e[key] if isinstance(e, dict) else getattr(e, key)


def _serialize(e) -> dict:
    return {
        "id": e.id,
        "calendar_event_id": e.calendar_event_id,
        "title": e.title,
        "description": e.description,
        "location": e.location,
        "start_time": e.start_time,
        "end_time": e.end_time,
        "is_all_day": e.is_all_day,
        "is_conflict": e.is_conflict,
        "conflict_description": e.conflict_description,
        "suggested_action": e.suggested_action,
        "source": e.source,
        "recurrence_rule": getattr(e, "recurrence_rule", None),
        "recurrence_until": getattr(e, "recurrence_until", None),
    }


def _get_owned(db: Session, user_id: int, event_id: int) -> Event:
    event = db.query(Event).filter(Event.user_id == user_id, Event.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    return event


@router.get("")
async def list_events(
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    limit: int = 50,
    offset: int = 0,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    limit = clamp_limit(limit)
    offset = clamp_offset(offset)
    query = db.query(Event).filter(Event.user_id == user.id)
    range_start = datetime.combine(date_from, datetime.min.time()) if date_from else None
    range_end = datetime.combine(date_to, datetime.max.time()) if date_to else None
    if range_start:
        query = query.filter(Event.start_time >= range_start)
    if range_end:
        query = query.filter(Event.end_time <= range_end)
    events = query.order_by(Event.start_time).offset(offset).limit(limit).all()

    expanded = []
    for e in events:
        expanded.append(_serialize(e))
        if range_start and range_end:
            expanded.extend(expand_recurrences(e, range_start, range_end))
    return expanded


@router.post("")
async def create_event(
    event: EventCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EventResponse:
    _invalidate_free_slots_cache()
    db_event = Event(**event.model_dump(), user_id=user.id)
    db.add(db_event)
    db.commit()
    db.refresh(db_event)
    return db_event


@router.get("/{event_id}")
async def get_event(
    event_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EventResponse:
    return _get_owned(db, user.id, event_id)


@router.put("/{event_id}")
async def update_event(
    event_id: int,
    update: EventUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EventResponse:
    event = _get_owned(db, user.id, event_id)
    _invalidate_free_slots_cache()
    for key, val in update.model_dump(exclude_unset=True).items():
        setattr(event, key, val)
    db.commit()
    db.refresh(event)
    return event


@router.delete("/{event_id}")
async def delete_event(
    event_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    event = _get_owned(db, user.id, event_id)
    _invalidate_free_slots_cache()
    db.delete(event)
    db.commit()
    return {"ok": True}


@router.get("/conflicts/detect")
async def detect_conflicts(
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    range_start = datetime.combine(date_from, datetime.min.time()) if date_from else datetime.min
    range_end = datetime.combine(date_to, datetime.max.time()) if date_to else datetime.max

    query = db.query(Event).filter(Event.user_id == user.id)
    if date_from:
        query = query.filter(Event.start_time < range_end)
    if date_to:
        query = query.filter(Event.end_time > range_start)
    events = query.all()

    expanded: list = []
    for e in events:
        expanded.append(e)
        if date_from and date_to:
            expanded.extend(expand_recurrences(e, range_start, range_end))

    def _name(e):
        return _ev(e, "title")

    conflicts: list[dict] = []
    marked: set[tuple[int, int]] = set()
    for i in range(len(expanded)):
        a = expanded[i]
        a_start, a_end = _ev(a, "start_time"), _ev(a, "end_time")
        a_id = _ev(a, "id")
        for j in range(i + 1, len(expanded)):
            b = expanded[j]
            b_start, b_end = _ev(b, "start_time"), _ev(b, "end_time")
            b_id = _ev(b, "id")
            if not (a_start < b_end and b_start < a_end):
                continue
            if (a_id, b_id) in marked or (b_id, a_id) in marked:
                continue
            marked.add((a_id, b_id))
            start = max(a_start, b_start)
            end = min(a_end, b_end)
            minutes = int((end - start).total_seconds() // 60)
            an, bn = _name(a), _name(b)
            conflict = {
                "event1": an,
                "event2": bn,
                "description": (
                    f"{an} overlaps {bn} for {minutes} minutes "
                    f"on {start.date()} ({start:%H:%M} – {end:%H:%M})."
                ),
                "suggestion": f"Consider moving {an} to another free slot to avoid the clash.",
            }
            conflicts.append(conflict)
            for target in (a, b):
                target.is_conflict = True
                target.conflict_description = conflict["description"]
                target.suggested_action = conflict["suggestion"]

    db.commit()
    return conflicts


@router.get("/free-slots/{target_date}")
async def get_free_slots(
    target_date: date,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FreeSlotsResponse:
    cache_key = f"{user.id}:{target_date.isoformat()}"
    now = time.time()
    cached = _free_slots_cache.get(cache_key)
    if cached and now - cached[0] < _FREE_SLOTS_TTL:
        return FreeSlotsResponse(date=target_date.isoformat(), free_slots=[FreeSlot(**s) for s in cached[1]])

    day_start = datetime.combine(target_date, datetime.min.time())
    day_end = datetime.combine(target_date, datetime.max.time())
    events = db.query(Event).filter(
        Event.user_id == user.id,
        Event.start_time < day_end,
        Event.end_time > day_start,
    ).all()
    busy = list(events)
    for e in events:
        busy.extend(expand_recurrences(e, day_start, day_end - timedelta(microseconds=1)))

    work_hours = (settings.DEFAULT_WORK_HOURS or "9:00-18:00").strip()
    try:
        start_str, end_str = [p.strip() for p in work_hours.split("-")]
        start_minutes = int(start_str.split(":")[0]) * 60 + int(start_str.split(":")[1])
        end_minutes = int(end_str.split(":")[0]) * 60 + int(end_str.split(":")[1])
    except (ValueError, IndexError):
        start_minutes, end_minutes = 8 * 60, 22 * 60

    blocks = _free_blocks(busy, day_start, start_minutes, end_minutes)
    free_slots = [
        {
            "start": s.strftime("%H:%M"),
            "end": e.strftime("%H:%M"),
            "duration_minutes": int((e - s).total_seconds() // 60),
        }
        for s, e in blocks
    ]

    _free_slots_cache[cache_key] = (now, free_slots)
    return FreeSlotsResponse(date=target_date.isoformat(), free_slots=[FreeSlot(**s) for s in free_slots])


def _free_blocks(events, day: datetime, start_minutes: int = 8 * 60, end_minutes: int = 22 * 60):
    day_start = day.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    bound_start = day_start + timedelta(minutes=start_minutes)
    bound_end = day_start + timedelta(minutes=end_minutes)

    def _get(e, key):
        return e[key] if isinstance(e, dict) else getattr(e, key)

    busy = []
    for e in events:
        s = max(_get(e, "start_time"), day_start)
        en = min(_get(e, "end_time"), day_end)
        if en > s:
            busy.append((s, en))
    busy.sort()
    merged = []
    for s, en in busy:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], en))
        else:
            merged.append((s, en))

    blocks = []
    cursor = bound_start
    for s, en in merged:
        if s > cursor and (s - cursor) >= timedelta(minutes=30):
            blocks.append((cursor, s))
        cursor = max(cursor, en)
    if bound_end > cursor and (bound_end - cursor) >= timedelta(minutes=30):
        blocks.append((cursor, bound_end))
    return blocks


@router.post("/schedule-study")
async def schedule_study(
    date_from: date,
    date_to: Optional[date] = None,
    minutes_per_day: int = 60,
    title: str = "Study time",
    subject: Optional[str] = None,
    overwrite: bool = False,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    if not date_to:
        date_to = date_from
    if date_to < date_from:
        raise HTTPException(status_code=422, detail="date_to must be on or after date_from")

    _invalidate_free_slots_cache()

    if overwrite:
        db.query(Event).filter(
            Event.user_id == user.id,
            Event.source == "study",
            Event.start_time >= datetime.combine(date_from, datetime.min.time()),
            Event.start_time <= datetime.combine(date_to, datetime.max.time()),
        ).delete(synchronize_session=False)
        db.commit()

    all_events = db.query(Event).filter(
        Event.user_id == user.id,
        Event.start_time >= datetime.combine(date_from, datetime.min.time()),
        Event.start_time <= datetime.combine(date_to, datetime.max.time()),
    ).all()

    created = 0
    per_day: dict[date, int] = {}
    cursor = date_from
    while cursor <= date_to:
        day_start = datetime.combine(cursor, datetime.min.time())
        day_events = [e for e in all_events if e.start_time < day_start + timedelta(days=1) and e.end_time > day_start]
        for e in all_events:
            day_events.extend(expand_recurrences(e, day_start, day_start + timedelta(days=1) - timedelta(microseconds=1)))

        blocks = _free_blocks(day_events, day_start)
        remaining = minutes_per_day
        for start, end in blocks:
            if remaining <= 0:
                break
            avail = int((end - start).total_seconds() // 60)
            take = min(remaining, avail)
            block_title = title if not subject else f"{title}: {subject}"
            db.add(
                Event(
                    title=block_title,
                    description=f"Auto-scheduled study block ({take} min). Subject: {subject}" if subject else None,
                    start_time=start,
                    end_time=start + timedelta(minutes=take),
                    source="study",
                    user_id=user.id,
                )
            )
            created += 1
            remaining -= take
        per_day[cursor] = minutes_per_day - remaining
        cursor += timedelta(days=1)

    db.commit()
    return {
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "minutes_per_day": minutes_per_day,
        "created": created,
        "per_day": {d.isoformat(): m for d, m in per_day.items()},
    }


@router.post("/plan-exams")
async def plan_exam_preparation(
    days_per_exam: int = 3,
    minutes_per_day: int = 120,
    overwrite: bool = False,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Create exam preparation tasks and calendar blocks in existing free time."""
    if days_per_exam < 1 or days_per_exam > 30:
        raise HTTPException(status_code=422, detail="days_per_exam must be between 1 and 30")
    if minutes_per_day < 30 or minutes_per_day > 480:
        raise HTTPException(status_code=422, detail="minutes_per_day must be between 30 and 480")

    today = date.today()
    exams = (
        db.query(Exam)
        .filter(
            Exam.user_id == user.id,
            Exam.status == "upcoming",
            Exam.exam_date > datetime.now(),
        )
        .order_by(Exam.exam_date.asc(), Exam.id.asc())
        .all()
    )
    if not exams:
        return {"exams": 0, "tasks_created": 0, "events_created": 0, "message": "No upcoming exams found."}

    first_day = today
    last_day = max(e.exam_date.date() - timedelta(days=1) for e in exams)
    if last_day < first_day:
        return {"exams": len(exams), "tasks_created": 0, "events_created": 0, "message": "Upcoming exams have no preparation days left."}

    if overwrite:
        db.query(Event).filter(
            Event.user_id == user.id,
            Event.source == "study",
            Event.start_time >= datetime.combine(first_day, datetime.min.time()),
            Event.start_time <= datetime.combine(last_day, datetime.max.time()),
            Event.title.like("Exam preparation:%"),
        ).delete(synchronize_session=False)
        db.query(Task).filter(
            Task.user_id == user.id,
            Task.source == "exam_plan",
        ).delete(synchronize_session=False)
        db.commit()

    existing_events = db.query(Event).filter(
        Event.user_id == user.id,
        Event.start_time <= datetime.combine(last_day, datetime.max.time()),
        Event.end_time >= datetime.combine(first_day, datetime.min.time()),
    ).all()
    planned_event_days = {
        (event.title, event.start_time.date())
        for event in existing_events
        if event.source == "study" and event.title.startswith("Exam preparation:")
    }
    created_events = 0
    created_tasks = 0

    for exam in exams:
        subject = exam.subject or exam.title
        task_title = f"Prepare for {subject}"
        existing_task = db.query(Task).filter(
            Task.user_id == user.id,
            Task.source == "exam_plan",
            Task.source_id == exam.id,
        ).first()
        if not existing_task:
            db.add(Task(
                title=task_title,
                description=f"Review and practice for {exam.title} on {exam.exam_date.date().isoformat()}.",
                priority="high",
                status="pending",
                source="exam_plan",
                source_id=exam.id,
                due_date=exam.exam_date,
                estimated_minutes=days_per_exam * minutes_per_day,
                user_id=user.id,
            ))
            created_tasks += 1

    db.flush()
    cursor = first_day
    while cursor <= last_day:
        active = [
            exam for exam in exams
            if cursor < exam.exam_date.date()
            and cursor >= max(first_day, exam.exam_date.date() - timedelta(days=days_per_exam))
        ]
        if active:
            day_start = datetime.combine(cursor, datetime.min.time())
            day_events = [
                event for event in existing_events
                if event.start_time < day_start + timedelta(days=1) and event.end_time > day_start
            ]
            day_events.extend(
                event for event in existing_events
                if event.start_time.date() == cursor and event not in day_events
            )
            blocks = _free_blocks(day_events, day_start)
            remaining = minutes_per_day
            for exam in sorted(active, key=lambda item: (item.exam_date, item.id)):
                if remaining < 30 or not blocks:
                    break
                event_title = f"Exam preparation: {exam.subject or exam.title}"
                if (event_title, cursor) in planned_event_days:
                    continue
                start, end = blocks.pop(0)
                take = min(45, remaining, int((end - start).total_seconds() // 60))
                if take < 30:
                    continue
                event = Event(
                    title=event_title,
                    description=f"Preparation for {exam.title}.",
                    start_time=start,
                    end_time=start + timedelta(minutes=take),
                    source="study",
                    user_id=user.id,
                )
                db.add(event)
                existing_events.append(event)
                planned_event_days.add((event_title, cursor))
                created_events += 1
                remaining -= take
        cursor += timedelta(days=1)

    db.commit()
    _invalidate_free_slots_cache()
    return {
        "exams": len(exams),
        "tasks_created": created_tasks,
        "events_created": created_events,
        "date_from": first_day.isoformat(),
        "date_to": last_day.isoformat(),
    }


@router.post("/sync-to-google")
async def sync_to_google(
    date_from: date,
    date_to: Optional[date] = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    from app.services.calendar_service import CalendarAuthError, push_events_to_calendar

    if not date_to:
        date_to = date_from
    if date_to < date_from:
        raise HTTPException(status_code=422, detail="date_to must be on or after date_from")

    events = db.query(Event).filter(
        Event.user_id == user.id,
        Event.start_time >= datetime.combine(date_from, datetime.min.time()),
        Event.start_time <= datetime.combine(date_to, datetime.max.time()),
    ).all()

    try:
        result = await push_events_to_calendar(db, events, user_id=user.id)
    except CalendarAuthError as e:
        logger.warning("calendar auth failure for user %s: %s", user.id, e)
        raise HTTPException(
            status_code=403,
            detail="Calendar authorization required. Reconnect Google in Settings.",
        )
    except Exception as e:
        logger.warning("calendar push failed for user %s: %s", user.id, e)
        raise HTTPException(
            status_code=502, detail="Could not reach Google Calendar."
        )

    return {
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        **result,
    }