import asyncio
import io
import logging
import math
import uuid

from pyrogram import Client, filters
from pyrogram.enums import ChatType
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

import info
from database import filesdb, usersdb, backend, statedb, verifydb
from plugins.group_auth import group_is_allowed, _is_group_admin
from plugins.force_sub import is_subscribed, fsub_markup
from utils import tmdb, poster, query as queryutil, texts
from utils.filesize import human_size

logger = logging.getLogger(__name__)

def _new_token(chat_id, msg_id):
    return f"{chat_id}_{msg_id}"


async def _new_result_entry(files, query, season, episode, offset, max_results, total, extra_buttons=None):
    """Stores one page of results + enough search context to fetch the next
    one, in Mongo (survives restarts). Re-used (same token) across
    pagination clicks on the same message — we overwrite in place and edit
    the message's buttons, rather than sending a new message each page."""
    token = uuid.uuid4().hex[:12]
    ctx = {
        "query": query, "season": season, "episode": episode,
        "offset": offset, "max_results": max_results, "total": total,
        "extra_buttons": extra_buttons or [],
    }
    await statedb.store_results(token, files, ctx)
    return token, ctx


async def _file_button_rows(bot: Client, files):
    """One button per file, always a plain t.me/<bot>?start=file_<id> deep
    link — never shortened here. Being a real Telegram deep link, it
    always opens the clicker's own PM regardless of where the button was
    pressed, and always maps to exactly one file, so there's no ambiguity
    about which file a tap delivers.

    The shortlink (when SHORTLINK_MODE is on) belongs *only* to the
    one-time verification step in plugins/start.py's deliver_file — once a
    user is verified, every file link works directly for the rest of
    their verification window. Shortening every individual file link here
    would put a shortlink between the user and every single file, every
    time, defeating the whole point of the time-window verification model.
    """
    rows = []
    for f in files:
        deep_link = f"https://t.me/{bot.username}?start=file_{f['file_id']}"
        rows.append([InlineKeyboardButton(
            texts.FILE_BUTTON_LABEL.format(name=f['file_name'][:55], size=human_size(f['file_size'])),
            url=deep_link,
        )])
    return rows


def _pagination_row(token, offset, max_results, total):
    if total <= max_results:
        return []
    pages = max(1, math.ceil(total / max_results))
    current_page = offset // max_results + 1
    row = []
    if offset > 0:
        row.append(InlineKeyboardButton("◀️ Prev", callback_data=f"pg|{token}|prev"))
    row.append(InlineKeyboardButton(f"📄 {current_page}/{pages}", callback_data="noop"))
    if offset + max_results < total:
        row.append(InlineKeyboardButton("Next ▶️", callback_data=f"pg|{token}|next"))
    return [row]


async def _build_markup(bot: Client, token, files, ctx):
    rows = await _file_button_rows(bot, files)
    for row in ctx.get("extra_buttons", []):
        rows.append([InlineKeyboardButton(b["text"], url=b["url"]) for b in row])
    rows.extend(_pagination_row(token, ctx["offset"], ctx["max_results"], ctx["total"]))
    rows.append([InlineKeyboardButton("✖️ Close", callback_data="close")])
    return InlineKeyboardMarkup(rows)


