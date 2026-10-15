from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from sqlalchemy import inspect, text
from app.core import access_policy
from app.core.config import normalize_request_path, settings
from app.core.database import engine, Base
from app.core.logging_filters import install_access_log_redaction
import app.models  # noqa: F401  (registers all models so create_all sees them)

import logging

logger = logging.getLogger("ai-chief.sync")
security_logger = logging.getLogger("ai-chief.security")

# Uvicorn's access log writes the raw request line, which for the OAuth callback
# contains the one-time authorization ``code`` and the ``state`` nonce. Uvicorn
# configures logging before importing this module, so installing the filter here
# is durable for the process lifetime.
install_access_log_redaction()


from app.api import (
    emails,
    events,
    tasks,
    documents,
    contacts,
    briefing,
    dashboard,
    news,
    timetable,
    classroom,
    teams,
    auth,
    notifications,
    exams,
    assignments,
    attendance,
    egov_attendance,
    integrations,
    integration_agent,
    user_integrations,
    google_oauth,
    whatsapp,
)
from app.api.deps import get_current_user
from app.models.user import User

_PUBLIC_PATHS = frozenset(
    normalize_request_path(path)
    for path in (
        "/api/auth/login",
        "/api/auth/status",
        "/api/auth/register",
        "/api/auth/exchange",
        # D2: the Google sign-in *entry point*. It is a browser navigation
        # taken from the logged-out AuthGate screen, so it carries no bearer
        # token and must be reachable anonymously - adding it here is the whole
        # fix for the 401 that anonymous Google login used to receive. Exactly
        # ONE route is added: nothing under /api/integrations/ becomes public,
        # the callback stays classified in app.core.access_policy as
        # CREDENTIAL_PUBLIC (authenticated by its own state cookie), and the
        # authorize/disconnect routes stay CREDENTIAL_USER.
        "/api/auth/google/login",
        "/api/health",
        "/api/webhooks/whatsapp",
    )
)
_DOC_PATHS = frozenset({"/docs", "/redoc", "/openapi.json"})


class AuthMiddleware(BaseHTTPMiddleware):
    """Credential gate for the API surface.

    P24B-04: the exemption list is explicit and exact. This middleware used to
    skip *every* path under ``/api/integrations/`` and rely on each handler to
    check something by hand, which meant a route added under that prefix later
    would silently inherit "public". Now every integration route is classified
    in ``app.core.access_policy`` - user session, ingest API key, both, or
    public - and anything unlisted fails closed.

    The handlers keep their own checks; this layer only decides whether the
    request may reach them, using the same comparisons
    (``verify_integration_key``, ``token_payload``) so there is no second,
    weaker notion of "authorized".
    """

    async def dispatch(self, request: Request, call_next):
        path = normalize_request_path(request.url.path)
        if request.method == "OPTIONS":
            # CORS preflight carries no credentials by design.
            return await call_next(request)
        if path in _PUBLIC_PATHS:
            return await call_next(request)
        if not path.startswith("/api/") and path not in _DOC_PATHS:
            # Non-API paths (static assets when the backend serves them).
            return await call_next(request)

        if access_policy.is_integration_path(path):
            template = access_policy.matched_route_template(request.app, request.scope)
            required = access_policy.integration_credential(request.method, template)
            if required != access_policy.CREDENTIAL_PUBLIC:
                needs_user = required in (
                    access_policy.CREDENTIAL_USER,
                    access_policy.CREDENTIAL_BOTH,
                )
                needs_key = required in (
                    access_policy.CREDENTIAL_API_KEY,
                    access_policy.CREDENTIAL_BOTH,
                )
                if needs_user and not auth.is_authenticated(request):
                    return _unauthorized()
                if needs_key and not access_policy.has_valid_api_key(request.headers):
                    return _unauthorized()
            return await call_next(request)

        if not auth.is_authenticated(request):
            return _unauthorized()
        return await call_next(request)


def _unauthorized() -> JSONResponse:
    return JSONResponse(status_code=401, content={"detail": "Authentication required"})


