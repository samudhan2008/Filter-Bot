import asyncio
import logging

from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.errors import FloodWait

import info
from database import usersdb, filesdb

logger = logging.getLogger(__name__)


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
        return await message.reply("Usage: /ban <user_id> [reason]  (or reply to a user's message)")
    await usersdb.ban_user(target, reason)
    await message.reply(f"🚫 Banned <code>{target}</code>.{f' Reason: {reason}' if reason else ''}")


@Client.on_message(filters.command('unban') & filters.user(info.ADMINS))
async def unban_cmd(bot: Client, message: Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].lstrip('-').isdigit():
        return await message.reply("Usage: /unban <user_id>")
    await usersdb.unban_user(int(parts[1]))
    await message.reply(f"✅ Unbanned <code>{parts[1]}</code>.")


@Client.on_message(filters.command('stats') & filters.user(info.ADMINS))
async def stats_cmd(bot: Client, message: Message):
    users = await usersdb.total_users_count()
    groups = await usersdb.total_groups_count()
    files_total = await filesdb.Media.count_documents({})
    await message.reply(
        "📊 <b>SC Files Bot Stats</b>\n\n"
        f"👤 Users: <code>{users}</code>\n"
        f"👥 Groups: <code>{groups}</code>\n"
        f"🎞 Indexed files: <code>{files_total}</code>"
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

    status = await message.reply(f"📢 Broadcasting to {len(ids)} {'groups' if to_groups else 'users'}…")
    sent = failed = 0
    for cid in ids:
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
        await asyncio.sleep(0.05)  # gentle rate limiting

    await status.edit(f"✅ Broadcast done.\nSent: <code>{sent}</code>\nFailed: <code>{failed}</code>")