@Client.on_callback_query(filters.regex(r'^close$'))
async def on_close_result(bot: Client, cq: CallbackQuery):
    """Deletes the result message — but only for a bot admin, or (in a
    group) that group's own admins/creator. Anyone else tapping it just
    gets told no, the message stays."""
    is_allowed = cq.from_user.id in info.ADMINS
    if not is_allowed and cq.message.chat.type != ChatType.PRIVATE:
        is_allowed = await _is_group_admin(bot, cq.message.chat.id, cq.from_user.id)
    if not is_allowed:
        return await cq.answer("Only an admin can close this.", show_alert=True)
    try:
        await cq.message.delete()
    except Exception as e:
        logger.warning(f"Could not delete message on close: {e}")
        return await cq.answer("Couldn't delete — I may be missing delete permission here.", show_alert=True)
    await cq.answer()


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

    # In PM, don't even run the search for a non-verified user when
    # SHORTLINK_MODE is on — verify first, search after. This is
    # deliberately PM-only: gating a *group* search on whoever happened to
    # type the query would block every other (possibly already-verified)
    # member from seeing results too, which isn't the intent. In groups,
    # search stays open for everyone, and verification is still enforced
    # per-user at file-delivery time when they tap a file's deep link.
    if (
        message.chat.type == ChatType.PRIVATE
        and info.SHORTLINK_MODE
        and info.FRONTEND_URL
        and info.FRONTEND_API_SECRET
        and user
        and not await verifydb.is_verified(user.id)
    ):
        from plugins.start import send_verify_prompt
        return await send_verify_prompt(bot, message)

    raw_query = message.text.strip()
    if len(raw_query) < 2:
        return

    # Order matters: pull season/episode out first (so "S01E02" isn't
    # mistaken for a year), then pull the year out of what's left.
    text_no_ep, season, episode = queryutil.extract_episode(raw_query)
    clean_query, year = queryutil.extract_year(text_no_ep)

    files, _, total = await filesdb.get_search_results(
        clean_query, max_results=info.MAX_RESULTS, offset=0, season=season, episode=episode
    )
    if total == 0:
        suggestions = await filesdb.get_suggestions(clean_query)
        if suggestions:
            sugg_lines = "\n".join(f"• {s}" for s in suggestions)
            await message.reply(
                texts.not_found(raw_query) + f"\n\n🤔 Did you mean:\n{sugg_lines}"
            )
        else:
            await message.reply(texts.not_found(raw_query))
        return

    cancel_token = uuid.uuid4().hex[:10]
    status_msg = await message.reply(
        "🔎 Checking TMDB for a match…",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data=f"cancelsearch|{cancel_token}")]]),
    )

    candidates = await tmdb.search_multi(clean_query, year=year)

    if await statedb.pop_cancelled(cancel_token):
        return  # user cancelled — on_cancel_search already updated the status message

    if not candidates:
        try:
            await status_msg.delete()
        except Exception:
            pass
        # Already have these files from the check above — no need to
        # search again for the same query/season/episode.
        return await _send_plain_results(bot, message, clean_query, season, episode, prefetched=(files, total))

    if year is not None:
        candidates = [c for c in candidates if not c["year"] or str(year) == c["year"]] or candidates

    if len(candidates) == 1 or _is_unambiguous(candidates):
        # status_msg is handed off here — _route_to_result either deletes it
        # (about to show a picker) or turns it into a "please wait" message
        # and clears it right before the actual result goes out.
        return await _route_to_result(bot, message, candidates[0], clean_query, season, episode, status_msg=status_msg)

    try:
        await status_msg.delete()
    except Exception:
        pass

    token = _new_token(message.chat.id, message.id)
    await statedb.store_pending(token, {
        "query": clean_query, "candidates": candidates[:8], "season": season, "episode": episode,
    })

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


@Client.on_callback_query(filters.regex(r'^cancelsearch\|'))
async def on_cancel_search(bot: Client, cq: CallbackQuery):
    """Anyone can cancel a search in progress — this isn't gated to the
    original requester or to admins, unlike the Close button below."""
    _, token = cq.data.split('|', 1)
    await statedb.set_cancelled(token)
    try:
        await cq.message.edit_text("❌ Search cancelled.")
    except Exception:
        pass
    await cq.answer("Cancelled.")


@Client.on_callback_query(filters.regex(r'^pick\|'))
async def on_pick_candidate(bot: Client, cq: CallbackQuery):
    _, token, idx = cq.data.split('|', 2)
    pending = await statedb.get_pending(token)
    if not pending:
        return await cq.answer("This search expired, please search again.", show_alert=True)
    try:
        candidate = pending["candidates"][int(idx)]
    except (IndexError, ValueError):
        return await cq.answer("Invalid selection.", show_alert=True)

    await cq.answer(texts.fetching_toast())
    await cq.message.delete()
    await _route_to_result(
        bot, cq.message, candidate, pending["query"], pending.get("season"), pending.get("episode"),
        reply_chat=cq.message.chat.id,
    )


async def _send_plain_results(bot: Client, message: Message, clean_query: str, season=None, episode=None, prefetched=None):
    max_results = info.MAX_RESULTS
    if prefetched is not None:
        files, total = prefetched
    else:
        files, _, total = await filesdb.get_search_results(
            clean_query, max_results=max_results, offset=0, season=season, episode=episode
        )
    if not files:
        return await message.reply(texts.not_found(clean_query))

    token, ctx = await _new_result_entry(files, clean_query, season, episode, 0, max_results, total)
    markup = await _build_markup(bot, token, files, ctx)
    await message.reply(
        f"📦 Found <b>{total}</b> file(s) for <b>{clean_query}</b>:",
        reply_markup=markup,
    )


