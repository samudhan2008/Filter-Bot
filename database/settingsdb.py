"""
A handful of settings (currently just shortlink_mode) can be flipped live
from the admin panel instead of requiring an env var change + redeploy.
The env var (info.py) is still what a *fresh* install starts with — this
collection only comes into play once something's been explicitly toggled
at runtime, at which point it takes precedence.

Cached briefly (TTLCache, a few seconds) so every request that checks a
setting doesn't cost a Mongo round trip — a toggle from the panel still
takes effect everywhere within that short window, not instantly, but close
enough for a manual admin action.
"""

import logging

import info
from database.mongo import db
from utils.cache import TTLCache

logger = logging.getLogger(__name__)

settings_col = db['settings']
_cache = TTLCache(ttl=10, max_size=50)


async def get_setting(key: str, default=None):
    cached = _cache.get(key)
    if cached is not None:
        return cached["value"]
    doc = await settings_col.find_one({'_id': key})
    value = doc['value'] if doc is not None else default
    _cache.set(key, {"value": value})
    return value


async def set_setting(key: str, value):
    await settings_col.update_one({'_id': key}, {'$set': {'value': value}}, upsert=True)
    _cache.set(key, {"value": value})  # update immediately, don't wait for the TTL to expire


async def is_shortlink_mode() -> bool:
    return bool(await get_setting('shortlink_mode', info.SHORTLINK_MODE))


async def is_pm_search_mode() -> bool:
    return bool(await get_setting('pm_search_mode', info.PM_SEARCH_MODE))
