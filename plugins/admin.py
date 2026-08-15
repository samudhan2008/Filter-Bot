import asyncio
import logging

from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.errors import FloodWait

import info
from database import usersdb, filesdb, backend, settingsdb
from utils.clients import get_sender, worker_count

logger = logging.getLogger(__name__)


def _pm_toggle_markup(pm_on: bool) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(f"{'✅' if pm_on else '⬜️'} Turn ON", callback_data="pm_search|on"),
        InlineKeyboardButton(f"{'✅' if not pm_on else '⬜️'} Turn OFF", callback_data="pm_search|off"),
    ]])


@Client.on_message(filters.command('pm') & filters.user(info.ADMINS))
async def pm_cmd(bot: Client, message: Message):
    pm_on = await settingsdb.is_pm_search_mode()
    await message.reply(
        f"💬 <b>PM Search Mode</b>\n\n"
        f"Currently: <b>{'ON' if pm_on else 'OFF'}</b>\n\n"
        f"When off, users searching in the bot's PM get redirected to "
        f"<b>{info.PM_SEARCH_REDIRECT_CHANNEL}</b> instead of getting results.\n"
        f"Group search is never affected by this.",
        reply_markup=_pm_toggle_markup(pm_on),
    )


@Client.on_callback_query(filters.regex(r'^pm_search\|(on|off)$') & filters.user(info.ADMINS))
async def pm_search_toggle_cq(bot: Client, cq: CallbackQuery):
    enabled = cq.data.split('|', 1)[1] == 'on'
    await settingsdb.set_setting('pm_search_mode', enabled)
    await cq.answer(f"PM search turned {'ON' if enabled else 'OFF'}.")
    await cq.message.edit_text(
        f"💬 <b>PM Search Mode</b>\n\n"
        f"Currently: <b>{'ON' if enabled else 'OFF'}</b>\n\n"
        f"When off, users searching in the bot's PM get redirected to "
        f"<b>{info.PM_SEARCH_REDIRECT_CHANNEL}</b> instead of getting results.\n"
        f"Group search is never affected by this.",
        reply_markup=_pm_toggle_markup(enabled),
    )


@Client.on_message(filters.command('ban') & filters.user(info.ADMINS))
async def ban_cmd(bot: Client, message: Message):
    parts = message.text.split(maxsplit=2)
    target = None
    if message.reply_to_message:
        target = message.reply_to_message.from_user.id
        reason = parts[1] if len(parts) > 1 else ""
    elif len(parts) > 1 and parts[1].lstrip('-').isdigit():
        target = int(parts[1])
        reason = parts[2] if len(parts) > 2 else ""
    else:
        return await message.reply("Usage: /ban &lt;user_id&gt; [reason]  (or reply to a user's message)")
    await usersdb.ban_user(target, reason)
    await message.reply(f"🚫 Banned <code>{target}</code>.{f' Reason: {reason}' if reason else ''}")


@Client.on_message(filters.command('unban') & filters.user(info.ADMINS))
async def unban_cmd(bot: Client, message: Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].lstrip('-').isdigit():
        return await message.reply("Usage: /unban &lt;user_id&gt;")
    await usersdb.unban_user(int(parts[1]))
    await message.reply(f"✅ Unbanned <code>{parts[1]}</code>.")


@Client.on_message(filters.command('stats') & filters.user(info.ADMINS))
async def stats_cmd(bot: Client, message: Message):
    users = await usersdb.total_users_count()
    groups = await usersdb.total_groups_count()
    coll = filesdb.Media.collection
    files_total = await coll.count_documents({})
    movies_files = await coll.count_documents({'season_number': None})
    series_files = await coll.count_documents({'season_number': {'$ne': None}})

    import time as _time
    from database.backend import _cache as backend_cache
    now = _time.time()
    movies_age = int(now - backend_cache["ts"]["movies"]) if backend_cache["ts"]["movies"] else None
    series_age = int(now - backend_cache["ts"]["series"]) if backend_cache["ts"]["series"] else None

    await message.reply(
        "📊 <b>SC Files Bot Stats</b>\n\n"
        f"👤 Users: <code>{users}</code>\n"
        f"👥 Groups: <code>{groups}</code>\n"
        f"🎞 Indexed files: <code>{files_total}</code>\n"
        f"　 ├ Movie files (no season tag): <code>{movies_files}</code>\n"
        f"　 └ Series/episode files: <code>{series_files}</code>\n"
        f"🌐 Backend cache age — movies: <code>{movies_age if movies_age is not None else 'never fetched'}s</code>, "
        f"series: <code>{series_age if series_age is not None else 'never fetched'}s</code>\n"
        f"👷 Worker bots active: <code>{worker_count()}</code>\n"
        f"🔎 Search mode: <code>{'Mongo $text' if info.USE_MONGO_TEXT_SEARCH else 'regex'}</code>"
    )


@Client.on_message(filters.command('logs') & filters.user(info.ADMINS))
async def logs_cmd(bot: Client, message: Message):
    try:
        await message.reply_document('scfilesbot.log', caption="📄 Latest logs")
    except Exception as e:
        await message.reply(f"❌ Couldn't fetch logs: {e}")


@Client.on_message(filters.command('broadcast') & filters.user(info.ADMINS))
async def broadcast_cmd(bot: Client, message: Message):
    if not message.reply_to_message:
        return await message.reply("Reply to the message you want to broadcast with /broadcast.\n"
                                    "Add `groups` after the command to broadcast to groups instead of users.")
    to_groups = 'groups' in message.text.lower()
    ids = await usersdb.all_group_ids() if to_groups else await usersdb.all_user_ids()

    status = await message.reply(
        f"📢 Broadcasting to {len(ids)} {'groups' if to_groups else 'users'}"
        f"{f' across {worker_count() + 1} bot clients' if worker_count() else ''}…"
    )
    sent = failed = 0
    for cid in ids:
        sender = get_sender(bot)  # round-robins across worker bots if configured
        try:
            await message.reply_to_message.copy(cid)
            sent += 1
        except FloodWait as e:
            await asyncio.sleep(e.value)
            try:
                await message.reply_to_message.copy(cid)
                sent += 1
            except Exception:
                failed += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.03 if worker_count() else 0.05)

    await status.edit(f"✅ Broadcast done.\nSent: <code>{sent}</code>\nFailed: <code>{failed}</code>")


@Client.on_message(filters.command('reindex_check') & filters.user(info.ADMINS))
async def reindex_check_cmd(bot: Client, message: Message):
    """
    Reconciliation job: walks distinct titles we have Telegram files for and
    flags ones that don't appear to exist on the SC Files website at all —
    catching drift in bulk instead of only when a user happens to search for
    it. This is a heuristic pass (title text only, no TMDB calls) meant to
    surface candidates for a human to check, not a definitive answer.
    """
    status = await message.reply("🔄 Refreshing backend data and scanning indexed titles…")
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
        if len(missing) >= 50:  # cap the report
            break

    if not missing:
        return await status.edit("✅ No obvious gaps found between indexed titles and the website (checked "
                                  f"{len(distinct_titles)} distinct titles).")

    report = "\n".join(f"• {t}" for t in missing[:50])
    await status.edit(
        f"⚠️ <b>{len(missing)}+ titles</b> indexed in Telegram don't obviously match anything on the website "
        f"(text heuristic — please verify manually, this doesn't use TMDB IDs):\n\n{report}"
    )
