from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime
from app.core.database import Base
from app.models.base import TimestampMixin


class Notification(Base, TimestampMixin):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, index=True)
    type = Column(String(32), index=True)
    title = Column(String(512))
    message = Column(Text)
    entity_type = Column(String(32), nullable=True)
    entity_id = Column(Integer, nullable=True)
    priority = Column(String(16), default="normal")
    is_read = Column(Boolean, default=False)
    fingerprint = Column(String(512), unique=True, index=True)
