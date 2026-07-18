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

import logging
import aiohttp

import info

logger = logging.getLogger(__name__)

TMDB_API = "https://api.themoviedb.org/3"

# Tamil first (SC Files' core audience), then other common South Indian +
# English/Hindi, used only to break ties when the query is ambiguous.
PREFERRED_LANGS = ["ta", "ml", "te", "kn", "hi", "en"]


async def _get(session, path, params):
    params = {**params, "api_key": info.TMDB_API_KEY}
    async with session.get(f"{TMDB_API}{path}", params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
        if resp.status != 200:
            return None
        return await resp.json()


async def search_multi(query: str, year: int = None, kind: str = None):
    """
    Returns a list of normalized candidate dicts:
    {tmdb_id, kind ('movie'/'tv'), title, year, language, backdrop_path, poster_path}
    kind: force 'movie' or 'tv', else search both.
    """
    if not info.TMDB_API_KEY:
        logger.warning("TMDB_API_KEY not set.")
        return []

    candidates = []
    async with aiohttp.ClientSession() as session:
        kinds = [kind] if kind else ["movie", "tv"]
        for k in kinds:
            params = {"query": query, "include_adult": "false"}
            if year:
                params["year" if k == "movie" else "first_air_date_year"] = year
            data = await _get(session, f"/search/{k}", params)
            if not data:
                continue
            for r in data.get("results", [])[:8]:
                title = r.get("title") or r.get("name") or ""
                date = r.get("release_date") or r.get("first_air_date") or ""
                candidates.append({
                    "tmdb_id": r.get("id"),
                    "kind": "movie" if k == "movie" else "series",
                    "title": title,
                    "year": date[:4] if date else "",
                    "language": r.get("original_language", ""),
                    "backdrop_path": r.get("backdrop_path"),
                    "poster_path": r.get("poster_path"),
                    "popularity": r.get("popularity", 0),
                })

    # Rank: preferred language first, then popularity.
    def sort_key(c):
        try:
            lang_rank = PREFERRED_LANGS.index(c["language"])
        except ValueError:
            lang_rank = len(PREFERRED_LANGS)
        return (lang_rank, -c["popularity"])

    candidates.sort(key=sort_key)
    return candidates


async def get_logo_url(tmdb_id: int, kind: str):
    """Best available title logo (transparent PNG), preferring English then
    any language, largest first. Returns None if TMDB has no logo."""
    if not info.TMDB_API_KEY:
        return None
    api_kind = "movie" if kind == "movie" else "tv"
    async with aiohttp.ClientSession() as session:
        data = await _get(session, f"/{api_kind}/{tmdb_id}/images", {"include_image_language": "en,ta,null"})
    if not data:
        return None
    logos = data.get("logos", [])
    if not logos:
        return None
    logos.sort(key=lambda l: (l.get("iso_639_1") != "en", -l.get("width", 0)))
    best = logos[0]
    return f"{info.TMDB_IMG_BASE}/original{best['file_path']}"


def backdrop_url(path: str, size="w1280"):
    if not path:
        return None
    return f"{info.TMDB_IMG_BASE}/{size}{path}"
