import logging
from pyrogram.errors import UserNotParticipant
from pyrogram.enums import ChatMemberStatus
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

import info

logger = logging.getLogger(__name__)


async def is_subscribed(bot, user_id: int) -> bool:
    if not info.AUTH_CHANNEL:
        return True
    try:
        member = await bot.get_chat_member(info.AUTH_CHANNEL, user_id)
        return member.status not in (ChatMemberStatus.LEFT, ChatMemberStatus.BANNED)
    except UserNotParticipant:
        return False
    except Exception as e:
        logger.warning(f"force-sub check failed: {e}")
        return True  # fail open so a misconfigured channel doesn't brick the bot


async def fsub_markup(bot):
    link = info.FSUB_INVITE_LINK
    if not link:
        try:
            invite = await bot.create_chat_invite_link(info.AUTH_CHANNEL)
            link = invite.invite_link
        except Exception as e:
            logger.warning(f"Could not create invite link: {e}")
            link = "https://t.me"
    return InlineKeyboardMarkup([[InlineKeyboardButton("📢 Join Channel", url=link)]])