@Client.on_callback_query(filters.regex(r'^pg\|'))
async def on_paginate(bot: Client, cq: CallbackQuery):
    _, token, direction = cq.data.split('|', 2)
    entry = await statedb.get_results(token)
    if not entry:
        return await cq.answer("This result has expired — please search again.", show_alert=True)

    ctx = entry["ctx"]
    max_results = ctx["max_results"]
    new_offset = ctx["offset"] + max_results if direction == "next" else max(0, ctx["offset"] - max_results)

    files, _, total = await filesdb.get_search_results(
        ctx["query"], max_results=max_results, offset=new_offset, season=ctx["season"], episode=ctx["episode"]
    )
    ctx["offset"] = new_offset
    ctx["total"] = total
    await statedb.update_results(token, files, ctx)

    try:
        await cq.message.edit_reply_markup(reply_markup=await _build_markup(bot, token, files, ctx))
    except Exception as e:
        logger.warning(f"Pagination edit failed: {e}")
    await cq.answer()


@Client.on_callback_query(filters.regex(r'^noop$'))
async def on_noop(bot: Client, cq: CallbackQuery):
    await cq.answer()


async def _route_to_result(bot: Client, message, candidate: dict, clean_query: str, season, episode,
                            reply_chat=None, status_msg=None):
    """Movies (and any series where the query already pinned down a season,
    e.g. "GOT S02E05") go straight to the result. A series with no season
    specified goes through the season → episode picker first — jumping
    straight to a single poster+file-list for a show with many seasons and
    episodes is exactly the "hard to find a specific episode" problem this
    is meant to fix.

    But that picker is only useful if the indexed files actually carry a
    recognizable season tag — plenty of real uploads don't (different
    naming convention, or none at all). Forcing a season filter onto
    content that was never tagged that way would just show "no files
    found" for every pick, so this checks the DB first and falls back to
    a flat, movie-style result if there's nothing season-tagged to browse.

    status_msg: the "checking TMDB" message from on_search_text, if any —
    deleted here if we're about to show a picker (new interactive content
    follows immediately, no need for a wait message), or handed to
    _show_result to turn into a "please wait" message and clear right
    before the actual result is sent, if we're going straight there.
    """
    chat_id = reply_chat or message.chat.id
    if candidate["kind"] == "series" and season is None:
        has_seasons = await filesdb.has_season_data(candidate["title"])
        if not has_seasons and clean_query != candidate["title"]:
            has_seasons = await filesdb.has_season_data(clean_query)
        if has_seasons:
            if status_msg:
                try:
                    await status_msg.delete()
                except Exception:
                    pass
            return await _handle_series_flow(bot, chat_id, candidate, clean_query)

    if status_msg:
        try:
            await status_msg.edit_text(texts.wait_message())
        except Exception:
            pass
    return await _show_result(bot, message, candidate, clean_query, season, episode,
                               reply_chat=reply_chat, status_msg=status_msg)


async def _handle_series_flow(bot: Client, chat_id: int, candidate: dict, clean_query: str):
    seasons = await tmdb.get_seasons(candidate["tmdb_id"])

    if len(seasons) > 1:
        token = uuid.uuid4().hex[:10]
        await statedb.store_pending(token, {"candidate": candidate, "query": clean_query})
        rows, row = [], []
        for s in seasons:
            row.append(InlineKeyboardButton(f"Season {s['season_number']}", callback_data=f"seas|{token}|{s['season_number']}"))
            if len(row) == 3:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        return await bot.send_message(
            chat_id,
            f"📺 <b>{candidate['title']}</b> has {len(seasons)} seasons — which one?",
            reply_markup=InlineKeyboardMarkup(rows),
        )

    # Only one season (or TMDB gave us nothing useful) — no point asking.
    season = seasons[0]["season_number"] if seasons else 1
    await _handle_episode_pick(bot, chat_id, candidate, clean_query, season)


@Client.on_callback_query(filters.regex(r'^seas\|'))
async def on_pick_season(bot: Client, cq: CallbackQuery):
    _, token, season_str = cq.data.split('|', 2)
    pending = await statedb.get_pending(token)
    if not pending:
        return await cq.answer("This search expired, please search again.", show_alert=True)
    await cq.answer()
    try:
        await cq.message.delete()
    except Exception:
        pass
    await _handle_episode_pick(bot, cq.message.chat.id, pending["candidate"], pending["query"], int(season_str))


