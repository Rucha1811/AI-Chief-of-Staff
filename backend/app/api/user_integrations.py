from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.user import User
from app.models.user_integration import UserIntegration
from app.api.deps import get_current_user, require_permission

router = APIRouter(
    prefix="/api/integrations/user",
    tags=["user-integrations"],
)


class IntegrationConnection(BaseModel):
    provider: str
    account_id: Optional[str] = None
    status: str = "active"
    last_synced_at: Optional[str] = None
    last_error: Optional[str] = None


class IntegrationUpsert(BaseModel):
    provider: str
    account_id: Optional[str] = None
    status: str = "active"
    scopes: Optional[str] = None


@router.get("", response_model=list[IntegrationConnection])
def list_connections(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List the current user's integration connections (metadata only;
    never exposes stored tokens)."""
    rows = (
        db.query(UserIntegration)
        .filter(UserIntegration.user_id == user.id)
        .order_by(UserIntegration.provider)
        .all()
    )
    return [
        IntegrationConnection(
            provider=r.provider,
            account_id=r.account_id,
            status=r.status,
            last_synced_at=r.last_synced_at.isoformat() if r.last_synced_at else None,
            last_error=r.last_error,
        )
        for r in rows
    ]


@router.post("", response_model=IntegrationConnection, status_code=201)
def upsert_connection(
    body: IntegrationUpsert,
    user: User = Depends(require_permission("manage_integrations")),
    db: Session = Depends(get_db),
):
    """Register or refresh a connection record for the current user."""
    provider = body.provider.strip().lower()
    if not provider:
        raise HTTPException(status_code=422, detail="provider is required")

    existing = (
        db.query(UserIntegration)
        .filter(
            UserIntegration.user_id == user.id,
            UserIntegration.provider == provider,
        )
        .first()
    )
    if existing:
        existing.account_id = body.account_id
        existing.status = body.status
        existing.scopes = body.scopes
        db.commit()
        db.refresh(existing)
        return IntegrationConnection(
            provider=existing.provider,
            account_id=existing.account_id,
            status=existing.status,
            last_synced_at=existing.last_synced_at.isoformat()
            if existing.last_synced_at
            else None,
            last_error=existing.last_error,
        )

    row = UserIntegration(
        user_id=user.id,
        provider=provider,
        account_id=body.account_id,
        status=body.status,
        scopes=body.scopes,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return IntegrationConnection(
        provider=row.provider,
        account_id=row.account_id,
        status=row.status,
        last_synced_at=None,
        last_error=None,
    )


@router.delete("/{provider}", status_code=204)
def delete_connection(
    provider: str,
    user: User = Depends(require_permission("manage_integrations")),
    db: Session = Depends(get_db),
):
    deleted = (
        db.query(UserIntegration)
        .filter(
            UserIntegration.user_id == user.id,
            UserIntegration.provider == provider.strip().lower(),
        )
        .delete()
    )
    db.commit()
    if not deleted:
        raise HTTPException(status_code=404, detail="Connection not found")