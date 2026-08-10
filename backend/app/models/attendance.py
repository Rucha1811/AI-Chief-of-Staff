from sqlalchemy import Column, Integer, String, Text, DateTime
from app.core.database import Base
from app.models.base import TimestampMixin


class Attendance(Base, TimestampMixin):
    __tablename__ = "attendance"

    id = Column(Integer, primary_key=True, index=True)
    course = Column(String(256))
    date = Column(DateTime, nullable=False)
    status = Column(String(16), default="present")
    notes = Column(Text, nullable=True)
