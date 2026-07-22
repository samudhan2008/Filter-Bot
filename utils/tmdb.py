"""
TMDB lookups. Two things this fixes vs the old bot:
 1. Language/region confusion ("leo" -> English film instead of Tamil) is
    resolved by (a) preferring results whose original_language is one of
    PREFERRED_LANGS when the query doesn't disambiguate, and (b) letting the
    search flow ask the user with title+year+language buttons when there
    are multiple plausible candidates.
 2. Every result carries backdrop_path (landscape) + we separately fetch the
    title logo for the poster compositor.
"""

import asyncio
import logging
import aiohttp

import info
from utils.cache import TTLCache
from utils.netutil import retry_async, CircuitBreaker
from utils.http import get_session

logger = logging.getLogger(__name__)

TMDB_API = "https://api.themoviedb.org/3"

# Tamil first (SC Files' core audience), then other common South Indian +
# English/Hindi, used only to break ties when the query is ambiguous.
PREFERRED_LANGS = ["ta", "ml", "te", "kn", "hi", "en"]

_search_cache = TTLCache(ttl=info.TMDB_CACHE_TTL, max_size=5000)
_logo_cache = TTLCache(ttl=info.TMDB_CACHE_TTL * 6, max_size=3000)  # logos don't change often
_breaker = CircuitBreaker(fail_threshold=5, cooldown=90)


