import logging
import aiohttp

import info
from utils.http import get_session
from database.settingsdb import is_shortlink_mode

logger = logging.getLogger(__name__)


async def shorten(url: str) -> str:
    """Shortens `url` via SHORTLINK_URL/SHORTLINK_API if shortlink mode is
    on (toggleable live from the admin panel — see database/settingsdb.py
    — falling back to the SHORTLINK_MODE env var if never toggled), else
    returns the url unchanged."""
    if not await is_shortlink_mode() or not info.SHORTLINK_URL or not info.SHORTLINK_API:
        return url
    api_url = f"https://{info.SHORTLINK_URL}/api"
    params = {"api": info.SHORTLINK_API, "url": url, "format": "text"}
    try:
        session = await get_session()
        async with session.get(api_url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            text = (await resp.text()).strip()
            if text.startswith("http"):
                return text
            logger.warning(f"Shortener returned unexpected response: {text}")
    except Exception as e:
        logger.warning(f"Shortlink failed: {e}")
    return url
