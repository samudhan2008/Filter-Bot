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

Every mutating action is logged to database/auditdb.py.
"""

import asyncio
import logging
import os
import re
import resource
import time as _time
from datetime import datetime, timezone

from aiohttp import web
from pyrogram import Client, enums

import info
from database import usersdb, filesdb, backend, verifydb, auditdb, settingsdb
from utils.clients import worker_count

logger = logging.getLogger(__name__)

LINK_RE = re.compile(r"(https://)?(t\.me/|telegram\.me/|telegram\.dog/)(c/)?(\d+|[a-zA-Z_0-9]+)/(\d+)$")

_start_time = _time.time()


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


# ---------------------------------------------------------------- stats ----

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

    verified_count = await verifydb.verified_col.count_documents(
        {'verified_until': {'$gt': datetime.now(timezone.utc)}}
    )

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
        'shortlink_mode': await settingsdb.is_shortlink_mode(),
        'currently_verified': verified_count,
    })


@_guarded
async def api_health(request: web.Request):
    from utils.tmdb import _breaker as tmdb_breaker
    from database.backend import _breaker as backend_breaker

    bot: Client = request.app.get('bot_client')
    mongo_ok = True
    try:
        await filesdb.Media.collection.database.client.admin.command('ping')
    except Exception:
        mongo_ok = False

    mem_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss  # peak RSS, KB on Linux
    uptime_sec = int(_time.time() - (bot.uptime if bot and getattr(bot, 'uptime', None) else _start_time))

    return web.json_response({
        'ok': True,
        'bot_online': bool(bot),
        'uptime_sec': uptime_sec,
        'mongo_ok': mongo_ok,
        'memory_mb': round(mem_kb / 1024, 1),
        'tmdb_circuit_open': tmdb_breaker.is_open,
        'backend_circuit_open': backend_breaker.is_open,
        'worker_bots': worker_count(),
        'poster_channel_configured': bool(info.POSTER_CHANNEL),
        'shortlink_verify_configured': bool(await settingsdb.is_shortlink_mode() and info.FRONTEND_URL and info.FRONTEND_API_SECRET),
    })


@_guarded
async def api_poster_cache_stats(request: web.Request):
    directory = info.POSTER_CACHE_DIR
    count = 0
    total_bytes = 0
    try:
        if os.path.isdir(directory):
            for name in os.listdir(directory):
                path = os.path.join(directory, name)
                if os.path.isfile(path):
                    count += 1
                    total_bytes += os.path.getsize(path)
    except Exception as e:
        return web.json_response({'ok': False, 'error': str(e)}, status=500)

    from database import postersdb
    archived_count = await postersdb.posters_col.count_documents({})
    incomplete_count = await postersdb.posters_col.count_documents({
        '$or': [{'has_backdrop': False}, {'has_logo': False}]
    })
    unverified_count = await postersdb.posters_col.count_documents({
        'has_backdrop': {'$exists': False}
    })

    return web.json_response({
        'ok': True,
        'disk_cached_files': count,
        'disk_cache_mb': round(total_bytes / (1024 * 1024), 2),
        'archived_in_channel': archived_count,
        'known_incomplete': incomplete_count,
        'unverified_pre_upgrade': unverified_count,
    })


# ---------------------------------------------------------------- users ----

@_guarded
async def api_ban(request: web.Request):
    data = await request.json()
    user_id = int(data['user_id'])
    reason = data.get('reason', '')
    await usersdb.ban_user(user_id, reason)
    await auditdb.log_action('ban', {'user_id': user_id, 'reason': reason})
    return web.json_response({'ok': True})


@_guarded
async def api_unban(request: web.Request):
    data = await request.json()
    user_id = int(data['user_id'])
    await usersdb.unban_user(user_id)
    await auditdb.log_action('unban', {'user_id': user_id})
    return web.json_response({'ok': True})


@_guarded
async def api_user_lookup(request: web.Request):
    user_id = request.query.get('user_id')
    if not user_id or not user_id.lstrip('-').isdigit():
        return web.json_response({'ok': False, 'error': 'user_id query param required'}, status=400)
    user_id = int(user_id)

    is_known = await usersdb.is_user_known(user_id)
    is_banned = await usersdb.is_banned(user_id)
    is_verified = await verifydb.is_verified(user_id)

    verified_doc = await verifydb.verified_col.find_one({'_id': user_id})
    verified_until = verified_doc['verified_until'].isoformat() if verified_doc and verified_doc.get('verified_until') else None

    return web.json_response({
        'ok': True,
        'user_id': user_id,
        'known': is_known,
        'banned': is_banned,
        'verified': is_verified,
        'verified_until': verified_until,
    })


# --------------------------------------------------------------- groups ----

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
    await auditdb.log_action('auth', {'group_id': group_id})
    return web.json_response({'ok': True})


@_guarded
async def api_unauth(request: web.Request):
    data = await request.json()
    group_id = int(data['group_id'])
    await usersdb.set_group_authorized(group_id, False)
    await auditdb.log_action('unauth', {'group_id': group_id})
    return web.json_response({'ok': True})


# ------------------------------------------------------------ broadcast ----

@_guarded
async def api_broadcast(request: web.Request):
    data = await request.json()
    target = data.get('target', 'users')
    text = (data.get('text') or '').strip()
    if not text:
        return web.json_response({'ok': False, 'error': 'text is required'}, status=400)

    bot: Client = request.app['bot_client']
    ids = await usersdb.all_group_ids() if target == 'groups' else await usersdb.all_user_ids()
    await auditdb.log_action('broadcast', {'target': target, 'recipient_count': len(ids), 'text_preview': text[:120]})

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


# --------------------------------------------------------------- backend ----

@_guarded
async def api_backend_refresh(request: web.Request):
    movies, series = await backend.force_refresh()
    await auditdb.log_action('backend_refresh', {'movies': len(movies), 'series': len(series)})
    return web.json_response({'ok': True, 'movies': len(movies), 'series': len(series)})


@_guarded
async def api_shortlink_toggle(request: web.Request):
    data = await request.json()
    enabled = bool(data.get('enabled'))
    await settingsdb.set_setting('shortlink_mode', enabled)
    await auditdb.log_action('shortlink_toggle', {'enabled': enabled})
    return web.json_response({'ok': True, 'shortlink_mode': enabled})


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


# ----------------------------------------------------------------- files ----

@_guarded
async def api_search_files(request: web.Request):
    query = request.query.get('q', '').strip()
    if not query:
        return web.json_response({'ok': False, 'error': 'q query param required'}, status=400)
    files, _, total = await filesdb.get_search_results(query, max_results=25)
    return web.json_response({
        'ok': True,
        'total': total,
        'results': [
            {'name': f['file_name'], 'size': f['file_size'], 'type': f.get('file_type'),
             'season': f.get('season_number'), 'episode': f.get('ep_number')}
            for f in files
        ],
    })


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

    await auditdb.log_action('index_start', {'chat_id': chat_id, 'last_msg_id': last_msg_id})

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

        await auditdb.log_action('index_finish', {'chat_id': chat_id, 'saved': total_saved,
                                                    'duplicate': duplicate, 'errors': errors})
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


@_guarded
async def api_audit_log(request: web.Request):
    entries = await auditdb.recent(50)
    return web.json_response({'ok': True, 'entries': entries})


def register_admin_routes(app: web.Application, bot: Client):
    app['bot_client'] = bot  # shared with utils/frontend_api.py — harmless to set from both
    app.router.add_get('/api/admin/stats', api_stats)
    app.router.add_get('/api/admin/health', api_health)
    app.router.add_get('/api/admin/poster_cache', api_poster_cache_stats)
    app.router.add_post('/api/admin/ban', api_ban)
    app.router.add_post('/api/admin/unban', api_unban)
    app.router.add_get('/api/admin/user', api_user_lookup)
    app.router.add_get('/api/admin/authlist', api_authlist)
    app.router.add_post('/api/admin/auth', api_auth)
    app.router.add_post('/api/admin/unauth', api_unauth)
    app.router.add_post('/api/admin/broadcast', api_broadcast)
    app.router.add_post('/api/admin/backend_refresh', api_backend_refresh)
    app.router.add_post('/api/admin/shortlink_toggle', api_shortlink_toggle)
    app.router.add_get('/api/admin/reindex_check', api_reindex_check)
    app.router.add_get('/api/admin/search_files', api_search_files)
    app.router.add_post('/api/admin/index', api_index)
    app.router.add_get('/api/admin/logs', api_logs)
    app.router.add_get('/api/admin/audit_log', api_audit_log)
