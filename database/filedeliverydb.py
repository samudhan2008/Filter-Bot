"""
Records every file the bot actually hands to a user via plugins/start.py's
deliver_file — who got it, which file, and where the request came from (a
specific group, or straight from the bot's own PM). Backs both the
LOG_CHANNEL delivery notice and the admin dashboard's new "File Deliveries"
column/table.

Deliberately a separate collection from database/auditdb.py: auditdb is
about admin-panel *actions* (ban, broadcast, auth...) done by whoever holds
the panel password, while this is about ordinary users receiving files —
different audience, much higher volume, and no reason to mix the two logs.
"""

import logging
from datetime import datetime, timezone

from database.mongo import db

logger = logging.getLogger(__name__)

delivery_col = db['file_deliveries']


async def ensure_delivery_indexes():
    try:
        await delivery_col.create_index('created_at', expireAfterSeconds=60 * 60 * 24 * 60)  # keep 60 days
        await delivery_col.create_index('user_id')
        logger.info("Ensured file-delivery TTL/user indexes.")
    except Exception as e:
        logger.warning(f"Could not ensure file-delivery indexes (non-fatal): {e}")


async def log_delivery(
    user_id: int,
    user_name: str,
    file_id: str,
    file_name: str,
    source_type: str,   # 'private' | 'group'
    source_chat_id,
    source_title: str = "",
):
    await delivery_col.insert_one({
        'user_id': user_id,
        'user_name': user_name or "",
        'file_id': file_id,
        'file_name': file_name or "",
        'source_type': source_type,
        'source_chat_id': source_chat_id,
        'source_title': source_title or "",
        'created_at': datetime.now(timezone.utc),
    })


async def recent(limit: int = 50, user_id: int = None):
    query = {'user_id': user_id} if user_id is not None else {}
    cursor = delivery_col.find(query).sort('created_at', -1).limit(limit)
    entries = await cursor.to_list(length=limit)
    for e in entries:
        e['_id'] = str(e['_id'])
        e['created_at'] = e['created_at'].isoformat()
    return entries


async def total_count() -> int:
    return await delivery_col.count_documents({})
