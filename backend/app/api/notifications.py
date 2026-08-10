from fastapi import APIRouter
from fastapi.requests import Request
from sqlalchemy import func, select

from app.core.database import SessionLocal
from app.models.notification import Notification
from app.services.reminder_service import generate_reminders
from app.services.push_service import get_vapid_public_key, save_subscription

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


def _serialize(n: Notification) -> dict:
    return {
        "id": n.id,
        "type": n.type,
        "title": n.title,
        "message": n.message,
        "entity_type": n.entity_type,
        "entity_id": n.entity_id,
        "priority": n.priority,
        "is_read": n.is_read,
        "created_at": n.created_at.isoformat() if n.created_at else None,
    }


@router.get("")
def list_notifications(limit: int = 50, unread_only: bool = False):
    generate_reminders()
    with SessionLocal() as session:
        q = select(Notification).order_by(Notification.created_at.desc(), Notification.id.desc())
        if unread_only:
            q = q.where(Notification.is_read.is_(False))
        items = session.execute(q.limit(limit)).scalars().all()
        return [_serialize(n) for n in items]


@router.get("/unread-count")
def unread_count():
    generate_reminders()
    with SessionLocal() as session:
        count = session.execute(
            select(func.count()).select_from(Notification).where(Notification.is_read.is_(False))
        ).scalar()
        return {"count": count}


@router.get("/vapid-public-key")
def vapid_public_key():
    return {"key": get_vapid_public_key()}


@router.post("/subscribe")
async def subscribe(request: Request):
    try:
        sub = await request.json()
    except Exception:
        sub = {}
    ok = save_subscription(sub)
    return {"ok": ok}


@router.post("/{notification_id}/read")
def mark_read(notification_id: int):
    with SessionLocal() as session:
        n = session.get(Notification, notification_id)
        if not n:
            return {"ok": False}
        n.is_read = True
        session.commit()
        return {"ok": True}


@router.post("/read-all")
def read_all():
    with SessionLocal() as session:
        session.execute(Notification.__table__.update().values(is_read=True))
        session.commit()
        return {"ok": True}
