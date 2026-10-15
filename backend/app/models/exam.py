from sqlalchemy import BigInteger, Column, ForeignKey, Integer, String, Text, DateTime
from app.core.database import Base
from app.models.base import TimestampMixin


class Exam(Base, TimestampMixin):
    __tablename__ = "exams"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(512))
    subject = Column(String(256), nullable=True)
    exam_date = Column(DateTime, nullable=False)
    duration_minutes = Column(Integer, nullable=True)
    location = Column(String(256), nullable=True)
    notes = Column(Text, nullable=True)
    status = Column(String(16), default="upcoming")
    score = Column(Integer, nullable=True)
    max_score = Column(Integer, nullable=True)
    user_id = Column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
