from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class TaskResponse(BaseModel):
    id: int
    title: str
    description: Optional[str] = None
    priority: str
    status: str
    source: str
    source_id: Optional[int] = None
    due_date: Optional[datetime] = None
    estimated_minutes: Optional[int] = None
    is_recurring: bool
    recurrence_rule: Optional[str] = None
    completed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class TaskCreate(BaseModel):
    title: str
    description: Optional[str] = None
    priority: str = "medium"
    due_date: Optional[datetime] = None
    estimated_minutes: Optional[int] = None
    source: str = "user"
    source_id: Optional[int] = None


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    priority: Optional[str] = None
    status: Optional[str] = None
    due_date: Optional[datetime] = None
    estimated_minutes: Optional[int] = None


class TaskRePrioritize(BaseModel):
    tasks: list[int]
