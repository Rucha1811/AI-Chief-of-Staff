import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

from app.api.integrations import _check_api_key
from app.core.database import get_db
from app.models.user import User
from app.models.user_integration import UserIntegration
from app.schemas.email import AgentAccount, AgentSyncRequest, AgentSyncResponse
from app.services import gmail_service
from app.services.email_ingest import EmailIngestIdentityError, resolve_ingest_user

router = APIRouter(prefix="/api/integrations/agent", tags=["integrations-agent"])


@router.get("/accounts", response_model=list[AgentAccount])
def agent_accounts(
    request: Request,
    x_api_key: Optional[str] = Header(default=None),
    x_admin_key: Optional[str] = Header(default=None),
    scope: str = "self",
    db: Session = Depends(get_db),
) -> list[AgentAccount]:
    """Trusted enumeration of Gmail accounts the backend can sync for n8n.

    Identity is derived ONLY from the server-side integration mapping (the
    account a user connected via their own OAuth flow), never from workflow
    input. Missing or revoked integrations are not listed; a revoked/missing
    account simply disappears and syncs for it fail closed.

    ``scope`` controls how much is returned:
    - ``self`` (default): only the first active account (single-user
      orchestration). Prevents a leaked ingest key from enumerating every
      user's email address.
    - ``all``: every active Google integration. Intended only for trusted
      server-side orchestration; requires a separate ``X-Admin-Key`` header
      (configurable via ``settings.ADMIN_API_KEY``) in addition to the ingest
      key, so a compromised ingest key cannot escalate to cross-user reads.
    """
    import hmac as _hmac
    _check_api_key(x_api_key)
    if scope != "all":
        scope = "self"
    query = (
        db.query(UserIntegration, User)
        .join(User, User.id == UserIntegration.user_id)
        .filter(
            UserIntegration.provider == "google",
            UserIntegration.status == "active",
            User.status == "active",
        )
        .order_by(User.id.asc())
    )
    if scope == "all":
        from app.core.config import settings as _s
        admin_key = _s.ADMIN_API_KEY
        if not admin_key:
            raise HTTPException(
                status_code=403,
                detail="Cross-user scope requires ADMIN_API_KEY to be configured",
            )
        if not x_admin_key or not _hmac.compare_digest(
            str(x_admin_key), str(admin_key)
        ):
            raise HTTPException(
                status_code=403,
                detail="Invalid or missing admin key for scope=all",
            )
        rows = query.all()
    else:
        # self: first active account only (single-user orchestration default)
        rows = query.limit(1).all()
    return [
        AgentAccount(
            user_id=ui.user_id,
            email=user.email,
            account_id=ui.account_id,
            status="active",
        )
        for ui, user in rows
    ]


async def _analyze_email_in_db(email_id: int) -> None:
    from app.services import ai_processor

    ai_processor.enqueue_email(email_id)


@router.post("/sync", response_model=AgentSyncResponse)
async def agent_sync(
    payload: AgentSyncRequest,
    background_tasks: BackgroundTasks,
    x_api_key: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
) -> AgentSyncResponse:
    """Server-side per-user Gmail sync for one account, invoked by n8n.

    n8n identifies the account (Gmail address); FastAPI resolves and validates
    it against the trusted integration mapping and syncs using THAT user's own
    stored Google credentials. n8n therefore never holds a Gmail credential
    and never submits a numeric ``user_id``."""
    _check_api_key(x_api_key)
    try:
        user_id = resolve_ingest_user(db, account_id=payload.account_id)
    except EmailIngestIdentityError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))

    try:
        added_ids, failed_ids = await gmail_service.sync_emails(
            max_results=payload.limit, db=db, user_id=user_id
        )
    except gmail_service.GmailAuthError as e:
        logger.warning("agent sync gmail auth failure for account %s: %s", payload.account_id, e)
        raise HTTPException(status_code=503, detail="Gmail authorization required for this account.")
    except gmail_service.GmailUnavailableError as e:
        logger.warning("agent sync gmail unavailable for account %s: %s", payload.account_id, e)
        raise HTTPException(status_code=502, detail="Gmail is temporarily unavailable.")

    if added_ids and payload.auto_process:
        for email_id in added_ids:
            background_tasks.add_task(_analyze_email_in_db, email_id)

    return AgentSyncResponse(
        user_id=user_id,
        account_id=payload.account_id,
        added=len(added_ids),
        failed=len(failed_ids),
        processing=len(added_ids) if payload.auto_process else 0,
    )