from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class AssignmentResponse(BaseModel):
    id: int
    title: str
    course: Optional[str] = None
    due_date: Optional[datetime] = None
    status: str = "todo"
    points_earned: Optional[int] = None
    points_max: Optional[int] = None
    priority: str = "medium"
    source: str = "user"
    source_id: Optional[str] = None
    external_url: Optional[str] = None

    class Config:
        from_attributes = True


class AssignmentCreate(BaseModel):
    title: str
    course: Optional[str] = None
    due_date: Optional[datetime] = None
    status: str = "todo"
    points_earned: Optional[int] = None
    points_max: Optional[int] = None
    priority: str = "medium"


class AssignmentUpdate(BaseModel):
    title: Optional[str] = None
    course: Optional[str] = None
    due_date: Optional[datetime] = None
    status: Optional[str] = None
    points_earned: Optional[int] = None
    points_max: Optional[int] = None
    priority: Optional[str] = None
