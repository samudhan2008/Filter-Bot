"""
Keeps Telegram's bot command menu (the "/" button in chats) in sync with
what the bot actually supports, automatically, every startup — no need to
manually run /setcommands with @BotFather again after adding a command.

Public commands are visible to everyone. Admin commands are pushed only
into each admin's own private chat scope, so regular users/groups never
see /ban, /broadcast, etc. in their menu.
"""

import logging

from pyrogram import Client
from pyrogram.types import BotCommand, BotCommandScopeDefault, BotCommandScopeChat

import info

logger = logging.getLogger(__name__)

PUBLIC_COMMANDS = [
    BotCommand("start", "Start the bot / get a shared file"),
    BotCommand("auth", "Authorize this group (group admins)"),
    BotCommand("unauth", "Remove authorization from this group"),
]

ADMIN_COMMANDS = PUBLIC_COMMANDS + [
    BotCommand("index", "Index a channel's files into the bot"),
    BotCommand("reindex_check", "Scan for titles missing from the website"),
    BotCommand("authlist", "List authorized groups"),
    BotCommand("ban", "Ban a user from using the bot"),
    BotCommand("unban", "Unban a user"),
    BotCommand("broadcast", "Broadcast a message (reply to it)"),
    BotCommand("stats", "Show bot statistics"),
    BotCommand("logs", "Get the latest log file"),
]


async def sync_bot_commands(bot: Client):
    try:
        await bot.set_bot_commands(PUBLIC_COMMANDS, scope=BotCommandScopeDefault())
    except Exception as e:
        logger.warning(f"Could not set default bot commands: {e}")

    for admin_id in info.ADMINS:
        if not isinstance(admin_id, int):
            continue  # ADMINS can technically contain non-numeric entries; skip those here
        try:
            await bot.set_bot_commands(ADMIN_COMMANDS, scope=BotCommandScopeChat(chat_id=admin_id))
        except Exception as e:
            logger.warning(f"Could not set admin bot commands for {admin_id}: {e}")

    logger.info("Bot command menu synced.")
