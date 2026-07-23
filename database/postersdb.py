"""
Once a poster has been generated and archived to POSTER_CHANNEL, its
Telegram file_id is stored here, keyed by the same cache key used for the
on-disk cache (utils/poster.py). A repeat request for that exact
poster/season/episode then just re-sends the stored file_id — Telegram
handles it server-side, no re-download or re-compositing on our end, and
unlike the disk cache this survives a restart or redeploy.
"""

import logging
from datetime import datetime, timezone

import info
from database.mongo import db

logger = logging.getLogger(__name__)

posters_col = db['poster_cache']


async def get_poster_file_id(cache_key: str):
    doc = await posters_col.find_one({'_id': cache_key})
    return doc['file_id'] if doc else None


async def save_poster_file_id(cache_key: str, file_id: str, kind: str):
    await posters_col.update_one(
        {'_id': cache_key},
        {'$set': {'file_id': file_id, 'kind': kind, 'created_at': datetime.now(timezone.utc)}},
        upsert=True,
    )
