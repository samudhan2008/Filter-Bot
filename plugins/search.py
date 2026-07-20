import io
import logging
import re
import time

from pyrogram import Client, filters
from pyrogram.enums import ChatType
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

_QUALITY_RE = re.compile(r'\b(2160p|4k|1440p|1080p|720p|480p|360p)\b', re.IGNORECASE)


def _new_token(chat_id, msg_id):
    return f"{chat_id}_{msg_id}"


def _gc_pending():
    now = time.time()
    dead = [t for t, v in _PENDING.items() if now - v["ts"] > _PENDING_TTL]
    for t in dead:
        _PENDING.pop(t, None)


def _quality_of(filename: str) -> str:
    m = _QUALITY_RE.search(filename)
    return m.group(1).upper() if m else "Other"


def _group_by_quality(files):
    """Groups file results by resolution so a series with many episodes
    doesn't turn into a wall of individual buttons — one row per quality,
    still linking to the same per-file deep links underneath."""
    groups = {}
    for f in files:
        q = _quality_of(f['file_name'])
        groups.setdefault(q, []).append(f)
    # sort qualities high-to-low, "Other" last
    order = ["2160P", "4K", "1440P", "1080P", "720P", "480P", "360P"]
    def key(q):
        return order.index(q) if q in order else len(order)
    return dict(sorted(groups.items(), key=lambda kv: key(kv[0])))


@Client.on_message(
    filters.text & ~filters.via_bot & ~filters.command(
        ['start', 'index', 'auth', 'unauth', 'authlist', 'ban', 'unban', 'broadcast', 'stats', 'logs',
         'setskip', 'reindex_check']
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

    if message.chat.type != ChatType.PRIVATE:
        await usersdb.add_group(message.chat.id, message.chat.title or "")
        if not await group_is_allowed(message.chat.id):
            return  # silently ignore unauthorized groups, no spam

    if user and not await is_subscribed(bot, user.id):
        markup = await fsub_markup(bot)
        return await message.reply("👋 Please join our channel first to search files.", reply_markup=markup)

    raw_query = message.text.strip()
    if len(raw_query) < 2:
        return

    # Order matters: pull season/episode out first (so "S01E02" isn't
    # mistaken for a year), then pull the year out of what's left.
    text_no_ep, season, episode = queryutil.extract_episode(raw_query)
    clean_query, year = queryutil.extract_year(text_no_ep)

    files, _, total = await filesdb.get_search_results(clean_query, max_results=1, season=season, episode=episode)
    if total == 0:
        suggestions = await filesdb.get_suggestions(clean_query)
        if suggestions:
            sugg_lines = "\n".join(f"• {s}" for s in suggestions)
            await message.reply(
                texts.NO_FILES_FOUND.format(query=raw_query) + f"\n\n🤔 Did you mean:\n{sugg_lines}"
            )
        else:
            await message.reply(texts.NO_FILES_FOUND.format(query=raw_query))
        return

    candidates = await tmdb.search_multi(clean_query, year=year)

    if not candidates:
        return await _send_plain_results(bot, message, clean_query, season, episode)

    if year is not None:
        candidates = [c for c in candidates if not c["year"] or str(year) == c["year"]] or candidates

    if len(candidates) == 1 or _is_unambiguous(candidates):
        return await _show_result(bot, message, candidates[0], clean_query, season, episode)

    _gc_pending()
    token = _new_token(message.chat.id, message.id)
    _PENDING[token] = {"query": clean_query, "candidates": candidates[:8], "season": season,
                        "episode": episode, "ts": time.time()}

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
    await _show_result(
        bot, cq.message, candidate, pending["query"], pending.get("season"), pending.get("episode"),
        reply_chat=cq.message.chat.id,
    )


async def _send_plain_results(bot: Client, message: Message, clean_query: str, season=None, episode=None):
    files, _, total = await filesdb.get_search_results(
        clean_query, max_results=info.MAX_RESULTS, season=season, episode=episode
    )
    if not files:
        return await message.reply(texts.NO_FILES_FOUND.format(query=clean_query))
    buttons = _file_buttons(bot, files)
    await message.reply(
        f"📦 Found <b>{total}</b> file(s) for <b>{clean_query}</b>:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


def _file_buttons(bot: Client, files):
    """One row per quality bucket when there are enough files that a flat
    per-file list would be unwieldy (e.g. a whole series); otherwise one
    row per file as before."""
    if len(files) <= 6:
        return [
            [InlineKeyboardButton(
                texts.FILE_BUTTON_LABEL.format(name=f['file_name'][:45], size=human_size(f['file_size'])),
                url=f"https://t.me/{bot.username}?start=file_{f['file_id']}",
            )]
            for f in files
        ]

    groups = _group_by_quality(files)
    buttons = []
    for quality, group_files in groups.items():
        # Telegram deep-link start payloads are capped at 64 chars, so we
        # can only carry one file per link — pick the most recent file in
        # that quality bucket and label the button with the count.
        f = group_files[0]
        label = f"🎞 {quality} ({len(group_files)} file{'s' if len(group_files) > 1 else ''})"
        buttons.append([InlineKeyboardButton(label, url=f"https://t.me/{bot.username}?start=file_{f['file_id']}")])
    return buttons


async def _show_result(bot: Client, message: Message, candidate: dict, clean_query: str,
                        season=None, episode=None, reply_chat=None):
    chat_id = reply_chat or message.chat.id
    kind = candidate["kind"]  # 'movie' or 'series'
    title = candidate["title"]

    files, _, total = await filesdb.get_search_results(title, max_results=info.MAX_RESULTS, season=season, episode=episode)
    if not files:
        files, _, total = await filesdb.get_search_results(clean_query, max_results=info.MAX_RESULTS, season=season, episode=episode)
    if not files:
        return await bot.send_message(chat_id, texts.NO_FILES_FOUND.format(query=title))

    logo_url = await tmdb.get_logo_url(candidate["tmdb_id"], kind)
    poster_bytes = await poster.build_poster(
        title, candidate.get("backdrop_path"), logo_url, tmdb_id=candidate["tmdb_id"], kind=kind
    )

    entry = await backend.find_entry(kind, candidate["tmdb_id"], title)
    website_part = ""
    if entry:
        link = backend.website_link(kind, entry)
        from utils.shortlink import shorten
        link = await shorten(link)
        website_part = texts.WEBSITE_LINE.format(link=link)
    else:
        await _notify_admin_missing(bot, candidate)

    ep_note = ""
    if season is not None:
        ep_note = f"\n📺 Season {season}" + (f", Episode {episode}" if episode else "")

    caption = texts.RESULT_CAPTION.format(
        title=title,
        year_part=f"({candidate['year']})" if candidate["year"] else "",
        language=candidate["language"].upper() or "N/A",
        file_count=total,
        website_part=website_part,
    ) + ep_note

    buttons = _file_buttons(bot, files)
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
