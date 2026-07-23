"""
Search-flow state (pending disambiguation picks, result pages backing the
file/pagination buttons, cancel flags) used to live in plain Python dicts
in plugins/search.py. That meant a bot restart — a redeploy, a crash, an
OOM kill, or simply running more than one instance — wiped everything
instantly: any button tapped after that pointed at a token nothing knew
about anymore, showing "This result has expired" even for a search from
seconds earlier.

Moved into MongoDB instead: same lookup-by-token shape, but it survives
restarts and is shared correctly across multiple instances if this bot is
ever scaled horizontally. TTL indexes handle expiry automatically — no
manual GC sweep needed anymore.
"""

import logging
from datetime import datetime, timezone

import info
from database.mongo import db

logger = logging.getLogger(__name__)

results_col = db['search_results']   # file-delivery + pagination state
pending_col = db['search_pending']   # disambiguation candidate picks
cancel_col = db['search_cancel']     # "checking TMDB" cancel flags
index_wait_col = db['index_pending']  # admin's pending "send me the channel link" state


async def ensure_state_indexes():
    try:
        await results_col.create_index('created_at', expireAfterSeconds=info.RESULTS_TTL)
        await pending_col.create_index('created_at', expireAfterSeconds=info.PENDING_TTL)
        await cancel_col.create_index('created_at', expireAfterSeconds=180)
        await index_wait_col.create_index('created_at', expireAfterSeconds=info.INDEX_WAIT_TTL)
        logger.info("Ensured search-state TTL indexes (results/pending/cancel/index_wait).")
    except Exception as e:
        logger.warning(f"Could not ensure search-state indexes (non-fatal): {e}")


# ---- Results (file buttons + pagination) ----
async def store_results(token: str, files: list, ctx: dict):
    await results_col.update_one(
        {'_id': token},
        {'$set': {'files': files, 'ctx': ctx, 'created_at': datetime.now(timezone.utc)}},
        upsert=True,
    )


async def get_results(token: str):
    return await results_col.find_one({'_id': token})


async def update_results(token: str, files: list, ctx: dict):
    """Overwrites files/ctx in place for a pagination click, without
    resetting the TTL clock (keeps the original created_at)."""
    await results_col.update_one({'_id': token}, {'$set': {'files': files, 'ctx': ctx}})


# ---- Pending disambiguation picks ----
async def store_pending(token: str, data: dict):
    doc = dict(data)
    doc['_id'] = token
    doc['created_at'] = datetime.now(timezone.utc)
    await pending_col.replace_one({'_id': token}, doc, upsert=True)


async def get_pending(token: str):
    return await pending_col.find_one({'_id': token})


# ---- Cancel flags ----
async def set_cancelled(token: str):
    await cancel_col.update_one(
        {'_id': token},
        {'$set': {'cancelled': True, 'created_at': datetime.now(timezone.utc)}},
        upsert=True,
    )


async def pop_cancelled(token: str) -> bool:
    """Checks and clears in one step — a search only needs to know once."""
    doc = await cancel_col.find_one_and_delete({'_id': token})
    return bool(doc and doc.get('cancelled'))


# ---- /index's "waiting for the channel link" state ----
async def set_index_wait(user_id: int, chat_id: int):
    await index_wait_col.update_one(
        {'_id': user_id},
        {'$set': {'chat_id': chat_id, 'created_at': datetime.now(timezone.utc)}},
        upsert=True,
    )


async def pop_index_wait(user_id: int):
    return await index_wait_col.find_one_and_delete({'_id': user_id})
