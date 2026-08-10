from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from sqlalchemy import inspect, text
from app.core.config import settings
from app.core.database import engine, Base
import app.models  # noqa: F401  (registers all models so create_all sees them)
from app.api import (
    emails,
    events,
    tasks,
    documents,
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
)

_PUBLIC_PATHS = {"/api/auth/login", "/api/auth/status", "/api/health"}


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if (
            request.method == "OPTIONS"
            or path in _PUBLIC_PATHS
            or not path.startswith("/api/")
        ):
            return await call_next(request)
        if not auth.is_authenticated(request):
            return JSONResponse(status_code=401, content={"detail": "Authentication required"})
        return await call_next(request)

Base.metadata.create_all(bind=engine)

_MIGRATIONS = {
    "daily_briefings": [
        ("meetings_highlights", "TEXT"),
        ("email_highlights", "TEXT"),
        ("task_highlights", "TEXT"),
    ],
    "events": [
        ("recurrence_rule", "VARCHAR(32)"),
        ("recurrence_until", "DATETIME"),
    ],
}


def _run_migrations() -> None:
    inspector = inspect(engine)
    tables = {
        t: {c["name"] for c in inspector.get_columns(t)}
        for t in inspector.get_table_names()
    }
    with engine.begin() as conn:
        for table, columns in _MIGRATIONS.items():
            if table not in tables:
                continue
            for col, ddl in columns:
                if col not in tables[table]:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))


_run_migrations()

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.VERSION,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(AuthMiddleware)

app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(news.router)
app.include_router(emails.router)
app.include_router(events.router)
app.include_router(tasks.router)
app.include_router(documents.router)
app.include_router(briefing.router)
app.include_router(timetable.router)
app.include_router(classroom.router)
app.include_router(teams.router)
app.include_router(notifications.router)
app.include_router(exams.router)
app.include_router(assignments.router)
app.include_router(attendance.router)


@app.on_event("startup")
def _startup_background():
    from app.services.reminder_service import generate_reminders

    def _run():
        try:
            from app.services.google_auth import load_credentials
            from app.services.auth_state import AuthStateStore

            if load_credentials() is None:
                AuthStateStore.set_state("not_authorized", reason="not_authorized")
        except Exception:
            pass
        try:
            generate_reminders(force=True)
        except Exception:
            pass
        try:
            import asyncio
            from app.services.classroom_service import sync_submission_status

            asyncio.run(sync_submission_status())
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
                session.close()
        except Exception:
            pass

    import threading

    threading.Thread(target=_run, daemon=True).start()

    def _push_loop():
        from app.services.push_service import push_pending_notifications

        import time

        last_email_sync = 0.0
        last_classroom_sync = 0.0
        while True:
            try:
                generate_reminders()
                push_pending_notifications()
            except Exception:
                pass

            now = time.time()
            if now - last_email_sync >= 600:
                last_email_sync = now
                try:
                    import asyncio
                    from app.core.database import get_db
                    from app.services.gmail_service import sync_emails

                    session = next(get_db())
                    try:
                        asyncio.run(sync_emails(max_results=10, db=session))
                    finally:
                        session.close()
                except Exception:
                    pass

            if now - last_classroom_sync >= 1800:
                last_classroom_sync = now
                try:
                    import asyncio
                    from app.core.database import get_db
                    from app.services.classroom_service import (
                        sync_assignments,
                        sync_submission_status,
                    )

                    session = next(get_db())
                    try:
                        asyncio.run(sync_submission_status(db=session))
                        asyncio.run(sync_assignments(db=session))
                    finally:
                        session.close()
                except Exception:
                    pass

            time.sleep(60)

    threading.Thread(target=_push_loop, daemon=True).start()


@app.get("/api/health")
async def health_check():
    return {"status": "ok", "app": settings.APP_NAME, "version": settings.VERSION}
