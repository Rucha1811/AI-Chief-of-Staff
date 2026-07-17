from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime, date
from typing import Optional

from app.core.database import get_db
from app.models.event import Event
from app.schemas.event import EventResponse, EventCreate, EventUpdate, FreeSlotsResponse, FreeSlot
from app.services.ai_service import ai_service

router = APIRouter(prefix="/api/events", tags=["events"])


@router.get("")
async def list_events(
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
) -> list[EventResponse]:
    query = db.query(Event)
    if date_from:
        query = query.filter(Event.start_time >= datetime.combine(date_from, datetime.min.time()))
    if date_to:
        query = query.filter(Event.end_time <= datetime.combine(date_to, datetime.max.time()))
    return query.order_by(Event.start_time).offset(offset).limit(limit).all()


@router.post("")
async def create_event(event: EventCreate, db: Session = Depends(get_db)) -> EventResponse:
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

    free_slots = [FreeSlot(**s) for s in slots]
    return FreeSlotsResponse(date=target_date.isoformat(), free_slots=free_slots)
