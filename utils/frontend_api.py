"""
A minimal HTTP API, alongside the existing health-check endpoint, that the
separate Vercel frontend calls during the cookie-continuity verification
flow (see database/verifydb.py for the full explanation). Every request
must include the shared secret in X-API-Secret — this is a public URL
(Koyeb/wherever the bot is hosted), so anyone could otherwise call these
endpoints and fake a verification.
"""

import logging

from aiohttp import web
from pyrogram import Client

import info
from database import verifydb

logger = logging.getLogger(__name__)


def _check_secret(request: web.Request) -> bool:
    if not info.FRONTEND_API_SECRET:
        return False  # refuse everything if no secret is configured — never run this open
    return request.headers.get('X-API-Secret') == info.FRONTEND_API_SECRET


async def api_set_cookie(request: web.Request):
    if not _check_secret(request):
        return web.json_response({'ok': False, 'error': 'unauthorized'}, status=401)
    try:
        data = await request.json()
        session_id = str(data['session_id'])
        cookie = str(data['cookie'])
        ip = data.get('ip')
    except Exception:
        return web.json_response({'ok': False, 'error': 'bad request'}, status=400)

    ok = await verifydb.set_session_cookie(session_id, cookie, ip)
    return web.json_response({'ok': ok})


async def api_confirm(request: web.Request):
    if not _check_secret(request):
        return web.json_response({'ok': False, 'error': 'unauthorized'}, status=401)
    try:
        data = await request.json()
        session_id = str(data['session_id'])
        cookie = data.get('cookie')  # may legitimately be missing/None — that's a mismatch, not a bad request
        ip = data.get('ip')
    except Exception:
        return web.json_response({'ok': False, 'error': 'bad request'}, status=400)

    status, user_id = await verifydb.confirm_session(session_id, cookie, ip)

    if status in ("ok", "ok_flagged"):
        if status == "ok_flagged" and info.LOG_CHANNEL:
            bot: Client = request.app.get('bot_client')
            if bot:
                try:
                    await bot.send_message(
                        info.LOG_CHANNEL,
                        f"⚠️ Verification for user <code>{user_id}</code> succeeded but its IP looked "
                        "inconsistent between steps — allowed (cookie matched), flagged for visibility.",
                    )
                except Exception:
                    pass
        return web.json_response({'ok': True})

    if status == "mismatch" and user_id:
        bot: Client = request.app.get('bot_client')
        if bot:
            try:
                await bot.send_message(
                    user_id,
                    "⚠️ <b>Bypass attempt detected</b>\n\n"
                    "Your verification link was opened without completing the actual shortlink step, "
                    "so it's been rejected and flagged. Please tap the file button again and complete "
                    "verification normally this time.",
                )
            except Exception as e:
                logger.warning(f"Could not notify user {user_id} of bypass attempt: {e}")

    return web.json_response({'ok': False, 'reason': status})


def register_routes(app: web.Application, bot: Client):
    app['bot_client'] = bot
    app.router.add_post('/api/verify/set-cookie', api_set_cookie)
    app.router.add_post('/api/verify/confirm', api_confirm)
