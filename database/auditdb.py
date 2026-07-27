"""
Every mutating action taken through the admin panel (ban, broadcast, auth
changes, indexing triggers, backend cache refresh) gets logged here. The
panel uses a single shared password rather than per-admin accounts, so
this can't attribute actions to a specific person — but it still gives a
timestamped record of *what* happened, which matters for a panel that can
ban users and message your entire audience.
"""

import logging
from datetime import datetime, timezone

from database.mongo import db

logger = logging.getLogger(__name__)

audit_col = db['admin_audit']


async def ensure_audit_indexes():
    try:
        await audit_col.create_index('created_at', expireAfterSeconds=60 * 60 * 24 * 30)  # keep 30 days
        logger.info("Ensured admin-audit TTL index.")
    except Exception as e:
        logger.warning(f"Could not ensure audit indexes (non-fatal): {e}")


async def log_action(action: str, details: dict = None):
    await audit_col.insert_one({
        'action': action,
        'details': details or {},
        'created_at': datetime.now(timezone.utc),
    })


async def recent(limit: int = 50):
    cursor = audit_col.find({}).sort('created_at', -1).limit(limit)
    entries = await cursor.to_list(length=limit)
    for e in entries:
        e['_id'] = str(e['_id'])
        e['created_at'] = e['created_at'].isoformat()
    return entries
