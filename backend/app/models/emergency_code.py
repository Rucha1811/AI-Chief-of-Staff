from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
)
from app.core.database import Base
from app.models.base import TimestampMixin


class EmergencyCode(Base, TimestampMixin):
    __tablename__ = "emergency_codes"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code_hash = Column(String(256), nullable=False)
    used = Column(Boolean, default=False)
    consumed_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=False)
    created_by = Column(String(64), nullable=True)
