from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.security import verify_integration_key
from app.schemas.email import (
    EmailIngestBatch,
    EmailIngestBatchResponse,
    EmailIngestItemResult,
    IntegrationConfigResponse,
    RetentionPurgeRequest,
    RetentionPurgeResponse,
)
from app.services import email_ingest, email_retention

router = APIRouter(prefix="/api/integrations", tags=["integrations"])


def _check_api_key(x_api_key: Optional[str]) -> None:
    if not verify_integration_key(x_api_key, settings.INGEST_API_KEY):
        raise HTTPException(status_code=401, detail="Invalid or missing integration API key")


async def _analyze_email_in_db(email_id: int) -> None:
    from app.services import ai_processor

    # Route through the bounded AI worker queue for controlled concurrency and
    # retry resilience (instead of a raw FastAPI background task).
    ai_processor.enqueue_email(email_id)


@router.get("/config", response_model=IntegrationConfigResponse)
async def integration_config(
    x_api_key: Optional[str] = Header(default=None),
) -> IntegrationConfigResponse:
    _check_api_key(x_api_key)
    return IntegrationConfigResponse(
        email_retention_days=settings.EMAIL_RETENTION_DAYS,
        email_retention_mode=settings.RETENTION_MODE,
        ingest_batch_max=settings.INGEST_BATCH_MAX,
        ingest_endpoint="/api/integrations/emails/ingest",
        purge_endpoint="/api/integrations/retention/purge",
    )


@router.post("/emails/ingest", response_model=EmailIngestBatchResponse)
async def ingest_emails(
    batch: EmailIngestBatch,
    background_tasks: BackgroundTasks,
    x_api_key: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
) -> EmailIngestBatchResponse:
    _check_api_key(x_api_key)
    if not batch.emails:
        return EmailIngestBatchResponse(
            received=0, inserted=0, ignored=0, failed=0, results=[]
        )
    if len(batch.emails) > settings.INGEST_BATCH_MAX:
        raise HTTPException(
            status_code=413,
            detail=f"Batch exceeds maximum size {settings.INGEST_BATCH_MAX}",
        )
    try:
        outcome = email_ingest.ingest_emails(
            db,
            batch.emails,
            user_id=batch.user_id,
            account_id=batch.account_id,
        )
    except email_ingest.EmailIngestIdentityError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
    except Exception:
        raise HTTPException(status_code=500, detail="Database write failed")

    # Trigger the EXISTING local Ollama pipeline only for newly inserted rows
    # (same processor the native Gmail sync uses).
    for email_id in outcome["inserted_ids"]:
        background_tasks.add_task(_analyze_email_in_db, email_id)

    results = [EmailIngestItemResult(**r) for r in outcome["results"]]
    return EmailIngestBatchResponse(
        received=outcome["received"],
        inserted=outcome["inserted"],
        ignored=outcome["ignored"],
        failed=0,
        results=results,
    )


@router.post("/retention/purge", response_model=RetentionPurgeResponse)
async def retention_purge(
    payload: RetentionPurgeRequest,
    x_api_key: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
) -> RetentionPurgeResponse:
    _check_api_key(x_api_key)
    result = email_retention.purge_expired(db, dry_run=payload.dry_run)
    return RetentionPurgeResponse(**result)