# --------------------------------------------------------------------------
# D6 - security headers
#
# Applied to every response this process produces (API JSON, /docs, redirects)
# by registering the middleware outermost, so 401s and 503s carry them too.
#
# HSTS is deliberately absent. The deployment is HTTP-only today, and
# Strict-Transport-Security on an HTTP origin is ignored by browsers while
# enabling it before HTTPS is guaranteed risks pinning clients to a scheme the
# server does not serve. Add it together with the certificate in the HTTPS
# phase, never before.
#
# The CSP is intentionally limited to directives that cannot break Next.js,
# Swagger UI or the Google OAuth round-trip: it restricts framing, <base>
# rewriting, plugin content and form targets, and leaves script/style loading
# alone rather than reaching for 'unsafe-inline'/'unsafe-eval' wildcards.
# --------------------------------------------------------------------------
SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    # Self is allowed for camera/microphone because the app has an in-browser
    # voice assistant; everything privacy-invasive that we do not use is off.
    "Permissions-Policy": (
        "camera=(self), microphone=(self), geolocation=(), payment=(), "
        "usb=(), serial=(), interest-cohort=()"
    ),
    "Content-Security-Policy": (
        "frame-ancestors 'none'; base-uri 'none'; object-src 'none'; "
        "form-action 'self'"
    ),
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach SECURITY_HEADERS to every response (D6).

    Uses presence checks rather than blind assignment so a handler that sets a
    stricter value of its own is never downgraded.
    """

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            if name not in response.headers:
                response.headers[name] = value
        return response


_MIGRATIONS = {
    "daily_briefings": [
        ("meetings_highlights", "TEXT"),
        ("email_highlights", "TEXT"),
        ("task_highlights", "TEXT"),
    ],
    "events": [
        ("recurrence_rule", "VARCHAR(32)"),
        ("recurrence_until", "TIMESTAMP"),
    ],
    "emails": [
        ("retention_until", "TIMESTAMP"),
        ("sensitivity", "VARCHAR(16)"),
    ],
    "emergency_codes": [
        # P1-3: bind emergency codes to a user. Legacy rows keep NULL and are
        # simply inert (consume_emergency_code requires a matching user_id).
        # Alembic revision 0007 applies the NOT NULL + FK for fresh installs.
        ("user_id", "BIGINT"),
    ],
    # Local development databases may have been created before the ownership
    # migrations ran. Keep the SQLite fallback schema aligned with the models
    # used by the authenticated dashboard and OAuth health state.
    **{
        table: [("user_id", "BIGINT")]
        + ([("push_sent_at", "TIMESTAMP")] if table == "notifications" else [])
        for table in (
            "emails",
            "events",
            "tasks",
            "assignments",
            "attendance",
            "timetable_entries",
            "notifications",
            "whatsapp_messages",
            "documents",
            "contacts",
            "daily_briefings",
            "exams",
            "push_subscriptions",
            "google_auth_state",
        )
    },
}


def _run_migrations(target_engine=None) -> None:
    """DEV/TEST ONLY. Add any column a pre-Alembic database is missing.

    Development convenience for local SQLite databases created before this
    repo moved its schema history to Alembic. Never reached in production or
    staging - see apply_startup_schema_policy().
    """
    target = target_engine if target_engine is not None else engine
    inspector = inspect(target)
    tables = {
        t: {c["name"] for c in inspector.get_columns(t)}
        for t in inspector.get_table_names()
    }
    with target.begin() as conn:
        for table, columns in _MIGRATIONS.items():
            if table not in tables:
                continue
            for col, ddl in columns:
                if col not in tables[table]:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))


def apply_startup_schema_policy(target_engine=None) -> None:
    """D4: decide, once, how startup is allowed to touch the schema.

    PRODUCTION / STAGING
        A read-only gate (app.core.schema_gate) compares the database's
        Alembic revision with the repository migration head and raises
        SchemaGateError when they differ, when the revision is unknown, when
        the repository has multiple heads, or when the database has never
        been migrated. Nothing is created, altered, dropped or upgraded:
        schema changes happen only when an operator deliberately runs
        `alembic upgrade head`, so a running deployment can never mutate its
        own schema as a side effect of being started or restarted.

    DEVELOPMENT / TESTING
        Keeps the historical conveniences (`Base.metadata.create_all` plus
        the ad-hoc ALTER TABLE pass) so a fresh local checkout and the test
        suite's throwaway database still bootstrap with no setup.

    Raises SchemaGateError (which aborts the import, so uvicorn exits non-zero)
    instead of ever "repairing" production schema.
    """
    from app.core.schema_gate import GATE_ENVIRONMENTS, enforce_schema_gate

    target = target_engine if target_engine is not None else engine
    if (settings.ENVIRONMENT or "").strip().lower() in GATE_ENVIRONMENTS:
        enforce_schema_gate(target)
        return
    Base.metadata.create_all(bind=target)
    _run_migrations(target)


apply_startup_schema_policy()

def _google_connected_user_ids(db) -> list[int]:
    """Users who have an active google connection. Falls back to the owner
    so a legacy token that predates user_integrations still syncs."""
    from app.models.user import User
    from app.models.user_integration import UserIntegration
    from app.services.ownership import owner_user_id

    # Check if any user exists in the database. If not, return empty list
    # so the background worker handles the zero-user state gracefully.
    # This can happen during app startup before the first user registers,
    # or in development mode where the first-user flow is used.
    user_exists = db.query(User).first() is not None

    ids = [
        row[0]
        for row in (
            db.query(UserIntegration.user_id)
            .filter(
                UserIntegration.provider == "google",
                UserIntegration.status == "active",
            )
            .distinct()
            .all()
        )
    ]
    if not ids and not user_exists:
        # No users in the database and no connected IDs -> return empty list
        # so the background worker does not crash on zero-user state.
        return []
    return ids or [owner_user_id(db)]


def _sync_emails_pass(session) -> list:
    """One gmail sync pass for every connected user. The per-user try/except
    keeps a single user's failure (auth/network/API) from blocking others."""
    import asyncio

    from app.services import google_auth
    from app.services.gmail_service import sync_emails

    added = []
    for uid in _google_connected_user_ids(session):
        if google_auth.credentials_for_user(session, uid) is None:
            continue
        try:
            added_ids, failed_ids = asyncio.run(
                sync_emails(max_results=1000, db=session, user_id=uid)
            )
            added.extend(added_ids)
            if failed_ids:
                logger.warning("gmail sync for user #%s: %d messages failed", uid, len(failed_ids))
            session.commit()
        except Exception as e:
            logger.warning("gmail sync failed for user #%s: %s", uid, _short(e))
            session.rollback()
    return added


