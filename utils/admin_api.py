"""
HTTP API backing the admin dashboard on the Vercel frontend. Every route
requires ADMIN_API_SECRET in X-API-Secret — a *different* secret from the
one the verify flow uses (utils/frontend_api.py), since this API can ban
users, broadcast messages, and trigger indexing: meaningfully higher
stakes than "did a shortlink cookie match", so it gets its own credential
rather than reusing that one.

This mirrors the existing Telegram-side admin commands (plugins/admin.py)
but as JSON endpoints instead of chat commands — the two aren't merged
into one implementation because the Telegram commands are built around
replying to a Message (e.g. /broadcast replies to the message being
broadcast, to support media), which doesn't map cleanly onto a stateless
HTTP call. Some behaviors are simplified here as a result (the API
broadcast is text-only, for instance).
"""

import asyncio
import logging
import re
import time as _time

from aiohttp import web
from pyrogram import Client, enums

import info
from database import usersdb, filesdb, backend
from utils.clients import worker_count

logger = logging.getLogger(__name__)

LINK_RE = re.compile(r"(https://)?(t\.me/|telegram\.me/|telegram\.dog/)(c/)?(\d+|[a-zA-Z_0-9]+)/(\d+)$")


def _check_secret(request: web.Request) -> bool:
    if not info.ADMIN_API_SECRET:
        return False  # refuse everything if no secret is configured — never run this open
    return request.headers.get('X-API-Secret') == info.ADMIN_API_SECRET


def _guarded(handler):
    async def wrapper(request):
        if not _check_secret(request):
            return web.json_response({'ok': False, 'error': 'unauthorized'}, status=401)
        try:
            return await handler(request)
        except Exception as e:
            logger.exception("Admin API handler failed")
            return web.json_response({'ok': False, 'error': str(e)}, status=500)
    return wrapper


@_guarded
async def api_stats(request: web.Request):
    users = await usersdb.total_users_count()
    groups = await usersdb.total_groups_count()
    coll = filesdb.Media.collection
    files_total = await coll.count_documents({})
    movies_files = await coll.count_documents({'season_number': None})
    series_files = await coll.count_documents({'season_number': {'$ne': None}})

    from database.backend import _cache as backend_cache
    now = _time.time()
    movies_age = int(now - backend_cache["ts"]["movies"]) if backend_cache["ts"]["movies"] else None
    series_age = int(now - backend_cache["ts"]["series"]) if backend_cache["ts"]["series"] else None

    return web.json_response({
        'ok': True,
        'users': users,
        'groups': groups,
        'files_total': files_total,
        'movie_files': movies_files,
        'series_files': series_files,
        'backend_movies_cache_age_sec': movies_age,
        'backend_series_cache_age_sec': series_age,
        'worker_bots': worker_count(),
        'search_mode': 'text' if info.USE_MONGO_TEXT_SEARCH else 'regex',
        'shortlink_mode': info.SHORTLINK_MODE,
    })


@_guarded
async def api_ban(request: web.Request):
    data = await request.json()
    user_id = int(data['user_id'])
    reason = data.get('reason', '')
    await usersdb.ban_user(user_id, reason)
    return web.json_response({'ok': True})


@_guarded
async def api_unban(request: web.Request):
    data = await request.json()
    user_id = int(data['user_id'])
    await usersdb.unban_user(user_id)
    return web.json_response({'ok': True})


@_guarded
async def api_authlist(request: web.Request):
    cursor = usersdb.groups_col.find({'authorized': True})
    groups = await cursor.to_list(length=200)
    return web.json_response({
        'ok': True,
        'groups': [{'id': g['_id'], 'title': g.get('title', '')} for g in groups],
    })


@_guarded
async def api_auth(request: web.Request):
    data = await request.json()
    group_id = int(data['group_id'])
    await usersdb.add_group(group_id, data.get('title', ''))
    await usersdb.set_group_authorized(group_id, True)
    return web.json_response({'ok': True})


@_guarded
async def api_unauth(request: web.Request):
    data = await request.json()
    group_id = int(data['group_id'])
    await usersdb.set_group_authorized(group_id, False)
    return web.json_response({'ok': True})


@_guarded
async def api_broadcast(request: web.Request):
    data = await request.json()
    target = data.get('target', 'users')
    text = (data.get('text') or '').strip()
    if not text:
        return web.json_response({'ok': False, 'error': 'text is required'}, status=400)

    bot: Client = request.app['bot_client']
    ids = await usersdb.all_group_ids() if target == 'groups' else await usersdb.all_user_ids()

    # Runs in the background — a broadcast to a large user base can take a
    # while, and there's no reason to hold the HTTP request (and the
    # admin's browser tab) open for it.
    async def _run():
        sent = failed = 0
        for cid in ids:
            try:
                await bot.send_message(cid, text)
                sent += 1
            except Exception:
                failed += 1
            await asyncio.sleep(0.05)
        if info.LOG_CHANNEL:
            try:
                await bot.send_message(
                    info.LOG_CHANNEL,
                    f"📢 Admin-panel broadcast to {target} finished.\nSent: {sent}, Failed: {failed}",
                )
            except Exception:
                pass

    asyncio.create_task(_run())
    return web.json_response({'ok': True, 'queued_for': len(ids), 'target': target})


