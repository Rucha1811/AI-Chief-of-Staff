from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


class EmailResponse(BaseModel):
    id: int
    message_id: str
    thread_id: Optional[str] = None
    from_email: str
    from_name: Optional[str] = None
    to_email: Optional[str] = None
    subject: str
    body_text: Optional[str] = None
    received_at: Optional[datetime] = None
    category: str
    is_read: bool
    is_starred: bool
    has_deadline: bool
    deadline_at: Optional[datetime] = None
    deadline_description: Optional[str] = None
    suggested_reply: Optional[str] = None
    summary: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class EmailCategoryUpdate(BaseModel):
    category: str


class EmailReplyRequest(BaseModel):
    email_id: int
    context: Optional[str] = None


class EmailReplyResponse(BaseModel):
    email_id: int
    suggested_reply: str


class EmailSummaryRequest(BaseModel):
    email_id: int


class EmailSummaryResponse(BaseModel):
    email_id: int
    summary: str
    has_deadline: bool
    deadline_at: Optional[datetime] = None
    deadline_description: Optional[str] = None


class EmailIngestItem(BaseModel):
    """One Gmail message submitted by n8n. body_html is intentionally NOT
    accepted: raw HTML is stripped client-side and only body_text is stored."""

    message_id: str = Field(min_length=1, max_length=512)
    thread_id: Optional[str] = Field(default=None, max_length=512)
    from_email: Optional[str] = Field(default=None, max_length=512)
    from_name: Optional[str] = Field(default=None, max_length=512)
    to_email: Optional[str] = Field(default=None, max_length=8192)
    subject: Optional[str] = Field(default="", max_length=512)
    body_text: Optional[str] = None
    received_at: Optional[datetime] = None
    is_read: bool = False


class EmailIngestBatch(BaseModel):
    """A batch of Gmail messages from n8n for one user's inbox.

    The server establishes identity from a trusted server-side mapping (the
    ``account_id`` a user connected via their own OAuth flow), never from a raw
    ``user_id``. ``account_id`` alone resolves to exactly one active Google
    integration; ``user_id``+``account_id`` must match; ``user_id`` alone is
    only trusted for users with an active Google integration. When both are
    omitted the batch keeps the legacy single-user (owner) fallback, still
    gated by the ingest API key.
    """

    emails: list[EmailIngestItem]
    user_id: Optional[int] = None
    account_id: Optional[str] = None


class EmailIngestItemResult(BaseModel):
    message_id: str
    status: str  # "inserted" | "ignored" | "error"
    email_id: Optional[int] = None
    error: Optional[str] = None


class EmailIngestBatchResponse(BaseModel):
    received: int
    inserted: int
    ignored: int
    failed: int
    results: list[EmailIngestItemResult]


class RetentionPurgeRequest(BaseModel):
    dry_run: bool = True


class RetentionPurgeResponse(BaseModel):
    mode: str
    dry_run: bool
    expired_emails: int
    dependent_notifications: int
    deleted_emails: int
    deleted_notifications: int


class IntegrationConfigResponse(BaseModel):
    email_retention_days: int
    email_retention_mode: str
    ingest_batch_max: int
    ingest_endpoint: str
    purge_endpoint: str


class AgentAccount(BaseModel):
    """A Google account AI Chief can sync, resolved from the trusted
    server-side integration mapping (bound to a user by their OAuth flow)."""

    user_id: int
    email: str
    account_id: Optional[str] = None
    status: str


class AgentSyncRequest(BaseModel):
    account_id: str = Field(min_length=1, max_length=512)
    limit: int = Field(default=50, ge=1, le=200)
    auto_process: bool = True


class AgentSyncResponse(BaseModel):
    user_id: int
    account_id: str
    added: int
    failed: int
    processing: int
