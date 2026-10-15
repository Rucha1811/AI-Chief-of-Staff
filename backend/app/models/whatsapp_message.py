from sqlalchemy import BigInteger, Column, ForeignKey, Integer, String, Text, DateTime

from app.core.database import Base
from app.models.base import TimestampMixin


class WhatsappMessage(Base, TimestampMixin):
    __tablename__ = "whatsapp_messages"

    id = Column(Integer, primary_key=True, index=True)
    message_id = Column(String(128), unique=True, index=True, nullable=True)
    direction = Column(String(8), default="in")  # in | out
    from_number = Column(String(32), nullable=False)
    to_number = Column(String(32), nullable=False)
    text = Column(Text, nullable=True)
    provider = Column(String(16), default="twilio")
    user_id = Column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )