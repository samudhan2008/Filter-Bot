import asyncio
import logging
import re
import time

from pyrogram import Client, filters, enums
from pyrogram.errors.exceptions.bad_request_400 import ChannelInvalid, UsernameInvalid, UsernameNotModified
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message

import info
from database.filesdb import save_files_batch

logger = logging.getLogger(__name__)
_lock = asyncio.Lock()
_cancel_flag = {"cancel": False}

# admin_id -> {"ts": float}  — tracks "we asked this admin for a channel
# link, waiting for their next message". Deliberately not using
# Client.ask()/.listen(): this pyrofork build doesn't actually implement
# them (confirmed by testing — calling ask() raised AttributeError, and
# with no visible reply, which made the whole command look silently dead).
_PENDING_INDEX = {}
_PENDING_TTL = 300

BATCH_SIZE = 200  # bulk-write batch; larger batches = far fewer DB round trips
LINK_RE = re.compile(r"(https://)?(t\.me/|telegram\.me/|telegram\.dog/)(c/)?(\d+|[a-zA-Z_0-9]+)/(\d+)$")


async def _iter_messages_by_id(bot: Client, chat_id, last_msg_id: int, batch_size: int = 200):
    """
    Reimplementation of Client.iter_messages that doesn't depend on it being
    mixed into the Client class (confirmed missing on this pyrofork build).
    Walks message IDs from last_msg_id down to 1 in chunks, using
    get_messages(list_of_ids), which is a core, always-present method.
    """
    current_id = last_msg_id
    while current_id > 0:
        batch_ids = list(range(max(1, current_id - batch_size + 1), current_id + 1))
        try:
            messages = await bot.get_messages(chat_id, batch_ids)
        except Exception as e:
            logger.warning(f"get_messages batch failed at {current_id}: {e}")
            messages = []
        for msg in reversed(messages):
            yield msg
        current_id -= batch_size


def _gc_pending():
    now = time.time()
    dead = [uid for uid, v in _PENDING_INDEX.items() if now - v["ts"] > _PENDING_TTL]
    for uid in dead:
        _PENDING_INDEX.pop(uid, None)


@Client.on_message(filters.command('index') & filters.user(info.ADMINS))
async def index_cmd(bot: Client, message: Message):
    """
    /index — no argument: ask for the channel link/forward as the *next*
    message from this admin (tracked via _PENDING_INDEX, not a blocking
    listener). /index <link> — skip the prompt and go straight to it.
    """
    parts = message.text.split(maxsplit=1)
    if len(parts) > 1 and parts[1].strip():
        return await _handle_index_input(bot, message, parts[1].strip(), reply_to=message)

    if _lock.locked():
        return await message.reply("⏳ Another indexing job is already running. Try again later.")

    _gc_pending()
    _PENDING_INDEX[message.from_user.id] = {"ts": time.time(), "chat_id": message.chat.id}
    await message.reply(
        "📥 Send the channel's last message link, or forward the last message from it, as your "
        "next message here.\n\n(Or next time, skip this step: <code>/index &lt;link&gt;</code>)"
    )


@Client.on_message(filters.private & filters.user(info.ADMINS) & ~filters.command(
    ['start', 'index', 'auth', 'unauth', 'authlist', 'ban', 'unban', 'broadcast', 'stats', 'logs', 'reindex_check']
), group=-1)
async def index_link_reply(bot: Client, message: Message):
    """Catches an admin's reply to the /index prompt above. Runs in group=-1
    so it's checked before the general search handler, and only actually
    does anything if that admin has a pending /index request."""
    _gc_pending()
    pending = _PENDING_INDEX.get(message.from_user.id)
    if not pending:
        return  # not awaiting anything from this admin — let normal handlers (search) run
    _PENDING_INDEX.pop(message.from_user.id, None)

    if message.text:
        await _handle_index_input(bot, message, message.text.strip(), reply_to=message)
    elif message.forward_from_chat and message.forward_from_chat.type == enums.ChatType.CHANNEL:
        chat_id = message.forward_from_chat.username or message.forward_from_chat.id
        last_msg_id = message.forward_from_message_id
        await _start_indexing(bot, message, chat_id, last_msg_id)
    else:
        await message.reply("❌ That's not a link or a forwarded channel message. Run /index again.")


