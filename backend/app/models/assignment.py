from sqlalchemy import BigInteger, Column, ForeignKey, Integer, String, Text, DateTime
from app.core.database import Base
from app.models.base import TimestampMixin


class Assignment(Base, TimestampMixin):
    __tablename__ = "assignments"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(512))
    course = Column(String(256), nullable=True)
    due_date = Column(DateTime, nullable=True)
    status = Column(String(16), default="todo")
    points_earned = Column(Integer, nullable=True)
    points_max = Column(Integer, nullable=True)
    priority = Column(String(16), default="medium")
    source = Column(String(32), default="user")
    source_id = Column(String(256), nullable=True)
    external_url = Column(String(512), nullable=True)
    user_id = Column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