@_guarded
async def api_reindex_check(request: web.Request):
    movies, series = await backend.force_refresh()
    backend_ids = {str(e.get('id', '')).lower() for e in movies + series}

    cursor = filesdb.Media.collection.aggregate([
        {"$group": {"_id": "$normalized_name"}},
        {"$limit": 5000},
    ])
    distinct_titles = [doc["_id"] async for doc in cursor]

    missing = []
    for title in distinct_titles:
        slug = title.replace(' ', '-')
        if not any(slug in bid or bid in slug for bid in backend_ids):
            missing.append(title)
        if len(missing) >= 100:
            break

    return web.json_response({'ok': True, 'checked': len(distinct_titles), 'missing': missing})


@_guarded
async def api_index(request: web.Request):
    data = await request.json()
    link = (data.get('channel_link') or '').strip()
    match = LINK_RE.match(link)
    if not match:
        return web.json_response({'ok': False, 'error': 'Invalid channel link'}, status=400)

    chat_id = match.group(4)
    last_msg_id = int(match.group(5))
    if chat_id.isnumeric():
        chat_id = int("-100" + chat_id)

    bot: Client = request.app['bot_client']

    from plugins.index import _iter_messages_by_id, _lock
    from database.filesdb import save_files_batch

    if _lock.locked():
        return web.json_response({'ok': False, 'error': 'Another indexing job is already running'}, status=409)

    # Runs in the background and reports to LOG_CHANNEL when done — indexing
    # a large channel can take a long time, far past any reasonable HTTP
    # timeout, so the API just kicks it off and returns immediately.
    async def _run():
        total_saved = duplicate = errors = 0
        buffer = []

        async def flush():
            nonlocal total_saved, duplicate, errors, buffer
            if not buffer:
                return
            saved, dup, err = await save_files_batch(buffer)
            total_saved += saved
            duplicate += dup
            errors += err
            buffer = []

        async with _lock:
            try:
                async for msg in _iter_messages_by_id(bot, chat_id, last_msg_id):
                    if msg.empty or not msg.media:
                        continue
                    if msg.media not in (enums.MessageMediaType.VIDEO, enums.MessageMediaType.AUDIO,
                                          enums.MessageMediaType.DOCUMENT, enums.MessageMediaType.ANIMATION):
                        continue
                    media = getattr(msg, msg.media.value, None)
                    if not media:
                        continue
                    media.file_type = msg.media.value
                    media.caption = msg.caption
                    media.file_date = msg.date
                    buffer.append(media)
                    if len(buffer) >= 200:
                        await flush()
                else:
                    await flush()
            except Exception:
                await flush()
                logger.exception("Admin-panel indexing crashed")

        if info.LOG_CHANNEL:
            try:
                await bot.send_message(
                    info.LOG_CHANNEL,
                    f"📥 Admin-panel indexing of <code>{chat_id}</code> finished.\n"
                    f"Saved: {total_saved}, Duplicates: {duplicate}, Errors: {errors}",
                )
            except Exception:
                pass

    asyncio.create_task(_run())
    return web.json_response({
        'ok': True, 'status': 'started',
        'note': 'This runs in the background — progress will be posted to LOG_CHANNEL when it finishes.',
    })


@_guarded
async def api_logs(request: web.Request):
    try:
        with open('scfilesbot.log', 'r', errors='replace') as f:
            lines = f.readlines()
        tail = ''.join(lines[-200:])
    except Exception as e:
        return web.json_response({'ok': False, 'error': str(e)}, status=500)
    return web.json_response({'ok': True, 'log': tail})


def register_admin_routes(app: web.Application, bot: Client):
    app['bot_client'] = bot  # shared with utils/frontend_api.py — harmless to set from both
    app.router.add_get('/api/admin/stats', api_stats)
    app.router.add_post('/api/admin/ban', api_ban)
    app.router.add_post('/api/admin/unban', api_unban)
    app.router.add_get('/api/admin/authlist', api_authlist)
    app.router.add_post('/api/admin/auth', api_auth)
    app.router.add_post('/api/admin/unauth', api_unauth)
    app.router.add_post('/api/admin/broadcast', api_broadcast)
    app.router.add_get('/api/admin/reindex_check', api_reindex_check)
    app.router.add_post('/api/admin/index', api_index)
    app.router.add_get('/api/admin/logs', api_logs)
