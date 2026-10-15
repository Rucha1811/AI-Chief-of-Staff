from sqlalchemy import BigInteger, Column, ForeignKey, Integer, String, Text, UniqueConstraint

from app.core.database import Base
from app.models.base import TimestampMixin


class Contact(Base, TimestampMixin):
    __tablename__ = "contacts"
    __table_args__ = (UniqueConstraint("user_id", "resource_name", name="ux_contacts_user_resource"),)

    id = Column(Integer, primary_key=True, index=True)
    resource_name = Column(String(256), nullable=False)
    display_name = Column(String(512), nullable=False)
    emails = Column(Text, nullable=True)
    phone_numbers = Column(Text, nullable=True)
    user_id = Column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
