"""Model registry.

Importing every model module here guarantees all tables are present on
Base.metadata (used by Alembic autogenerate and create_all).
"""

from app.models.google_auth_state import GoogleAuthState
from app.models.app_config import AppConfig
from app.models.emergency_code import EmergencyCode
from app.models.whatsapp_message import WhatsappMessage
from app.models.assignment import Assignment
from app.models.attendance import Attendance
from app.models.briefing import DailyBriefing
from app.models.document import Document
from app.models.contact import Contact
from app.models.egov_attendance import EGovAttendance
from app.models.email import Email
from app.models.event import Event
from app.models.exam import Exam
from app.models.notification import Notification
from app.models.push_subscription import PushSubscription
from app.models.task import Task
from app.models.timetable import TimetableEntry
from app.models.user import User, Role, Permission, RolePermission, UserRole
from app.models.user_integration import UserIntegration

__all__ = [
    "GoogleAuthState",
    "AppConfig",
    "EmergencyCode",
    "WhatsappMessage",
    "Assignment",
    "Attendance",
    "DailyBriefing",
    "Document",
    "Contact",
    "EGovAttendance",
    "Email",
    "Event",
    "Exam",
    "Notification",
    "PushSubscription",
    "Task",
    "TimetableEntry",
    "User",
    "Role",
    "Permission",
    "RolePermission",
    "UserRole",
    "UserIntegration",
]