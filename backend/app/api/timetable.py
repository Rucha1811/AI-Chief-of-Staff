from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import date, datetime, time, timedelta
from typing import Optional

from app.core.database import get_db
from app.models.event import Event
from app.models.timetable import TimetableEntry
from app.schemas.timetable import (
    TimetableEntryResponse,
    TimetableEntryCreate,
    TimetableEntryUpdate,
    TimetableScheduleResponse,
)

router = APIRouter(prefix="/api/timetable", tags=["timetable"])

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _to_entry(entry: TimetableEntry) -> dict:
    return {
        "id": entry.id,
        "title": entry.title,
        "location": entry.location,
        "day_of_week": entry.day_of_week,
        "day_name": DAYS[entry.day_of_week] if 0 <= entry.day_of_week <= 6 else "?",
        "start_time": entry.start_time,
        "end_time": entry.end_time,
        "source": entry.source,
        "active": entry.active,
    }


@router.get("")
async def list_timetable(db: Session = Depends(get_db)) -> list[dict]:
    entries = db.query(TimetableEntry).order_by(TimetableEntry.day_of_week, TimetableEntry.start_time).all()
    return [_to_entry(e) for e in entries]


@router.post("")
async def create_timetable_entry(entry: TimetableEntryCreate, db: Session = Depends(get_db)) -> dict:
    if entry.day_of_week not in range(0, 7):
        raise HTTPException(status_code=422, detail="day_of_week must be 0 (Monday) to 6 (Sunday)")
    db_entry = TimetableEntry(**entry.model_dump())
    db.add(db_entry)
    db.commit()
    db.refresh(db_entry)
    return _to_entry(db_entry)


@router.put("/{entry_id}")
async def update_timetable_entry(
    entry_id: int, update: TimetableEntryUpdate, db: Session = Depends(get_db)
) -> dict:
    entry = db.query(TimetableEntry).filter(TimetableEntry.id == entry_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Timetable entry not found")
    for key, val in update.model_dump(exclude_unset=True).items():
        setattr(entry, key, val)
    db.commit()
    db.refresh(entry)
    return _to_entry(entry)


@router.delete("/{entry_id}")
async def delete_timetable_entry(entry_id: int, db: Session = Depends(get_db)) -> dict:
    entry = db.query(TimetableEntry).filter(TimetableEntry.id == entry_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Timetable entry not found")
    db.delete(entry)
    db.commit()
    return {"ok": True}


def _parse_hhmm(value: str) -> time:
    try:
        hour, minute = value.split(":")
        return time(int(hour), int(minute))
    except (ValueError, AttributeError):
        raise HTTPException(status_code=422, detail=f"Invalid time: {value!r} (use HH:MM)")


@router.post("/schedule")
async def schedule_week(
    date_from: date,
    date_to: Optional[date] = None,
    overwrite: bool = False,
    db: Session = Depends(get_db),
) -> TimetableScheduleResponse:
    if not date_to:
        date_to = date_from
    if date_to < date_from:
        raise HTTPException(status_code=422, detail="date_to must be on or after date_from")

    if overwrite:
        db.query(Event).filter(
            Event.source == "timetable",
            Event.start_time >= datetime.combine(date_from, datetime.min.time()),
            Event.start_time <= datetime.combine(date_to, datetime.max.time()),
        ).delete(synchronize_session=False)
        db.commit()

    entries = db.query(TimetableEntry).filter(TimetableEntry.active == True).all()

    created = 0
    skipped = 0
    cursor = date_from
    while cursor <= date_to:
        for entry in entries:
            if entry.day_of_week != cursor.weekday():
                continue
            start_t = _parse_hhmm(entry.start_time)
            end_t = _parse_hhmm(entry.end_time)
            if end_t <= start_t:
                skipped += 1
                continue
            start_dt = datetime.combine(cursor, start_t)
            end_dt = datetime.combine(cursor, end_t)
            exists = (
                db.query(Event)
                .filter(
                    Event.title == entry.title,
                    Event.start_time == start_dt,
                    Event.source == "timetable",
                )
                .first()
            )
            if exists:
                skipped += 1
                continue
            db.add(
                Event(
                    title=entry.title,
                    location=entry.location,
                    start_time=start_dt,
                    end_time=end_dt,
                    source="timetable",
                )
            )
            created += 1
        cursor += timedelta(days=1)
    db.commit()

    return TimetableScheduleResponse(
        date_from=date_from.isoformat(),
        date_to=date_to.isoformat(),
        created=created,
        skipped=skipped,
    )