async def _handle_episode_pick(bot: Client, chat_id: int, candidate: dict, clean_query: str, season: int):
    """Shows real episode-number buttons for this title+season, built from
    what's actually in the DB (checking S01E01, Season.1.EP1, S01 EP1,
    1x01, and other conventions — see utils/query.py) rather than guessing
    an episode count from TMDB."""
    title = candidate["title"]
    episodes = await filesdb.get_distinct_episodes(title, season)
    if not episodes and clean_query != title:
        episodes = await filesdb.get_distinct_episodes(clean_query, season)

    if not episodes:
        # Nothing tagged with a recognizable episode number for this season
        # — most likely a full-season pack file. Before committing to a
        # season filter, confirm there's actually at least one file tagged
        # for this season; if not (e.g. TMDB's season numbering doesn't
        # line up with how this title happens to be tagged in the DB),
        # fall back to showing everything for the title, unfiltered,
        # rather than a guaranteed-empty result.
        _, _, season_total = await filesdb.get_search_results(title, season=season, max_results=1)
        if season_total == 0 and clean_query != title:
            _, _, season_total = await filesdb.get_search_results(clean_query, season=season, max_results=1)
        if season_total == 0:
            return await _show_result(bot, None, candidate, clean_query, None, None, reply_chat=chat_id)
        return await _show_result(bot, None, candidate, clean_query, season, None, reply_chat=chat_id)

    if len(episodes) == 1:
        return await _show_result(bot, None, candidate, clean_query, season, episodes[0], reply_chat=chat_id)

    token = uuid.uuid4().hex[:10]
    await statedb.store_pending(token, {"candidate": candidate, "query": clean_query, "season": season})
    rows, row = [], []
    for ep in episodes:
        row.append(InlineKeyboardButton(f"E{ep:02d}", callback_data=f"epi|{token}|{ep}"))
        if len(row) == 5:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("📦 All episodes in this season", callback_data=f"epi|{token}|all")])
    await bot.send_message(
        chat_id,
        f"📺 <b>{title}</b> — Season {season}\n{len(episodes)} episode(s) available. Which one?",
        reply_markup=InlineKeyboardMarkup(rows),
    )


@Client.on_callback_query(filters.regex(r'^epi\|'))
async def on_pick_episode(bot: Client, cq: CallbackQuery):
    _, token, ep_str = cq.data.split('|', 2)
    pending = await statedb.get_pending(token)
    if not pending:
        return await cq.answer("This search expired, please search again.", show_alert=True)
    episode = None if ep_str == "all" else int(ep_str)
    await cq.answer(texts.fetching_toast())
    try:
        await cq.message.delete()
    except Exception:
        pass
    await _show_result(
        bot, None, pending["candidate"], pending["query"], pending["season"], episode,
        reply_chat=cq.message.chat.id,
    )


