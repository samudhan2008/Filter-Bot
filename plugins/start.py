import logging

from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

import info
from database import usersdb, filesdb
from plugins.force_sub import is_subscribed, fsub_markup

logger = logging.getLogger(__name__)


@Client.on_message(filters.command('start') & filters.private)
async def start_cmd(bot: Client, message: Message):
    user = message.from_user
    await usersdb.add_user(user.id)

    if await usersdb.is_banned(user.id):
        return await message.reply("🚫 You are banned from using this bot.")

    if not await is_subscribed(bot, user.id):
        markup = await fsub_markup(bot)
        return await message.reply(
            "👋 Please join our channel first to use SC Files Bot.",
            reply_markup=markup,
        )

    args = message.text.split(maxsplit=1)
    if len(args) > 1 and args[1].startswith('file_'):
        return await deliver_file(bot, message, args[1].split('file_', 1)[1])

    await message.reply(
        f"👋 Hi {user.mention}, I'm <b>SC Files Bot</b>!\n\n"
        "Add me to your group and search any movie or series name — I'll show "
        "you a poster, details, and the files, plus a link to watch it on "
        "SC Files if it's on the website.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Add me to a group", url=f"https://t.me/{bot.username}?startgroup=true")],
            [InlineKeyboardButton("🌐 SC Files Website", url=info.WEBSITE_URL)],
        ]),
    )


async def deliver_file(bot: Client, message: Message, file_id: str):
    doc = await filesdb.get_file_by_id(file_id)
    if not doc:
        return await message.reply("❌ File not found — it may have been removed.")
    try:
        await bot.send_cached_media(
            chat_id=message.chat.id,
            file_id=file_id,
            protect_content=info.PROTECT_CONTENT,
        )
    except Exception as e:
        logger.warning(f"send_cached_media failed: {e}")
        await message.reply("❌ Couldn't send that file. It may have expired — please search again.")


@Client.on_message(filters.new_chat_members)
async def on_added_to_group(bot: Client, message: Message):
    for member in message.new_chat_members:
        if member.id == bot.me.id:
            await usersdb.add_group(message.chat.id, message.chat.title or "")
            await message.reply(
                "👋 Thanks for adding <b>SC Files Bot</b>!\n\n"
                "Ask a group admin to run /auth so I can start serving search "
                "results here."
            )
