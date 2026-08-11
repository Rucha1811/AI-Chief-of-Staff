from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from datetime import datetime
from typing import Optional

from app.core.database import get_db, SessionLocal
from app.models.email import Email
from app.schemas.email import (
    EmailResponse,
    EmailCategoryUpdate,
    EmailReplyResponse,
    EmailSummaryResponse,
)
from app.services.ai_service import ai_service
from app.services import gmail_service
from app.services.email_processor import process_email as process_email_task

router = APIRouter(prefix="/api/emails", tags=["emails"])


async def analyze_email_in_db(email_id: int) -> None:
    await process_email_task(email_id)


@router.post("/sync")
async def sync_from_gmail(
    limit: int = 50,
    auto_process: bool = True,
    background_tasks: BackgroundTasks = None,
    db: Session = Depends(get_db),
) -> dict:
    try:
        added_ids = await gmail_service.sync_emails(max_results=limit, db=db)
    except gmail_service.GmailAuthError as e:
        raise HTTPException(status_code=401, detail=f"Gmail authorization needed: {e}")
    except gmail_service.GmailUnavailableError as e:
        raise HTTPException(status_code=502, detail=f"Gmail is temporarily unavailable: {e}")
    if background_tasks and added_ids and auto_process:
        for email_id in added_ids:
            background_tasks.add_task(analyze_email_in_db, email_id)
    return {"added": len(added_ids), "processing": len(added_ids) if auto_process else 0}


@router.get("/auth-status")
async def gmail_auth_status() -> dict:
    return await gmail_service.auth_status()


@router.get("/health")
async def gmail_health() -> dict:
    return await gmail_service.health_check()


@router.post("/re-auth")
async def gmail_reauthorize() -> dict:
    try:
        return await gmail_service.reauthorize()
    except gmail_service.GmailAuthError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("")
async def list_emails(
    category: Optional[str] = None,
    has_deadline: Optional[bool] = None,
    is_starred: Optional[bool] = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
) -> list[EmailResponse]:
    query = db.query(Email)
    if category:
        query = query.filter(Email.category == category)
    if has_deadline is not None:
        query = query.filter(Email.has_deadline == has_deadline)
    if is_starred is not None:
        query = query.filter(Email.is_starred == is_starred)
    return query.order_by(Email.received_at.desc()).offset(offset).limit(limit).all()


@router.post("/process-all")
async def process_all_emails(
    limit: int = 25, db: Session = Depends(get_db)
) -> dict:
    emails = (
        db.query(Email)
        .filter(Email.category == "unknown")
        .order_by(Email.received_at.desc())
        .limit(limit)
        .all()
    )
    processed = 0
    failed = 0
    scheduled = 0
    for email in emails:
        try:
            result = await process_email_task(email.id)
            processed += 1
            scheduled += sum(
                1
                for it in result.get("scheduled_items", [])
                if it.get("action") == "created"
            )
        except Exception:
            failed += 1
    return {
        "processed": processed,
        "failed": failed,
        "scheduled": scheduled,
        "remaining": db.query(Email).filter(Email.category == "unknown").count(),
    }


@router.post("/{email_id}/process")
async def process_single_email(email_id: int) -> dict:
    from app.services.email_processor import process_email

    result = await process_email(email_id)
    if result.get("error") == "not found":
        raise HTTPException(status_code=404, detail="Email not found")
    return result


@router.get("/stats")
async def email_stats(db: Session = Depends(get_db)) -> dict:
    total = db.query(Email).count()
    unread = db.query(Email).filter(Email.is_read == False).count()
    reply_required = db.query(Email).filter(Email.category == "reply_required").count()
    important = db.query(Email).filter(Email.category == "important").count()
    with_deadlines = db.query(Email).filter(Email.has_deadline == True).count()
    return {
        "total": total,
        "unread": unread,
        "reply_required": reply_required,
        "important": important,
        "with_deadlines": with_deadlines,
        "can_ignore": db.query(Email).filter(Email.category == "can_ignore").count(),
    }


@router.get("/{email_id}")
async def get_email(email_id: int, db: Session = Depends(get_db)) -> EmailResponse:
    email = db.query(Email).filter(Email.id == email_id).first()
    if not email:
        raise HTTPException(status_code=404, detail="Email not found")
    return email


@router.patch("/{email_id}/category")
async def update_email_category(
    email_id: int, update: EmailCategoryUpdate, db: Session = Depends(get_db)
) -> EmailResponse:
    email = db.query(Email).filter(Email.id == email_id).first()
    if not email:
        raise HTTPException(status_code=404, detail="Email not found")
    email.category = update.category
    db.commit()
    db.refresh(email)
    return email


@router.post("/{email_id}/classify")
async def classify_email(email_id: int, db: Session = Depends(get_db)) -> EmailResponse:
    email = db.query(Email).filter(Email.id == email_id).first()
    if not email:
        raise HTTPException(status_code=404, detail="Email not found")
    result = await ai_service.classify_email(email.subject or "", email.body_text or "")
    if "category" in result:
        email.category = result["category"]
    if result.get("has_deadline"):
        email.has_deadline = True
        email.deadline_description = result.get("deadline_description")
        if result.get("deadline"):
            try:
                email.deadline_at = datetime.fromisoformat(result["deadline"])
            except (ValueError, TypeError):
                pass
    email.summary = result.get("reason", "")
    db.commit()
    db.refresh(email)
    return email


@router.post("/{email_id}/suggest-reply")
async def suggest_email_reply(
    email_id: int, db: Session = Depends(get_db)
) -> EmailReplyResponse:
    email = db.query(Email).filter(Email.id == email_id).first()
    if not email:
        raise HTTPException(status_code=404, detail="Email not found")
    reply = await ai_service.suggest_reply(
        email.body_text or "", email.subject or ""
    )
    email.suggested_reply = reply
    db.commit()
    return EmailReplyResponse(email_id=email_id, suggested_reply=reply)


@router.post("/{email_id}/summarize")
async def summarize_email(
    email_id: int, db: Session = Depends(get_db)
) -> EmailSummaryResponse:
    email = db.query(Email).filter(Email.id == email_id).first()
    if not email:
        raise HTTPException(status_code=404, detail="Email not found")
    result = await ai_service.summarize_email(email.subject or "", email.body_text or "")
    email.summary = result.get("summary", "")
    if result.get("has_deadline"):
        email.has_deadline = True
        email.deadline_description = result.get("deadline_description")
    db.commit()
    return EmailSummaryResponse(
        email_id=email_id,
        summary=result.get("summary", ""),
        has_deadline=result.get("has_deadline", False),
        deadline_at=email.deadline_at,
        deadline_description=email.deadline_description,
    )


@router.patch("/{email_id}/star")
async def toggle_star(email_id: int, db: Session = Depends(get_db)) -> EmailResponse:
    email = db.query(Email).filter(Email.id == email_id).first()
    if not email:
        raise HTTPException(status_code=404, detail="Email not found")
    email.is_starred = not email.is_starred
    db.commit()
    db.refresh(email)
    return email


@router.patch("/{email_id}/read")
async def mark_read(email_id: int, db: Session = Depends(get_db)) -> EmailResponse:
    email = db.query(Email).filter(Email.id == email_id).first()
    if not email:
        raise HTTPException(status_code=404, detail="Email not found")
    email.is_read = True
    db.commit()
    db.refresh(email)
    return email