async def _show_result(bot: Client, message: Message, candidate: dict, clean_query: str,
                        season=None, episode=None, reply_chat=None, status_msg=None):
    chat_id = reply_chat or message.chat.id
    kind = candidate["kind"]  # 'movie' or 'series'
    title = candidate["title"]
    max_results = info.MAX_RESULTS

    effective_query = title
    files, _, total = await filesdb.get_search_results(title, max_results=max_results, offset=0, season=season, episode=episode)
    if not files:
        effective_query = clean_query
        files, _, total = await filesdb.get_search_results(clean_query, max_results=max_results, offset=0, season=season, episode=episode)
    if not files:
        if status_msg:
            try:
                await status_msg.delete()
            except Exception:
                pass
        return await bot.send_message(chat_id, texts.not_found(title))

    # get_logo_url, find_entry, and (for a series) the episode/season art
    # are all independent — kick them off together so their latency
    # overlaps with the (longer) poster build below instead of adding on
    # top of it.
    logo_task = asyncio.create_task(tmdb.get_logo_url(candidate["tmdb_id"], kind))
    entry_task = asyncio.create_task(backend.find_entry(kind, candidate["tmdb_id"], title))
    episode_task = None
    season_poster_task = None
    if kind == "series" and season is not None:
        if episode is not None:
            # A specific episode is picked — episodes have a real
            # landscape "still" image, so this skips the season poster's
            # blur-fill workaround entirely.
            episode_task = asyncio.create_task(tmdb.get_episode_details(candidate["tmdb_id"], season, episode))
        else:
            # "All episodes in this season" — no single episode to show a
            # still for, so we show the season's actual poster plainly.
            season_poster_task = asyncio.create_task(tmdb.get_season_poster(candidate["tmdb_id"], season))

    logo_url = await logo_task

    backdrop_path = candidate.get("backdrop_path")
    cache_suffix = ""
    episode_details = None
    poster_ref, poster_is_file_id = None, False  # set below if a full-poster path applies

    if episode_task is not None:
        episode_details = await episode_task
        if episode_details and episode_details.get("still_path"):
            backdrop_path = episode_details["still_path"]
            cache_suffix = f"_s{season}e{episode}"
        else:
            # No still for this specific episode — fall back to showing
            # the season's actual poster, plainly.
            season_poster_path = await tmdb.get_season_poster(candidate["tmdb_id"], season)
            if season_poster_path:
                poster_ref, poster_is_file_id = await poster.build_full_poster(
                    bot, season_poster_path, tmdb_id=candidate["tmdb_id"], kind=kind, cache_suffix=f"_s{season}"
                )
    elif season_poster_task is not None:
        season_poster_path = await season_poster_task
        if season_poster_path:
            poster_ref, poster_is_file_id = await poster.build_full_poster(
                bot, season_poster_path, tmdb_id=candidate["tmdb_id"], kind=kind, cache_suffix=f"_s{season}"
            )

    if poster_ref is None:
        poster_ref, poster_is_file_id = await poster.build_poster(
            bot, title, backdrop_path, logo_url, tmdb_id=candidate["tmdb_id"], kind=kind, cache_suffix=cache_suffix,
        )

    entry = await entry_task
    website_part = ""
    extra_buttons = []
    if entry:
        link = backend.website_link(kind, entry)
        from utils.shortlink import shorten
        short_link = await shorten(link)
        website_part = texts.WEBSITE_LINE.format(link=short_link)
        # Plain dicts, not InlineKeyboardButton objects — this gets stored
        # in Mongo via _new_result_entry below, and Pyrogram objects aren't
        # BSON-serializable. Storing the raw object here was silently
        # throwing inside statedb.store_results() whenever a website match
        # was found, which is why a search could get all the way through
        # generating (and archiving) the poster and then just... stop, with
        # no message and no visible error.
        extra_buttons = [[{"text": "🌐 Watch on SC Files", "url": short_link}]]
    else:
        await _notify_admin_missing(bot, candidate)

    ep_note = ""
    if season is not None:
        ep_note = f"\n📺 Season {season}" + (f", Episode {episode}" if episode else "")
        if episode_details:
            if episode_details.get("name"):
                ep_note += f" — {episode_details['name']}"
            if episode_details.get("air_date"):
                ep_note += f"\n🗓 Aired: {episode_details['air_date']}"
            overview = episode_details.get("overview")
            if overview:
                if len(overview) > 300:
                    overview = overview[:297] + "..."
                ep_note += f"\n\n{overview}"

    caption = texts.RESULT_CAPTION.format(
        title=title,
        year_part=f"({candidate['year']})" if candidate["year"] else "",
        language=candidate["language"].upper() or "N/A",
        file_count=total,
        website_part=website_part,
    ) + ep_note

    token, ctx = await _new_result_entry(files, effective_query, season, episode, 0, max_results, total, extra_buttons)
    markup = await _build_markup(bot, token, files, ctx)

    if status_msg:
        try:
            await status_msg.delete()
        except Exception:
            pass

    if poster_is_file_id:
        # Cached in POSTER_CHANNEL — send the existing file_id directly.
        # No bytes downloaded or held in memory on our end at all.
        await bot.send_photo(chat_id, photo=poster_ref, caption=caption, reply_markup=markup)
    else:
        photo_buf = io.BytesIO(poster_ref) if poster_ref else None
        if photo_buf:
            photo_buf.name = "poster.png"
        try:
            await bot.send_photo(
                chat_id,
                photo=photo_buf,
                caption=caption,
                reply_markup=markup,
            )
        finally:
            # Free the in-memory poster bytes as soon as Telegram has it —
            # no reason to hold a ~1MB+ buffer alive for the rest of this
            # task's lifetime. (The disk/channel caches in utils/poster.py
            # are separate, deliberately-kept caches for speed on repeat
            # searches — this only clears the one-off send buffer.)
            if photo_buf:
                photo_buf.close()
            del poster_ref


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
