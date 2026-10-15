import json

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.contact import Contact
from app.models.user import User
from app.services.contacts_service import sync_contacts

router = APIRouter(prefix="/api/contacts", tags=["contacts"])


def _response(row: Contact) -> dict:
    return {
        "id": row.id,
        "display_name": row.display_name,
        "emails": json.loads(row.emails or "[]"),
        "phone_numbers": json.loads(row.phone_numbers or "[]"),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


@router.get("")
def list_contacts(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return [_response(row) for row in db.query(Contact).filter(Contact.user_id == user.id).order_by(Contact.display_name).all()]


@router.post("/sync")
def sync_google_contacts(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return {"synced": sync_contacts(db, user.id)}
