import logging

from fastapi import APIRouter, HTTPException
from typing import Optional

from app.core.limits import clamp_limit
from app.services.news_service import fetch_news

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/news", tags=["news"])


@router.get("")
async def get_news(
    topic: Optional[str] = "technology", limit: int = 20
) -> dict:
    try:
        items = await fetch_news(topic=topic or "technology", limit=clamp_limit(limit, 50))
        return {"topic": topic or "technology", "items": items, "cached": False}
    except RuntimeError as e:
        logger.warning("news fetch failed: %s", e)
        raise HTTPException(status_code=503, detail="News is temporarily unavailable.")