@retry_async(retries=3, base_delay=0.5, exceptions=(aiohttp.ClientError, asyncio.TimeoutError))
async def _get(session, path, params):
    params = {**params, "api_key": info.TMDB_API_KEY}
    async with session.get(f"{TMDB_API}{path}", params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
        if resp.status == 429:
            raise aiohttp.ClientError("TMDB rate limited (429)")
        if resp.status != 200:
            return None
        return await resp.json()


async def search_multi(query: str, year: int = None, kind: str = None):
    """
    Returns a list of normalized candidate dicts:
    {tmdb_id, kind ('movie'/'tv'), title, year, language, backdrop_path, poster_path}
    kind: force 'movie' or 'tv', else search both.
    Cached per (query, year, kind); backed by a circuit breaker so a TMDB
    outage doesn't stall every search in the group.
    """
    if not info.TMDB_API_KEY:
        logger.warning("TMDB_API_KEY not set.")
        return []

    cache_key = (query.lower(), year, kind)
    cached = _search_cache.get(cache_key)
    if cached is not None:
        return cached

    if not _breaker.allow():
        logger.warning("TMDB circuit breaker open — skipping live search.")
        return []

    candidates = []
    try:
        session = await get_session()
        kinds = [kind] if kind else ["movie", "tv"]

        async def _search_kind(k):
            params = {"query": query, "include_adult": "false"}
            if year:
                params["year" if k == "movie" else "first_air_date_year"] = year
            data = await _get(session, f"/search/{k}", params)
            results = []
            if data:
                for r in data.get("results", [])[:8]:
                    title = r.get("title") or r.get("name") or ""
                    date = r.get("release_date") or r.get("first_air_date") or ""
                    results.append({
                        "tmdb_id": r.get("id"),
                        "kind": "movie" if k == "movie" else "series",
                        "title": title,
                        "year": date[:4] if date else "",
                        "language": r.get("original_language", ""),
                        "backdrop_path": r.get("backdrop_path"),
                        "poster_path": r.get("poster_path"),
                        "popularity": r.get("popularity", 0),
                    })
            return results

        # movie + tv searches are independent — run them concurrently
        # instead of one after the other.
        per_kind_results = await asyncio.gather(*(_search_kind(k) for k in kinds))
        for results in per_kind_results:
            candidates.extend(results)
        _breaker.record_success()
    except Exception as e:
        _breaker.record_failure()
        logger.warning(f"TMDB search_multi failed: {e}")
        return []

    # Rank: preferred language first, then popularity.
    def sort_key(c):
        try:
            lang_rank = PREFERRED_LANGS.index(c["language"])
        except ValueError:
            lang_rank = len(PREFERRED_LANGS)
        return (lang_rank, -c["popularity"])

    candidates.sort(key=sort_key)
    _search_cache.set(cache_key, candidates)
    return candidates


async def get_logo_url(tmdb_id: int, kind: str):
    """Best available title logo (transparent PNG), preferring English then
    any language, largest first. Returns None if TMDB has no logo."""
    if not info.TMDB_API_KEY:
        return None

    cache_key = (tmdb_id, kind)
    cached = _logo_cache.get(cache_key)
    if cached is not None:
        return cached or None  # cache stores "" for "no logo" to avoid re-fetching every time

    if not _breaker.allow():
        return None

    api_kind = "movie" if kind == "movie" else "tv"
    try:
        session = await get_session()
        data = await _get(session, f"/{api_kind}/{tmdb_id}/images", {"include_image_language": "en,ta,null"})
        _breaker.record_success()
    except Exception as e:
        _breaker.record_failure()
        logger.warning(f"TMDB get_logo_url failed: {e}")
        return None

    if not data:
        _logo_cache.set(cache_key, "")
        return None
    logos = data.get("logos", [])
    if not logos:
        _logo_cache.set(cache_key, "")
        return None
    logos.sort(key=lambda l: (l.get("iso_639_1") != "en", -l.get("width", 0)))
    best = logos[0]
    url = f"{info.TMDB_IMG_BASE}/w500{best['file_path']}"
    _logo_cache.set(cache_key, url)
    return url


def backdrop_url(path: str, size="w780"):
    if not path:
        return None
    return f"{info.TMDB_IMG_BASE}/{size}{path}"


def poster_url(path: str, size="w500"):
    if not path:
        return None
    return f"{info.TMDB_IMG_BASE}/{size}{path}"


_season_cache = TTLCache(ttl=info.TMDB_CACHE_TTL * 6, max_size=1000)  # season lists don't change often


async def get_seasons(tv_id):
    """Returns [{season_number, name, episode_count}, ...] for a TV show,
    season 0 ("Specials") excluded unless it's the only season that exists."""
    if not info.TMDB_API_KEY:
        return []
    cache_key = ("seasons", tv_id)
    cached = _season_cache.get(cache_key)
    if cached is not None:
        return cached
    if not _breaker.allow():
        return []
    try:
        session = await get_session()
        data = await _get(session, f"/tv/{tv_id}", {})
        _breaker.record_success()
    except Exception as e:
        _breaker.record_failure()
        logger.warning(f"TMDB get_seasons failed: {e}")
        return []
    if not data:
        return []
    seasons = [
        {"season_number": s["season_number"], "name": s.get("name") or f"Season {s['season_number']}",
         "episode_count": s.get("episode_count", 0)}
        for s in data.get("seasons", [])
    ]
    real = [s for s in seasons if s["season_number"] > 0]
    result = real if real else seasons
    result.sort(key=lambda s: s["season_number"])
    _season_cache.set(cache_key, result)
    return result


async def get_season_poster(tv_id, season_number):
    """TMDB doesn't provide a per-season *backdrop* (landscape) — only a
    poster (portrait). Returns that poster's path, or None. Callers that
    want a landscape image should fall back to the show's overall
    backdrop_path when this comes back empty, or handle the portrait
    aspect explicitly (see utils/poster.py's blurred-fill mode)."""
    if not info.TMDB_API_KEY:
        return None
    cache_key = ("season_poster", tv_id, season_number)
    cached = _season_cache.get(cache_key)
    if cached is not None:
        return cached or None
    if not _breaker.allow():
        return None
    try:
        session = await get_session()
        data = await _get(session, f"/tv/{tv_id}/season/{season_number}", {})
        _breaker.record_success()
    except Exception as e:
        _breaker.record_failure()
        logger.warning(f"TMDB get_season_poster failed: {e}")
        return None
    path = data.get("poster_path") if data else None
    _season_cache.set(cache_key, path or "")
    return path


async def get_episode_details(tv_id, season_number, episode_number):
    """
    Unlike seasons, individual episodes DO have a proper landscape image
    (`still_path` — an actual frame/still from the episode, standard 16:9-
    ish backdrop aspect), so a specific-episode result doesn't need the
    season poster's blur-fill workaround at all.

    Returns {name, overview, air_date, still_path, runtime, vote_average}
    or None.
    """
    if not info.TMDB_API_KEY:
        return None
    cache_key = ("episode", tv_id, season_number, episode_number)
    cached = _season_cache.get(cache_key)
    if cached is not None:
        return cached or None
    if not _breaker.allow():
        return None
    try:
        session = await get_session()
        data = await _get(session, f"/tv/{tv_id}/season/{season_number}/episode/{episode_number}", {})
        _breaker.record_success()
    except Exception as e:
        _breaker.record_failure()
        logger.warning(f"TMDB get_episode_details failed: {e}")
        return None
    if not data:
        _season_cache.set(cache_key, "")
        return None
    result = {
        "name": data.get("name"),
        "overview": data.get("overview"),
        "air_date": data.get("air_date"),
        "still_path": data.get("still_path"),
        "runtime": data.get("runtime"),
        "vote_average": data.get("vote_average"),
    }
    _season_cache.set(cache_key, result)
    return result
