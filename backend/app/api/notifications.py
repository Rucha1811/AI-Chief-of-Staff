from fastapi import APIRouter, Depends
from fastapi.requests import Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.notification import Notification
from app.models.user import User
from app.services.reminder_service import generate_reminders
from app.services.push_service import get_vapid_public_key, save_subscription
from app.api.deps import get_current_user
from app.core.limits import clamp_limit

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
def list_notifications(
    limit: int = 50,
    unread_only: bool = False,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    generate_reminders()
    q = (
        select(Notification)
        .where(Notification.user_id == user.id)
        .order_by(Notification.created_at.desc(), Notification.id.desc())
    )
    if unread_only:
        q = q.where(Notification.is_read.is_(False))
    items = db.execute(q.limit(clamp_limit(limit))).scalars().all()
    return [_serialize(n) for n in items]


@router.get("/unread-count")
def unread_count(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    generate_reminders()
    count = db.execute(
        select(func.count())
        .select_from(Notification)
        .where(Notification.user_id == user.id, Notification.is_read.is_(False))
    ).scalar()
    return {"count": count}


@router.get("/vapid-public-key")
def vapid_public_key(user: User = Depends(get_current_user)):
    return {"key": get_vapid_public_key()}


@router.post("/subscribe")
async def subscribe(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        sub = await request.json()
    except Exception:
        sub = {}
    ok = save_subscription(sub, user_id=user.id)
    return {"ok": ok}


@router.post("/{notification_id}/read")
def mark_read(
    notification_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    n = (
        db.query(Notification)
        .filter(Notification.user_id == user.id, Notification.id == notification_id)
        .first()
    )
    if not n:
        return {"ok": False}
    n.is_read = True
    db.commit()
    return {"ok": True}


@router.post("/read-all")
def read_all(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    db.query(Notification).filter(Notification.user_id == user.id).update(
        {Notification.is_read: True}, synchronize_session=False
    )
    db.commit()
    return {"ok": True}