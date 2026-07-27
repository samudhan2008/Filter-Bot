"""
Once a poster has been generated and archived to POSTER_CHANNEL, its
Telegram file_id (and message_id, so it can be edited later) is stored
here, keyed by the same cache key used for the on-disk cache
(utils/poster.py). A repeat request for that exact poster/season/episode
then just re-sends the stored file_id — Telegram handles it server-side,
no re-download or re-compositing on our end, and unlike the disk cache
this survives a restart or redeploy.

has_backdrop / has_logo record whether THIS cached poster actually had a
real backdrop/logo from TMDB at the time it was built, or fell back to a
plain background / drawn-text title because TMDB didn't have one yet.
That's what lets a later search notice "TMDB has the artwork now, but our
cached poster predates it" and self-heal — see
utils/poster.py's _resolve_with_channel_cache for that logic.
"""

import logging
from datetime import datetime, timezone

import info
from database.mongo import db

logger = logging.getLogger(__name__)

posters_col = db['poster_cache']


async def get_poster_doc(cache_key: str):
    return await posters_col.find_one({'_id': cache_key})


async def save_poster(cache_key: str, file_id: str, message_id, kind: str, has_backdrop: bool, has_logo: bool):
    await posters_col.update_one(
        {'_id': cache_key},
        {'$set': {
            'file_id': file_id,
            'message_id': message_id,
            'kind': kind,
            'has_backdrop': has_backdrop,
            'has_logo': has_logo,
            'created_at': datetime.now(timezone.utc),
        }},
        upsert=True,
    )
