from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class EventResponse(BaseModel):
    id: int
    calendar_event_id: Optional[str] = None
    title: str
    description: Optional[str] = None
    location: Optional[str] = None
    start_time: datetime
    end_time: datetime
    is_all_day: bool
    is_conflict: bool
    conflict_description: Optional[str] = None
    suggested_action: Optional[str] = None
    source: str
    recurrence_rule: Optional[str] = None
    recurrence_until: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class EventCreate(BaseModel):
    title: str
    description: Optional[str] = None
    location: Optional[str] = None
    start_time: datetime
    end_time: datetime
    is_all_day: bool = False
    recurrence_rule: Optional[str] = None
    recurrence_until: Optional[datetime] = None


class EventUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    location: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    is_all_day: Optional[bool] = None
    recurrence_rule: Optional[str] = None
    recurrence_until: Optional[datetime] = None


class FreeSlot(BaseModel):
    start: str
    end: str
    duration_minutes: int


class FreeSlotsResponse(BaseModel):
    date: str
    free_slots: list[FreeSlot]
