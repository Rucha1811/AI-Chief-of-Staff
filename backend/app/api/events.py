from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime, date, timedelta
from typing import Optional
import time

from app.core.database import get_db
from app.models.event import Event
from app.schemas.event import EventResponse, EventCreate, EventUpdate, FreeSlotsResponse, FreeSlot
from app.services.ai_service import ai_service
from app.services.recurrence import expand_recurrences

router = APIRouter(prefix="/api/events", tags=["events"])

_free_slots_cache: dict[str, tuple[float, list]] = {}
_FREE_SLOTS_TTL = 1800


def _invalidate_free_slots_cache():
    _free_slots_cache.clear()


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


@router.get("")
async def list_events(
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
) -> list[dict]:
    query = db.query(Event)
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
async def create_event(event: EventCreate, db: Session = Depends(get_db)) -> EventResponse:
    _invalidate_free_slots_cache()
    db_event = Event(**event.model_dump())
    db.add(db_event)
    db.commit()
    db.refresh(db_event)
    return db_event


@router.get("/{event_id}")
async def get_event(event_id: int, db: Session = Depends(get_db)) -> EventResponse:
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    return event


@router.put("/{event_id}")
async def update_event(
    event_id: int, update: EventUpdate, db: Session = Depends(get_db)
) -> EventResponse:
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    _invalidate_free_slots_cache()
    for key, val in update.model_dump(exclude_unset=True).items():
        setattr(event, key, val)
    db.commit()
    db.refresh(event)
    return event


@router.delete("/{event_id}")
async def delete_event(event_id: int, db: Session = Depends(get_db)):
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    _invalidate_free_slots_cache()
    db.delete(event)
    db.commit()
    return {"ok": True}


@router.get("/conflicts/detect")
async def detect_conflicts(
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    db: Session = Depends(get_db),
) -> list[dict]:
    query = db.query(Event)
    if date_from:
        query = query.filter(Event.start_time >= datetime.combine(date_from, datetime.min.time()))
    if date_to:
        query = query.filter(Event.end_time <= datetime.combine(date_to, datetime.max.time()))
    events = query.all()

    events_text = "\n".join(
        [f"{e.title}: {e.start_time.strftime('%H:%M')}-{e.end_time.strftime('%H:%M')}" for e in events]
    )
    conflicts = await ai_service.detect_calendar_conflicts(events_text)

    for conflict in conflicts:
        e1_name = conflict.get("event1", "")
        e2_name = conflict.get("event2", "")
        for e in events:
            if e.title == e1_name or e.title == e2_name:
                e.is_conflict = True
                e.conflict_description = conflict.get("description", "")
                e.suggested_action = conflict.get("suggestion", "")
        db.commit()

    return conflicts


@router.get("/free-slots/{target_date}")
async def get_free_slots(
    target_date: date, db: Session = Depends(get_db)
) -> FreeSlotsResponse:
    cache_key = target_date.isoformat()
    now = time.time()
    cached = _free_slots_cache.get(cache_key)
    if cached and now - cached[0] < _FREE_SLOTS_TTL:
        return FreeSlotsResponse(date=cache_key, free_slots=[FreeSlot(**s) for s in cached[1]])

    day_start = datetime.combine(target_date, datetime.min.time())
    day_end = datetime.combine(target_date, datetime.max.time())
    events = db.query(Event).filter(
        Event.start_time >= day_start,
        Event.end_time <= day_end,
    ).all()

    events_text = "\n".join(
        [f"{e.title}: {e.start_time.strftime('%H:%M')}-{e.end_time.strftime('%H:%M')}" for e in events]
    )
    slots = await ai_service.suggest_free_slots(events_text, target_date.isoformat())

    free_slots = [s for s in slots if isinstance(s, dict)]
    _free_slots_cache[cache_key] = (now, free_slots)
    return FreeSlotsResponse(date=cache_key, free_slots=[FreeSlot(**s) for s in free_slots])


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
    db: Session = Depends(get_db),
) -> dict:
    if not date_to:
        date_to = date_from
    if date_to < date_from:
        raise HTTPException(status_code=422, detail="date_to must be on or after date_from")

    _invalidate_free_slots_cache()

    if overwrite:
        db.query(Event).filter(
            Event.source == "study",
            Event.start_time >= datetime.combine(date_from, datetime.min.time()),
            Event.start_time <= datetime.combine(date_to, datetime.max.time()),
        ).delete(synchronize_session=False)
        db.commit()

    all_events = db.query(Event).filter(
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


@router.post("/sync-to-google")
async def sync_to_google(
    date_from: date,
    date_to: Optional[date] = None,
    db: Session = Depends(get_db),
) -> dict:
    from app.services.calendar_service import CalendarAuthError, push_events_to_calendar

    if not date_to:
        date_to = date_from
    if date_to < date_from:
        raise HTTPException(status_code=422, detail="date_to must be on or after date_from")

    events = db.query(Event).filter(
        Event.start_time >= datetime.combine(date_from, datetime.min.time()),
        Event.start_time <= datetime.combine(date_to, datetime.max.time()),
    ).all()

    try:
        result = await push_events_to_calendar(db, events)
    except CalendarAuthError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not reach Google Calendar: {e}")

    return {
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        **result,
    }
