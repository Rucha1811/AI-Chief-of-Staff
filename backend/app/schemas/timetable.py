from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class TimetableEntryResponse(BaseModel):
    id: int
    title: str
    location: Optional[str] = None
    day_of_week: int
    start_time: str
    end_time: str
    source: str
    active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class TimetableEntryCreate(BaseModel):
    title: str
    location: Optional[str] = None
    day_of_week: int
    start_time: str
    end_time: str
    source: str = "college"
    active: bool = True


class TimetableEntryUpdate(BaseModel):
    title: Optional[str] = None
    location: Optional[str] = None
    day_of_week: Optional[int] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    source: Optional[str] = None
    active: Optional[bool] = None


class TimetableScheduleResponse(BaseModel):
    date_from: str
    date_to: str
    created: int
    skipped: int
