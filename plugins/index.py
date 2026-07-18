import asyncio
import logging
import re

from pyrogram import Client, filters, enums
from pyrogram.errors.exceptions.bad_request_400 import ChannelInvalid, UsernameInvalid, UsernameNotModified
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

import info
from database.filesdb import save_files_batch

logger = logging.getLogger(__name__)
_lock = asyncio.Lock()
_cancel_flag = {"cancel": False}

BATCH_SIZE = 200  # bulk-write batch; larger batches = far fewer DB round trips
LINK_RE = re.compile(r"(https://)?(t\.me/|telegram\.me/|telegram\.dog/)(c/)?(\d+|[a-zA-Z_0-9]+)/(\d+)$")


@Client.on_message(filters.command('index') & filters.user(info.ADMINS))
async def index_cmd(bot: Client, message):
    ask = await bot.ask(message.chat.id, "📥 Send the last message link of the channel to index (or forward the last message from it).")
    chat_id, last_msg_id = None, None

    if ask.text:
        match = LINK_RE.match(ask.text.strip())
        if not match:
            return await ask.reply("❌ Invalid link. Try /index again.")
        chat_id = match.group(4)
        last_msg_id = int(match.group(5))
        if chat_id.isnumeric():
            chat_id = int("-100" + chat_id)
    elif ask.forward_from_chat and ask.forward_from_chat.type == enums.ChatType.CHANNEL:
        last_msg_id = ask.forward_from_message_id
        chat_id = ask.forward_from_chat.username or ask.forward_from_chat.id
    else:
        return await ask.reply("❌ That's not a link or a forwarded channel message.")

    try:
        await bot.get_chat(chat_id)
    except ChannelInvalid:
        return await ask.reply("❌ This looks like a private channel — make me an admin there first.")
    except (UsernameInvalid, UsernameNotModified):
        return await ask.reply("❌ Invalid channel link/username.")
    except Exception as e:
        return await ask.reply(f"❌ Error: {e}")

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
            async for msg in bot.iter_messages(chat_id, last_msg_id, 0):
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
