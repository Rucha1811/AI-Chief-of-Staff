from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.core.database import engine, Base
from app.api import emails, events, tasks, documents, briefing, dashboard

Base.metadata.create_all(bind=engine)

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.VERSION,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(dashboard.router)
app.include_router(emails.router)
app.include_router(events.router)
app.include_router(tasks.router)
app.include_router(documents.router)
app.include_router(briefing.router)


@app.get("/api/health")
async def health_check():
    return {"status": "ok", "app": settings.APP_NAME, "version": settings.VERSION}
