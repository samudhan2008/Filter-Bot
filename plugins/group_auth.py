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


async def _log(bot: Client, text: str):
    if info.LOG_CHANNEL:
        try:
            await bot.send_message(info.LOG_CHANNEL, text)
        except Exception as e:
            logger.warning(f"Could not log to LOG_CHANNEL: {e}")


@Client.on_message(filters.command('auth'))
async def auth_cmd(bot: Client, message: Message):
    parts = message.text.split(maxsplit=1)
    is_global_admin = message.from_user and message.from_user.id in info.ADMINS

    # Case 1: /auth <group_id> — bot-wide admins only, works from anywhere
    # (PM to the bot, another group, wherever) and authorizes that group id
    # directly without needing to be in it or run the command there.
    if len(parts) > 1 and parts[1].strip().lstrip('-').isdigit():
        if not is_global_admin:
            return await message.reply("🚫 Only bot admins can authorize a group by ID.")
        target_id = int(parts[1].strip())
        await usersdb.add_group(target_id, "")
        await usersdb.set_group_authorized(target_id, True)
        await message.reply(f"✅ Group <code>{target_id}</code> is now authorized.")
        await _log(bot, f"🔓 Group <code>{target_id}</code> authorized by {message.from_user.mention} "
                         f"(<code>{message.from_user.id}</code>) via /auth <id>.")
        return

    # Case 2: plain /auth — authorizes the group the command was sent in.
    # Allowed for bot admins OR admins of that specific group.
    if message.chat.type not in ("group", "supergroup"):
        return await message.reply(
            "Usage:\n"
            "• Send <code>/auth</code> inside a group (as a group admin) to authorize it.\n"
            "• Send <code>/auth &lt;group_id&gt;</code> from anywhere (bot admins only) to authorize a group by ID."
        )

    if not await _is_group_admin(bot, message.chat.id, message.from_user.id):
        return await message.reply("🚫 Only group admins can authorize this bot.")

    await usersdb.add_group(message.chat.id, message.chat.title or "")
    await usersdb.set_group_authorized(message.chat.id, True)
    await message.reply("✅ This group is now authorized. Search away!")
    await _log(bot, f"🔓 Group <b>{message.chat.title}</b> (<code>{message.chat.id}</code>) authorized by "
                     f"{message.from_user.mention} (<code>{message.from_user.id}</code>).")


@Client.on_message(filters.command('unauth'))
async def unauth_cmd(bot: Client, message: Message):
    parts = message.text.split(maxsplit=1)
    is_global_admin = message.from_user and message.from_user.id in info.ADMINS

    if len(parts) > 1 and parts[1].strip().lstrip('-').isdigit():
        if not is_global_admin:
            return await message.reply("🚫 Only bot admins can unauthorize a group by ID.")
        target_id = int(parts[1].strip())
        await usersdb.set_group_authorized(target_id, False)
        await message.reply(f"🚫 Group <code>{target_id}</code> is no longer authorized.")
        await _log(bot, f"🔒 Group <code>{target_id}</code> unauthorized by {message.from_user.mention}.")
        return

    if message.chat.type not in ("group", "supergroup"):
        return await message.reply(
            "Usage:\n"
            "• Send <code>/unauth</code> inside a group (as a group admin) to remove authorization.\n"
            "• Send <code>/unauth &lt;group_id&gt;</code> from anywhere (bot admins only)."
        )

    if not await _is_group_admin(bot, message.chat.id, message.from_user.id):
        return await message.reply("🚫 Only group admins can unauthorize this bot.")

    await usersdb.set_group_authorized(message.chat.id, False)
    await message.reply("🚫 This group is no longer authorized.")
    await _log(bot, f"🔒 Group <b>{message.chat.title}</b> (<code>{message.chat.id}</code>) unauthorized by "
                     f"{message.from_user.mention}.")


@Client.on_message(filters.command('authlist') & filters.user(info.ADMINS))
async def authlist_cmd(bot: Client, message: Message):
    from database.usersdb import groups_col
    cursor = groups_col.find({'authorized': True})
    groups = await cursor.to_list(length=200)
    if not groups:
        return await message.reply("No groups are currently authorized.")
    lines = [f"• <code>{g['_id']}</code> — {g.get('title') or 'unknown title'}" for g in groups]
    text = "🔓 <b>Authorized groups</b> (" + str(len(groups)) + "):\n\n" + "\n".join(lines[:100])
    await message.reply(text)


async def group_is_allowed(chat_id: int) -> bool:
    if info.AUTH_GROUPS is not None:
        return chat_id in info.AUTH_GROUPS
    return await usersdb.is_group_authorized(chat_id)
