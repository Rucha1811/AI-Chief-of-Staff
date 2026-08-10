from sqlalchemy import Column, Integer, String, Text, DateTime
from app.core.database import Base
from app.models.base import TimestampMixin


class PushSubscription(Base, TimestampMixin):
    __tablename__ = "push_subscriptions"

    id = Column(Integer, primary_key=True, index=True)
    endpoint = Column(String(1024), unique=True, index=True)
    p256dh = Column(String(512))
    auth = Column(String(512))
    expires_at = Column(DateTime, nullable=True)
