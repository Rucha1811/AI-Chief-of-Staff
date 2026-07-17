from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class DocumentResponse(BaseModel):
    id: int
    filename: str
    file_type: str
    file_size: int
    summary: Optional[str] = None
    key_points: Optional[str] = None
    deadlines: Optional[str] = None
    is_indexed: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class DocumentQuery(BaseModel):
    document_id: int
    question: str


class DocumentQueryResponse(BaseModel):
    document_id: int
    question: str
    answer: str
