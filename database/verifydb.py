"""
Anti-bypass for SHORTLINK_MODE.

The gate isn't "did you click the shortlink for *this* file" — a raw deep
link, once seen, can always be copied and reshared, and there's no way for
a plain URL button to check who's opening it before Telegram does. So
instead: the bot itself refuses to hand over *any* file to a user who
isn't currently "verified", and verification can only be obtained by
completing one specific, single-use, per-user shortlink round trip. That
holds regardless of how a file's deep link reaches someone — sharing it
doesn't help a non-verified user, because the bot checks verification
status server-side at delivery time, not at link-creation time.

- verify_tokens: short-lived, single-use, created right before sending
  someone a "please verify" shortlink. Bound to the specific user_id that
  requested it, so it can't be redeemed by anyone else even if the link
  leaks.
- verified_users: user_id -> verified_until. Checked on every file
  delivery. Expires after VERIFY_VALID_HOURS, after which they need to
  verify again.
"""

import logging
import uuid
from datetime import datetime, timezone, timedelta

import info
from database.mongo import db

logger = logging.getLogger(__name__)

tokens_col = db['verify_tokens']
verified_col = db['verified_users']


async def ensure_verify_indexes():
    try:
        await tokens_col.create_index('created_at', expireAfterSeconds=info.VERIFY_TOKEN_TTL)
        logger.info("Ensured verify-token TTL index.")
    except Exception as e:
        logger.warning(f"Could not ensure verify indexes (non-fatal): {e}")


async def create_verify_token(user_id: int) -> str:
    token = uuid.uuid4().hex[:20]
    await tokens_col.insert_one({'_id': token, 'user_id': user_id, 'created_at': datetime.now(timezone.utc)})
    return token


async def redeem_verify_token(token: str, user_id: int) -> bool:
    """Single-use: the token is deleted the moment it's checked, whether or
    not it turns out to be valid. Returns True only if it existed, hadn't
    been used yet, and belongs to this exact user_id."""
    doc = await tokens_col.find_one_and_delete({'_id': token})
    if not doc or doc.get('user_id') != user_id:
        return False
    await verified_col.update_one(
        {'_id': user_id},
        {'$set': {'verified_until': datetime.now(timezone.utc) + timedelta(hours=info.VERIFY_VALID_HOURS)}},
        upsert=True,
    )
    return True


async def is_verified(user_id: int) -> bool:
    doc = await verified_col.find_one({'_id': user_id})
    if not doc or not doc.get('verified_until'):
        return False
    return doc['verified_until'] > datetime.now(timezone.utc)
