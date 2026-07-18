"""
Talks to the SC Files website backend (BACKEND_URL/api/movies, /api/series).

The "advanced checking" requirement is solved by matching on `tmdb_id`
instead of trying to reconstruct a slug ourselves — every entry in the
backend already carries its own authoritative `id`, so once we find the
entry whose tmdb_id matches, we just use that id verbatim for the website
link (e.g. "sannidhanam-po" vs "sannidhanam-p.o." is a non-issue because we
never guess it).

As a fallback (e.g. a legacy backend entry with a missing/blank tmdb_id),
we fuzzy-match the title against each entry's `id` field.
"""

import logging
import time
import difflib
import re

import aiohttp

import info

logger = logging.getLogger(__name__)

_cache = {"movies": None, "series": None, "ts": {"movies": 0, "series": 0}}


async def _fetch(kind: str):
    """kind: 'movies' or 'series'. Cached for BACKEND_CACHE_TTL seconds."""
    now = time.time()
    if _cache[kind] is not None and (now - _cache["ts"][kind]) < info.BACKEND_CACHE_TTL:
        return _cache[kind]

    if not info.BACKEND_URL:
        logger.warning("BACKEND_URL is not set; cannot check website availability.")
        return []

    url = f"{info.BACKEND_URL}/api/{kind}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    logger.warning(f"Backend {url} returned HTTP {resp.status}")
                    return _cache[kind] or []
                data = await resp.json(content_type=None)
    except Exception as e:
        logger.warning(f"Failed to fetch {url}: {e}")
        return _cache[kind] or []

    if not isinstance(data, list):
        logger.warning(f"Unexpected backend response shape from {url}")
        return _cache[kind] or []

    _cache[kind] = data
    _cache["ts"][kind] = now
    return data


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


async def find_entry(kind: str, tmdb_id, title: str = ""):
    """
    kind: 'movie' or 'series'
    Returns the raw backend entry dict if found, else None.
    """
    api_kind = "movies" if kind == "movie" else "series"
    entries = await _fetch(api_kind)
    if not entries:
        return None

    # 1) Primary: exact tmdb_id match (robust, ignores id-string quirks entirely)
    if tmdb_id is not None:
        tmdb_id_str = str(tmdb_id)
        for entry in entries:
            if str(entry.get('tmdb_id', '')) == tmdb_id_str:
                return entry

    # 2) Fallback: fuzzy-match title against each entry's own `id` slug
    if title:
        target = _slugify(title)
        best, best_score = None, 0.0
        for entry in entries:
            entry_id = str(entry.get('id', ''))
            score = difflib.SequenceMatcher(None, target, entry_id).ratio()
            if score > best_score:
                best, best_score = entry, score
        if best is not None and best_score >= 0.82:
            return best

    return None


def website_link(kind: str, entry: dict) -> str:
    path = "movie" if kind == "movie" else "series"
    return f"{info.WEBSITE_URL}/{path}?id={entry.get('id')}"
