from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class AttendanceResponse(BaseModel):
    id: int
    course: str
    date: datetime
    status: str = "present"
    notes: Optional[str] = None

    class Config:
        from_attributes = True


class AttendanceCreate(BaseModel):
    course: str
    date: datetime
    status: str = "present"
    notes: Optional[str] = None


class AttendanceStats(BaseModel):
    total: int
    present: int
    absent: int
    late: int
    excused: int
    percentage: float
