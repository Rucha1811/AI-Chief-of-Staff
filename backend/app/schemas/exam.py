from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class ExamResponse(BaseModel):
    id: int
    title: str
    subject: Optional[str] = None
    exam_date: datetime
    duration_minutes: Optional[int] = None
    location: Optional[str] = None
    notes: Optional[str] = None
    status: str = "upcoming"
    score: Optional[int] = None
    max_score: Optional[int] = None

    class Config:
        from_attributes = True


class ExamCreate(BaseModel):
    title: str
    subject: Optional[str] = None
    exam_date: datetime
    duration_minutes: Optional[int] = None
    location: Optional[str] = None
    notes: Optional[str] = None
    status: str = "upcoming"
    score: Optional[int] = None
    max_score: Optional[int] = None


class ExamUpdate(BaseModel):
    title: Optional[str] = None
    subject: Optional[str] = None
    exam_date: Optional[datetime] = None
    duration_minutes: Optional[int] = None
    location: Optional[str] = None
    notes: Optional[str] = None
    status: Optional[str] = None
    score: Optional[int] = None
    max_score: Optional[int] = None
