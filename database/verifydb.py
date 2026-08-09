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

import ipaddress
import logging
import uuid
from datetime import datetime, timezone, timedelta

import info
from database.mongo import db
from utils.cache import TTLCache

logger = logging.getLogger(__name__)

sessions_col = db['verify_sessions']   # short-lived: one per in-progress verification attempt
verified_col = db['verified_users']    # user_id -> verified_until
confirm_tokens_col = db['confirm_tokens']  # one-time token for the final "return to Telegram" redirect

# How long an unused post-verification confirmation token stays valid.
# Not a security boundary (the actual verification already happened
# server-side by the time this token exists) — just how long the landing
# page's "Return to Telegram" link/button stays clickable before someone
# would need to verify again to get a fresh one.
CONFIRM_TOKEN_TTL = 600

# Rate-limits how often a fresh verification session can be handed out to
# the same user — not to stop a legitimate person (nobody taps "verify"
# more than once every few seconds), but to make it pointless for a script
# to rapid-fire /start requests probing the session system.
_session_cooldown = TTLCache(ttl=info.VERIFY_SESSION_COOLDOWN, max_size=5000)


def _ip_network(ip: str, prefix_v4: int = 24, prefix_v6: int = 64):
    """Coarse-grained network for an IP, used to compare "close enough to
    the same connection" rather than an exact match — mobile networks and
    some ISPs legitimately rotate the exact address mid-session, but
    jumping to a totally different subnet (let alone a different country)
    between /go and /finish is a real signal something's off."""
    if not ip:
        return None
    try:
        addr = ipaddress.ip_address(ip.strip())
        prefix = prefix_v4 if addr.version == 4 else prefix_v6
        return ipaddress.ip_network(f"{addr}/{prefix}", strict=False)
    except ValueError:
        return None


async def ensure_verify_indexes():
    try:
        await sessions_col.create_index('created_at', expireAfterSeconds=info.VERIFY_SESSION_TTL)
        await confirm_tokens_col.create_index('created_at', expireAfterSeconds=CONFIRM_TOKEN_TTL)
        logger.info("Ensured verify-session and confirm-token TTL indexes.")
    except Exception as e:
        logger.warning(f"Could not ensure verify indexes (non-fatal): {e}")


async def can_create_session(user_id: int) -> bool:
    return _session_cooldown.get(user_id) is None


async def create_session(user_id: int) -> str:
    session_id = uuid.uuid4().hex[:24]
    await sessions_col.insert_one({
        '_id': session_id, 'user_id': user_id, 'cookie': None, 'ip': None,
        'created_at': datetime.now(timezone.utc),
    })
    _session_cooldown.set(user_id, True)
    return session_id


async def set_session_cookie(session_id: str, cookie: str, ip: str = None) -> bool:
    """Called by the frontend's /go page right after it sets the cookie in
    the browser, so we have something to compare against later. Also
    records the IP seen at this point, for the coarse consistency check in
    confirm_session()."""
    result = await sessions_col.update_one({'_id': session_id}, {'$set': {'cookie': cookie, 'ip': ip}})
    return result.matched_count > 0


async def create_confirm_token(user_id: int) -> str:
    """A random one-time token for the final Telegram redirect after a
    successful verification — used instead of a generic, static
    "?start=verified" so the landing message is tied to this specific
    verification for this specific user, not a guessable/reusable fixed
    string. Single-use (see redeem_confirm_token) and freshly regenerated
    every time someone verifies, never reused."""
    token = str(uuid.uuid4())
    await confirm_tokens_col.insert_one({
        '_id': token, 'user_id': user_id, 'created_at': datetime.now(timezone.utc),
    })
    return token


async def redeem_confirm_token(token: str):
    """One-time use — deletes the token the moment it's checked, whether
    or not it existed. Returns the bound user_id on success, else None."""
    doc = await confirm_tokens_col.find_one_and_delete({'_id': token})
    return doc['user_id'] if doc else None


async def confirm_session(session_id: str, cookie: str, ip: str = None):
    """
    Called by the frontend's /finish page with whatever cookie/IP it read
    for this request. One-time use: the session is deleted here regardless
    of outcome.

    Returns (status, user_id, confirm_token):
      status = "ok"           — cookie matched (and, if STRICT_IP_CHECK is
                                 on, the IP was consistent too).
      status = "ok_flagged"   — cookie matched but the IP looked
                                 inconsistent; still verified (the cookie is
                                 the strong signal — IPs legitimately change
                                 mid-session on mobile networks), but logged
                                 for visibility. Only distinguished from
                                 "ok" when STRICT_IP_CHECK is off.
      status = "mismatch"     — cookie didn't match (or STRICT_IP_CHECK is
                                 on and the IP didn't either) — a bypass
                                 attempt.
      status = "not_found"    — no such session (expired, already used, or
                                 never existed) — also treated as
                                 suspicious, but there's no user_id to notify.

    confirm_token is only ever non-None when status is "ok"/"ok_flagged" —
    see create_confirm_token().
    """
    doc = await sessions_col.find_one_and_delete({'_id': session_id})
    if not doc:
        return "not_found", None, None

    user_id = doc.get('user_id')
    if not cookie or doc.get('cookie') != cookie:
        return "mismatch", user_id, None

    ip_consistent = True
    stored_net = _ip_network(doc.get('ip'))
    seen_net = _ip_network(ip)
    if stored_net is not None and seen_net is not None and stored_net != seen_net:
        ip_consistent = False
        logger.warning(f"Verify session {session_id} (user {user_id}): IP moved from {doc.get('ip')} to {ip}")

    if not ip_consistent and info.STRICT_IP_CHECK:
        return "mismatch", user_id, None

    await verified_col.update_one(
        {'_id': user_id},
        {'$set': {'verified_until': datetime.now(timezone.utc) + timedelta(hours=info.VERIFY_VALID_HOURS)}},
        upsert=True,
    )
    confirm_token = await create_confirm_token(user_id)
    return ("ok" if ip_consistent else "ok_flagged"), user_id, confirm_token


async def is_verified(user_id: int) -> bool:
    doc = await verified_col.find_one({'_id': user_id})
    if not doc or not doc.get('verified_until'):
        return False
    verified_until = doc['verified_until']
    if verified_until.tzinfo is None:
        # Defensive: shouldn't happen with tz_aware=True on the shared
        # client (database/mongo.py), but never let a naive/aware mismatch
        # here raise and silently kill the search flow again.
        verified_until = verified_until.replace(tzinfo=timezone.utc)
    return verified_until > datetime.now(timezone.utc)