def _sync_classroom_pass(session) -> None:
    """One classroom sync pass for every connected user (isolated per user)."""
    import asyncio

    from app.services import google_auth
    from app.services.classroom_service import sync_assignments, sync_submission_status

    for uid in _google_connected_user_ids(session):
        if google_auth.credentials_for_user(session, uid) is None:
            continue
        try:
            asyncio.run(sync_submission_status(db=session, user_id=uid))
            asyncio.run(sync_assignments(db=session, user_id=uid))
            session.commit()
        except Exception as e:
            logger.warning("classroom sync failed for user #%s: %s", uid, _short(e))
            session.rollback()


def _sync_contacts_pass(session) -> None:
    from app.services import google_auth
    from app.services.contacts_service import sync_contacts

    for uid in _google_connected_user_ids(session):
        if google_auth.credentials_for_user(session, uid) is None:
            continue
        try:
            sync_contacts(session, uid)
        except Exception as e:
            logger.warning("contacts sync failed for user #%s: %s", uid, _short(e))
            session.rollback()


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.VERSION,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(AuthMiddleware)
# Registered last so it is the OUTERMOST middleware: every response, including
# the 401s and 503s produced below it, carries the D6 security headers.
app.add_middleware(SecurityHeadersMiddleware)

app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(news.router)
app.include_router(emails.router)
app.include_router(events.router)
app.include_router(tasks.router)
app.include_router(documents.router)
app.include_router(contacts.router)
app.include_router(briefing.router)
app.include_router(timetable.router)
app.include_router(classroom.router)
app.include_router(teams.router)
app.include_router(notifications.router)
app.include_router(exams.router)
app.include_router(assignments.router)
# Registered before the manual-attendance router so the literal
# ``/api/attendance/egov`` paths are matched ahead of ``/api/attendance/{id}``.
app.include_router(egov_attendance.router)
app.include_router(attendance.router)
app.include_router(integrations.router)
app.include_router(integration_agent.router)
app.include_router(user_integrations.router)
app.include_router(google_oauth.router)
app.include_router(whatsapp.router)


