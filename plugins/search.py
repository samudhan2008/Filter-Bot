import io
import logging
import time

from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

import info
from database import filesdb, usersdb, backend
from plugins.group_auth import group_is_allowed
from plugins.force_sub import is_subscribed, fsub_markup
from utils import tmdb, poster, query as queryutil, texts
from utils.filesize import human_size

logger = logging.getLogger(__name__)

# token -> {"query": str, "candidates": [...], "ts": float}
_PENDING = {}
_PENDING_TTL = 600


def _new_token(chat_id, msg_id):
    return f"{chat_id}_{msg_id}"


def _gc_pending():
    now = time.time()
    dead = [t for t, v in _PENDING.items() if now - v["ts"] > _PENDING_TTL]
    for t in dead:
        _PENDING.pop(t, None)


@Client.on_message(
    filters.text & ~filters.via_bot & ~filters.command(
        ['start', 'index', 'authorize', 'unauthorize', 'ban', 'unban', 'broadcast', 'stats', 'logs', 'setskip']
    ) & (filters.group | filters.private)
)
async def on_search_text(bot: Client, message: Message):
    if not message.text or message.text.startswith('/'):
        return

    user = message.from_user
    if user:
        await usersdb.add_user(user.id)
        if await usersdb.is_banned(user.id):
            return

    if message.chat.type != "private":
        await usersdb.add_group(message.chat.id, message.chat.title or "")
        if not await group_is_allowed(message.chat.id):
            return  # silently ignore unauthorized groups, no spam

    if user and not await is_subscribed(bot, user.id):
        markup = await fsub_markup(bot)
        return await message.reply("👋 Please join our channel first to search files.", reply_markup=markup)

    raw_query = message.text.strip()
    if len(raw_query) < 2:
        return

    clean_query, year = queryutil.extract_year(raw_query)

    # Quick pre-check: is there even anything in our file DB for this? Avoids
    # burning a TMDB call on totally unrelated chat messages in groups.
    files, _, total = await filesdb.get_search_results(clean_query, max_results=1)
    if total == 0:
        if message.chat.type == "private":
            await message.reply(texts.NO_FILES_FOUND.format(query=raw_query))
        return

    candidates = await tmdb.search_multi(clean_query, year=year)

    if not candidates:
        # No TMDB match at all — still deliver files, just without a poster/website link.
        return await _send_plain_results(bot, message, clean_query)

    if year is not None:
        candidates = [c for c in candidates if not c["year"] or str(year) == c["year"]] or candidates

    if len(candidates) == 1 or _is_unambiguous(candidates):
        return await _show_result(bot, message, candidates[0], clean_query)

    _gc_pending()
    token = _new_token(message.chat.id, message.id)
    _PENDING[token] = {"query": clean_query, "candidates": candidates[:8], "ts": time.time()}

    buttons = []
    for i, c in enumerate(candidates[:8]):
        label = f"{c['title']} ({c['year'] or '—'}) · {c['language'].upper()}"
        buttons.append([InlineKeyboardButton(label, callback_data=f"pick|{token}|{i}")])
    await message.reply(
        texts.DISAMBIGUATION_PROMPT.format(query=raw_query),
        reply_markup=InlineKeyboardMarkup(buttons),
    )


def _is_unambiguous(candidates):
    """Per the agreed UX: only skip the picker when there's truly one option
    left (e.g. after a year filter narrowed it down)."""
    return len(candidates) <= 1


@Client.on_callback_query(filters.regex(r'^pick\|'))
async def on_pick_candidate(bot: Client, cq: CallbackQuery):
    _, token, idx = cq.data.split('|', 2)
    pending = _PENDING.get(token)
    if not pending:
        return await cq.answer("This search expired, please search again.", show_alert=True)
    try:
        candidate = pending["candidates"][int(idx)]
    except (IndexError, ValueError):
        return await cq.answer("Invalid selection.", show_alert=True)

    await cq.answer("Fetching…")
    await cq.message.delete()
    await _show_result(bot, cq.message, candidate, pending["query"], reply_chat=cq.message.chat.id, user_msg=None)


async def _send_plain_results(bot: Client, message: Message, clean_query: str):
    files, _, total = await filesdb.get_search_results(clean_query, max_results=info.MAX_RESULTS)
    if not files:
        return await message.reply(texts.NO_FILES_FOUND.format(query=clean_query))
    buttons = [
        [InlineKeyboardButton(
            texts.FILE_BUTTON_LABEL.format(name=f['file_name'][:45], size=human_size(f['file_size'])),
            url=f"https://t.me/{bot.username}?start=file_{f['file_id']}",
        )]
        for f in files
    ]
    await message.reply(
        f"📦 Found <b>{total}</b> file(s) for <b>{clean_query}</b>:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def _show_result(bot: Client, message: Message, candidate: dict, clean_query: str, reply_chat=None, user_msg=None):
    chat_id = reply_chat or message.chat.id
    kind = candidate["kind"]  # 'movie' or 'series'
    title = candidate["title"]

    files, _, total = await filesdb.get_search_results(title, max_results=info.MAX_RESULTS)
    if not files:
        files, _, total = await filesdb.get_search_results(clean_query, max_results=info.MAX_RESULTS)
    if not files:
        return await bot.send_message(chat_id, texts.NO_FILES_FOUND.format(query=title))

    logo_url = await tmdb.get_logo_url(candidate["tmdb_id"], kind)
    poster_bytes = await poster.build_poster(title, candidate.get("backdrop_path"), logo_url)

    entry = await backend.find_entry(kind, candidate["tmdb_id"], title)
    website_part = ""
    if entry:
        link = backend.website_link(kind, entry)
        from utils.shortlink import shorten
        link = await shorten(link)
        website_part = texts.WEBSITE_LINE.format(link=link)
    else:
        await _notify_admin_missing(bot, candidate)

    caption = texts.RESULT_CAPTION.format(
        title=title,
        year_part=f"({candidate['year']})" if candidate["year"] else "",
        language=candidate["language"].upper() or "N/A",
        file_count=total,
        website_part=website_part,
    )

    buttons = [
        [InlineKeyboardButton(
            texts.FILE_BUTTON_LABEL.format(name=f['file_name'][:45], size=human_size(f['file_size'])),
            url=f"https://t.me/{bot.username}?start=file_{f['file_id']}",
        )]
        for f in files
    ]
    if entry:
        buttons.append([InlineKeyboardButton("🌐 Watch on SC Files", url=backend.website_link(kind, entry))])

    photo_buf = io.BytesIO(poster_bytes)
    photo_buf.name = "poster.png"
    await bot.send_photo(
        chat_id,
        photo=photo_buf,
        caption=caption,
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def _notify_admin_missing(bot: Client, candidate: dict):
    if not info.LOG_CHANNEL:
        return
    try:
        await bot.send_message(
            info.LOG_CHANNEL,
            texts.ADMIN_NOT_ON_WEBSITE.format(
                title=candidate["title"], year=candidate["year"] or "?",
                kind=candidate["kind"], tmdb_id=candidate["tmdb_id"],
            ),
        )
    except Exception as e:
        logger.warning(f"Could not notify admin: {e}")
