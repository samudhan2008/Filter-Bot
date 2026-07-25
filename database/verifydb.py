"""
Anti-bypass for SHORTLINK_MODE, cookie-continuity version.

A raw deep link (or even a raw shortlink destination) can always be copied
and reshared — there's no way for a plain URL to prove who's opening it
before Telegram (or a browser) does. So instead of trying to protect a
link, this proves *continuity*: the same browser that started the
verification flow is the one that finished it, via a random cookie value
set on our own frontend domain at the start and checked again at the end.
Jumping straight to the final URL (skipping the actual shortlink click)
means arriving without that cookie, or with a stale/wrong one — caught and
flagged as a bypass attempt.

Flow (implemented across this module, utils/frontend_api.py, and the
separate Vercel frontend project):
  1. create_session(user_id) — bot generates a session when a non-verified
     user requests a file.
  2. The frontend's /go/<session> page sets a random cookie in the user's
     browser and calls set_session_cookie() to record what it set.
  3. User goes through the actual shortlink (ads, countdown, whatever).
  4. The frontend's /finish/<session> page reads the cookie back and calls
     confirm_session() with it.
  5. confirm_session() checks the cookie matches, grants a verified_until
     window on success, and — one-time use — deletes the session record
     either way, whether it matched or not.
"""

import logging
import uuid
from datetime import datetime, timezone, timedelta

import info
from database.mongo import db

logger = logging.getLogger(__name__)

sessions_col = db['verify_sessions']   # short-lived: one per in-progress verification attempt
verified_col = db['verified_users']    # user_id -> verified_until


async def ensure_verify_indexes():
    try:
        await sessions_col.create_index('created_at', expireAfterSeconds=info.VERIFY_SESSION_TTL)
        logger.info("Ensured verify-session TTL index.")
    except Exception as e:
        logger.warning(f"Could not ensure verify indexes (non-fatal): {e}")


async def create_session(user_id: int) -> str:
    session_id = uuid.uuid4().hex[:24]
    await sessions_col.insert_one({
        '_id': session_id, 'user_id': user_id, 'cookie': None,
        'created_at': datetime.now(timezone.utc),
    })
    return session_id


async def set_session_cookie(session_id: str, cookie: str) -> bool:
    """Called by the frontend's /go page right after it sets the cookie in
    the browser, so we have something to compare against later."""
    result = await sessions_col.update_one({'_id': session_id}, {'$set': {'cookie': cookie}})
    return result.matched_count > 0


async def confirm_session(session_id: str, cookie: str):
    """
    Called by the frontend's /finish page with whatever cookie value it
    read back from the browser. One-time use: the session is deleted here
    regardless of outcome.

    Returns (status, user_id):
      status = "ok"        — cookie matched, user is now verified.
      status = "mismatch"  — session existed but the cookie didn't match
                              (or was missing entirely) — a bypass attempt.
      status = "not_found" — no such session (expired, already used, or
                              never existed) — also treated as suspicious,
                              but there's no user_id to notify.
    """
    doc = await sessions_col.find_one_and_delete({'_id': session_id})
    if not doc:
        return "not_found", None

    user_id = doc.get('user_id')
    if not cookie or doc.get('cookie') != cookie:
        return "mismatch", user_id

    await verified_col.update_one(
        {'_id': user_id},
        {'$set': {'verified_until': datetime.now(timezone.utc) + timedelta(hours=info.VERIFY_VALID_HOURS)}},
        upsert=True,
    )
    return "ok", user_id


async def is_verified(user_id: int) -> bool:
    doc = await verified_col.find_one({'_id': user_id})
    if not doc or not doc.get('verified_until'):
        return False
    return doc['verified_until'] > datetime.now(timezone.utc)
