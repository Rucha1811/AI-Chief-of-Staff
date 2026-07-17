from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class EmailResponse(BaseModel):
    id: int
    message_id: str
    thread_id: Optional[str] = None
    from_email: str
    from_name: Optional[str] = None
    to_email: Optional[str] = None
    subject: str
    body_text: Optional[str] = None
    received_at: Optional[datetime] = None
    category: str
    is_read: bool
    is_starred: bool
    has_deadline: bool
    deadline_at: Optional[datetime] = None
    deadline_description: Optional[str] = None
    suggested_reply: Optional[str] = None
    summary: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class EmailCategoryUpdate(BaseModel):
    category: str


class EmailReplyRequest(BaseModel):
    email_id: int
    context: Optional[str] = None


class EmailReplyResponse(BaseModel):
    email_id: int
    suggested_reply: str


class EmailSummaryRequest(BaseModel):
    email_id: int


class EmailSummaryResponse(BaseModel):
    email_id: int
    summary: str
    has_deadline: bool
    deadline_at: Optional[datetime] = None
    deadline_description: Optional[str] = None
