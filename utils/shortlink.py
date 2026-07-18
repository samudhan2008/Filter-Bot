import logging
import aiohttp

import info

logger = logging.getLogger(__name__)


async def shorten(url: str) -> str:
    """Shortens `url` via SHORTLINK_URL/SHORTLINK_API if SHORTLINK_MODE is on,
    else returns the url unchanged."""
    if not info.SHORTLINK_MODE or not info.SHORTLINK_URL or not info.SHORTLINK_API:
        return url
    api_url = f"https://{info.SHORTLINK_URL}/api"
    params = {"api": info.SHORTLINK_API, "url": url, "format": "text"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                text = (await resp.text()).strip()
                if text.startswith("http"):
                    return text
                logger.warning(f"Shortener returned unexpected response: {text}")
    except Exception as e:
        logger.warning(f"Shortlink failed: {e}")
    return url