async def _handle_index_input(bot: Client, message: Message, text: str, reply_to: Message):
    match = LINK_RE.match(text)
    if not match:
        return await reply_to.reply("❌ Invalid link. Run /index again.")
    chat_id = match.group(4)
    last_msg_id = int(match.group(5))
    if chat_id.isnumeric():
        chat_id = int("-100" + chat_id)
    await _start_indexing(bot, message, chat_id, last_msg_id)


async def _start_indexing(bot: Client, message: Message, chat_id, last_msg_id: int):
    try:
        await bot.get_chat(chat_id)
    except ChannelInvalid:
        return await message.reply("❌ This looks like a private channel — make me an admin there first.")
    except (UsernameInvalid, UsernameNotModified):
        return await message.reply("❌ Invalid channel link/username.")
    except Exception as e:
        return await message.reply(f"❌ Error: {e}")

    try:
        target = await bot.get_messages(chat_id, last_msg_id)
    except Exception:
        return await message.reply("❌ Make sure I'm an admin in that channel.")
    if target.empty:
        return await message.reply("❌ That message doesn't exist / channel is empty there.")

    if _lock.locked():
        return await message.reply("⏳ Another indexing job is already running. Try again later.")

    status = await message.reply(
        "🚀 Starting indexing…",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data="index_cancel")]]),
    )
    await _run_index(bot, chat_id, last_msg_id, status)


@Client.on_callback_query(filters.regex(r'^index_cancel$'))
async def cancel_index(bot, query):
    _cancel_flag["cancel"] = True
    await query.answer("Cancelling…")


async def _run_index(bot: Client, chat_id, last_msg_id: int, status_msg):
    total_saved = duplicate = errors = skipped_non_media = skipped_deleted = 0
    buffer = []
    current = 0

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
        _cancel_flag["cancel"] = False
        try:
            async for msg in _iter_messages_by_id(bot, chat_id, last_msg_id):
                if _cancel_flag["cancel"]:
                    await flush()
                    break
                current += 1
                if current % 500 == 0:
                    try:
                        await status_msg.edit_text(
                            f"📥 Indexing…\nScanned: <code>{current}</code>\nSaved: <code>{total_saved}</code>\n"
                            f"Duplicates: <code>{duplicate}</code>\nErrors: <code>{errors}</code>",
                            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data="index_cancel")]]),
                        )
                    except Exception:
                        pass
                if msg.empty:
                    skipped_deleted += 1
                    continue
                if not msg.media:
                    skipped_non_media += 1
                    continue
                if msg.media not in (enums.MessageMediaType.VIDEO, enums.MessageMediaType.AUDIO, enums.MessageMediaType.DOCUMENT):
                    skipped_non_media += 1
                    continue
                media = getattr(msg, msg.media.value, None)
                if not media:
                    skipped_non_media += 1
                    continue
                media.file_type = msg.media.value
                media.caption = msg.caption
                buffer.append(media)
                if len(buffer) >= BATCH_SIZE:
                    await flush()
            else:
                await flush()
        except Exception as e:
            await flush()
            logger.exception("Indexing crashed")
            return await status_msg.edit(f"❌ Indexing stopped due to an error: {e}")

    await status_msg.edit(
        f"✅ Indexing complete!\nSaved: <code>{total_saved}</code>\nDuplicates skipped: <code>{duplicate}</code>\n"
        f"Non-media skipped: <code>{skipped_non_media}</code>\nDeleted skipped: <code>{skipped_deleted}</code>\n"
        f"Errors: <code>{errors}</code>"
    )
