import json
import os
import time
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from typing import Optional

import httpx

from app.utils import clean_news_summary

_TOPICS = {
    "top": "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en",
    "technology": "https://news.google.com/rss/headlines/section/topic/TECHNOLOGY?hl=en-US&gl=US&ceid=US:en",
    "business": "https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=en-US&gl=US&ceid=US:en",
    "world": "https://news.google.com/rss/headlines/section/topic/WORLD?hl=en-US&gl=US&ceid=US:en",
    "india": "https://news.google.com/rss/headlines/section/topic/NATION?hl=en-IN&gl=IN&ceid=IN:en",
}

_ATOM_SOURCE = "{http://www.w3.org/2005/Atom}source"
_SOURCE_TAGS = ("source", _ATOM_SOURCE)

CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "cache")
CACHE_MAX_AGE_SECONDS = 24 * 60 * 60
CACHE_FALLBACK_MAX_AGE_SECONDS = 7 * 24 * 60 * 60


def _cache_path(topic: str) -> str:
    return os.path.join(CACHE_DIR, f"news_{topic}.json")


def _load_cache(topic: str, max_age: int) -> Optional[list]:
    path = _cache_path(topic)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r") as f:
            data = json.load(f)
        if data.get("cached_at", 0) + max_age < time.time():
            return None
        items = data.get("items", [])
        return items if isinstance(items, list) else None
    except (OSError, json.JSONDecodeError):
        return None


def _save_cache(topic: str, items: list) -> None:
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(_cache_path(topic), "w") as f:
            json.dump({"cached_at": int(time.time()), "items": items}, f)
    except OSError:
        pass


async def _fetch_google_news(topic: str) -> list[dict]:
    url = _TOPICS.get(topic)
    if not url:
        return []
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
    return _parse_rss(resp.text)


def _parse_rss(text: str) -> list[dict]:
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return []
    items = []
    for item in root.iter("item"):
        title_el = item.find("title")
        title = title_el.text if title_el is not None and title_el.text else ""
        if not title:
            continue
        link_el = item.find("link")
        link = link_el.text if link_el is not None else ""
        desc_el = item.find("description")
        description = desc_el.text if desc_el is not None and desc_el.text else ""
        pub_el = item.find("pubDate")
        published_at = None
        if pub_el is not None and pub_el.text:
            try:
                published_at = parsedate_to_datetime(pub_el.text).isoformat()
            except Exception:
                pass
        source_el = None
        for tag in _SOURCE_TAGS:
            source_el = item.find(tag)
            if source_el is not None:
                break
        source = source_el.text if source_el is not None else None
        items.append(
            {
                "title": title,
                "link": link,
                "summary": clean_news_summary(description),
                "source": source,
                "published_at": published_at,
            }
        )
    return items


async def _fetch_hacker_news(limit: int) -> list[dict]:
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        resp = await client.get(
            "https://hn.algolia.com/api/v1/search_by_date",
            params={"tags": "story", "hitsPerPage": limit},
        )
        resp.raise_for_status()
    hits = resp.json().get("hits", [])
    items = []
    for hit in hits:
        title = hit.get("title") or ""
        url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID', '')}"
        if not title:
            continue
        items.append(
            {
                "title": title,
                "link": url,
                "summary": f"{hit.get('points', 0)} points · {hit.get('num_comments', 0)} comments on Hacker News",
                "source": "Hacker News",
                "published_at": hit.get("created_at", None),
            }
        )
        if len(items) >= limit:
            break
    return items


async def fetch_news(topic: str = "technology", limit: int = 20) -> list[dict]:
    topic = topic if topic in _TOPICS else "technology"

    cached = _load_cache(topic, CACHE_MAX_AGE_SECONDS)
    if cached:
        return cached[:limit]

    sources = [
        lambda: _fetch_google_news(topic),
        lambda: _fetch_hacker_news(limit),
    ]

    last_error = None
    for source in sources:
        try:
            items = await source()
            if items:
                _save_cache(topic, items)
                return items[:limit]
        except Exception as e:
            last_error = e

    stale = _load_cache(topic, CACHE_FALLBACK_MAX_AGE_SECONDS)
    if stale:
        return stale[:limit]

    raise RuntimeError(f"All news sources failed: {last_error}")
