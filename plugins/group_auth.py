import logging
from pyrogram import Client, filters
from pyrogram.types import Message

import info
from database import usersdb

logger = logging.getLogger(__name__)


async def _is_group_admin(bot: Client, chat_id: int, user_id: int) -> bool:
    if user_id in info.ADMINS:
        return True
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in ("administrator", "creator")
    except Exception:
        return False


@Client.on_message(filters.command('authorize') & filters.group)
async def authorize_group(bot: Client, message: Message):
    if not await _is_group_admin(bot, message.chat.id, message.from_user.id):
        return await message.reply("🚫 Only group admins can authorize this bot.")
    await usersdb.add_group(message.chat.id, message.chat.title or "")
    await usersdb.set_group_authorized(message.chat.id, True)
    await message.reply("✅ This group is now authorized. Search away!")


@Client.on_message(filters.command('unauthorize') & filters.group)
async def unauthorize_group(bot: Client, message: Message):
    if not await _is_group_admin(bot, message.chat.id, message.from_user.id):
        return await message.reply("🚫 Only group admins can unauthorize this bot.")
    await usersdb.set_group_authorized(message.chat.id, False)
    await message.reply("🚫 This group is no longer authorized.")


async def group_is_allowed(chat_id: int) -> bool:
    if info.AUTH_GROUPS is not None:
        return chat_id in info.AUTH_GROUPS
    return await usersdb.is_group_authorized(chat_id)