def _log_startup_security_policy() -> None:
    """Log the effective route authorization policy and bootstrap readiness.

    Read-only and fail-safe. An operator reading the startup log should be able
    to answer "which routes skip AuthMiddleware, and which credential does each
    integration route need?" (P24B-04) and "can anybody still claim ownership?"
    (P24B-06) without reading the source.
    """
    security_logger.info("auth-exempt API paths: %s", sorted(_PUBLIC_PATHS))
    security_logger.info(
        "integration route policy: %s",
        sorted(
            f"{method} {template} -> {credential}"
            for (method, template), credential in access_policy.POLICY.items()
        ),
    )

    unclassified = access_policy.unclassified_integration_routes(app)
    if unclassified:
        # Not a vulnerability (the default is deny-both) but a maintenance
        # signal: these routes stay reachable only with user + API key until
        # someone states their intended credential class.
        security_logger.warning(
            "integration routes without an explicit policy entry (defaulting to "
            "fail-closed 'both' credentials): %s",
            unclassified,
        )

    from app.core.database import SessionLocal
    from app.services import bootstrap

    try:
        session = SessionLocal()
        try:
            state = bootstrap.bootstrap_state(session)
        finally:
            session.rollback()
            session.close()
    except Exception as exc:  # never block startup on a diagnostic
        security_logger.warning("could not determine bootstrap state: %s", _short(exc))
        return

    if state != "available":
        return
    if settings.is_production:
        security_logger.warning(
            "no user exists yet: first-owner creation requires a one-time "
            "BOOTSTRAP_TOKEN (set a high-entropy value, create the account, "
            "then remove it). Until it is set, first-owner creation is refused."
        )
    else:
        security_logger.warning(
            "no user exists yet: ENVIRONMENT=%s allows first-owner registration "
            "without a token (local convenience). Production always requires "
            "BOOTSTRAP_TOKEN.",
            settings.ENVIRONMENT,
        )


@app.on_event("startup")
def _startup_background():
    from app.services.reminder_service import generate_reminders
    from app.services import ai_processor
    from app.core import health as health_obs

    _log_startup_security_policy()

    # Start the bounded AI worker pool before spawning the push loop.
    try:
        ai_processor.start()
    except Exception:
        import logging

        logging.getLogger("ai-chief").exception("Failed to start AI worker pool")

    def _run():
        try:
            generate_reminders(force=True)
        except Exception:
            pass
        try:
            import asyncio
            from app.services.classroom_service import sync_submission_status

            asyncio.run(sync_submission_status())
            health_obs.record_sync("classroom")
        except Exception:
            pass
        try:
            import asyncio
            from datetime import date, datetime
            from app.core.database import get_db
            from app.models.briefing import DailyBriefing
            from app.api.briefing import generate_today_briefing

            session = next(get_db())
            try:
                today = date.today()
                existing = (
                    session.query(DailyBriefing)
                    .filter(
                        DailyBriefing.date >= datetime.combine(today, datetime.min.time()),
                        DailyBriefing.date <= datetime.combine(today, datetime.max.time()),
                    )
                    .first()
                )
                if not existing:
                    asyncio.run(generate_today_briefing(session))
            finally:
                session.rollback()
                session.close()
        except Exception:
            pass

    import threading

    threading.Thread(target=_run, daemon=True).start()

    def _session_worker():
        """Worker for DB-bound background tasks that must not hold a session
        open across a slow external call. Each task opens/commits its own
        fresh session via SessionLocal (see email_processor)."""
        from queue import Queue
        from app.services.email_processor import process_email_sync
        from app.core import health as _health

        import logging
        import time

        logger = logging.getLogger("ai-chief.sync")

        def _sync_gmail():
            from app.core.database import SessionLocal
            from app.core.db_operations import run_with_retry, RetryExhaustedError
            from app.services import ai_processor

            def _do_sync():
                session = SessionLocal()
                try:
                    return _sync_emails_pass(session)
                finally:
                    session.rollback()
                    session.close()

            try:
                added_ids = run_with_retry(
                    _do_sync, description="gmail sync"
                )
            except RetryExhaustedError as e:
                logger.warning("gmail sync failed permanently: %s", e)
                return
            except Exception as e:
                logger.warning("gmail sync failed: %s", _short(e))
                return
            _health.record_sync("gmail")

            # Push new emails to the bounded AI worker queue; do not process
            # inline (would block this loop and hold nothing open, but queueing
            # gives controlled concurrency).
            for email_id in added_ids:
                ai_processor.enqueue_email(email_id)
            logger.info("email sync: %s new emails queued", len(added_ids))

            # Backfill a few 'unknown' emails as capacity/queue allows.
            _backfill_unknown()

        def _backfill_unknown():
            import asyncio
            from app.core.database import SessionLocal
            from app.models.email import Email
            from app.services import ai_processor

            ids = []
            session = SessionLocal()
            try:
                if hasattr(Email, "summary"):
                    unknown = (
                        session.query(Email)
                        .filter(
                            Email.category == "unknown",
                            Email.summary.is_(None),
                            Email.user_id.isnot(None),
                        )
                        .order_by(Email.received_at.desc())
                        .all()
                    )
                else:
                    unknown = (
                        session.query(Email)
                        .filter(Email.category == "unknown", Email.user_id.isnot(None))
                        .order_by(Email.received_at.desc())
                        .all()
                    )
                # Keep processing user-scoped and bounded per account. A
                # global limit could starve newer accounts behind one user's
                # mailbox and must never be replaced with an unscoped worker.
                per_user: dict[int, int] = {}
                for email in unknown:
                    count = per_user.get(email.user_id, 0)
                    if count < 10:
                        ids.append(email.id)
                        per_user[email.user_id] = count + 1
            finally:
                session.rollback()
                session.close()
            for eid in ids:
                try:
                    ai_processor.enqueue_email(eid)
                except Exception:
                    logger.exception("failed to enqueue backfill email %s", eid)

        def _sync_classroom():
            from app.core.database import SessionLocal

            session = SessionLocal()
            try:
                _sync_classroom_pass(session)
            finally:
                session.rollback()
                session.close()
            _health.record_sync("classroom")
            logger.info("classroom sync done")

        def _sync_contacts():
            from app.core.database import SessionLocal

            session = SessionLocal()
            try:
                _sync_contacts_pass(session)
            finally:
                session.rollback()
                session.close()
            logger.info("contacts sync done")

        def _retention_purge():
            from app.core.database import SessionLocal
            from app.services.email_retention import purge_expired

            session = SessionLocal()
            try:
                purge_expired(session, dry_run=False)
            except Exception:
                logger.exception("retention purge failed")
            finally:
                session.rollback()
                session.close()

        last_email_sync = 0.0
        last_classroom_sync = 0.0
        last_retention_purge = 0.0
        while True:
            try:
                generate_reminders()
                from app.services.push_service import push_pending_notifications
                push_pending_notifications()
            except Exception:
                logger.exception("reminders/push failed")

            now = time.time()
            if now - last_retention_purge >= settings.RETENTION_PURGE_INTERVAL_SECONDS:
                last_retention_purge = now
                _retention_purge()

            if now - last_email_sync >= settings.SYNC_GMAIL_INTERVAL_SECONDS:
                last_email_sync = now
                _sync_gmail()

            if now - last_classroom_sync >= settings.SYNC_CLASSROOM_INTERVAL_SECONDS:
                last_classroom_sync = now
                _sync_classroom()
                _sync_contacts()

            # Bound the loop to run each background batch once; revisit every 60s.
            time.sleep(60)

    threading.Thread(target=_session_worker, daemon=True).start()


