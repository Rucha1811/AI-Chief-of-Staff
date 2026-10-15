from sqlalchemy import BigInteger, Column, ForeignKey, Integer, String, Text, Boolean, DateTime
from app.core.database import Base
from app.models.base import TimestampMixin


class Document(Base, TimestampMixin):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String(512))
    file_type = Column(String(16))
    file_size = Column(Integer)
    storage_path = Column(String(1024))
    summary = Column(Text, nullable=True)
    key_points = Column(Text, nullable=True)
    deadlines = Column(Text, nullable=True)
    raw_text = Column(Text, nullable=True)
    is_indexed = Column(Boolean, default=False)
    user_id = Column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