def _short(exc: BaseException) -> str:
    try:
        return str(exc)[:300]
    except Exception:
        return type(exc).__name__


@app.get("/api/health")
async def health_check():
    """Public liveness/readiness probe (D7).

    Deliberately minimal: an uptime monitor or reverse proxy needs exactly one
    bit of information - "can this instance serve requests, and can it reach
    its database". Everything else that used to be published here (connection
    pool sizes, app/version, integration/feature flags, operational detail) is
    now reachable only from /api/system/info, which AuthMiddleware requires an
    authenticated session for.

    Returns 200 {"status": "ok"} when the database probe succeeds and
    503 {"status": "error"} when it does not. No reason, host, pool or config
    detail is ever included in the body.
    """
    from app.core import health as health_obs

    if health_obs.db_health().get("status") == "ok":
        return {"status": "ok"}
    return JSONResponse(status_code=503, content={"status": "error"})


@app.get("/api/system/info")
async def system_info(user: User = Depends(get_current_user)):
    """Operational health for a live, authenticated user (D7).

    Reachable only behind the session middleware, and additionally pinned to a
    real, active user: the middleware only proves the bearer signature is
    valid and unexpired, so without this dependency a token issued to a user
    who was later disabled or deleted would still read operational detail.
    Every other session-scoped route resolves the user the same way.
    """
    from app.core import health as health_obs

    try:
        ai = await health_obs.ai_health()
    except Exception:
        ai = {"ollama": {"status": "unavailable"}, "ai_queue": {}, "circuit_breaker": "unknown"}
    return {"system": health_obs.system_health(), "ai": ai}
