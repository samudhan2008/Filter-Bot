"""
Real-time indexing for the channels listed in the CHANNELS env var
(comma-separated IDs/usernames): any video/document/audio posted there is
saved to the file DB the moment it arrives — no manual /index needed.

/index (plugins/index.py) is still there for backfilling a channel's
existing history; this plugin only handles new posts going forward.
"""

import logging

from pyrogram import Client, filters, enums
from pyrogram.types import Message

import info
from database.filesdb import save_file

logger = logging.getLogger(__name__)

MEDIA_TYPES = (enums.MessageMediaType.VIDEO, enums.MessageMediaType.AUDIO,
               enums.MessageMediaType.DOCUMENT, enums.MessageMediaType.ANIMATION)


async def _notify_log(bot: Client, text: str):
    if info.LOG_CHANNEL:
        try:
            await bot.send_message(info.LOG_CHANNEL, text)
        except Exception as e:
            logger.warning(f"Could not post to LOG_CHANNEL: {e}")


async def _resolve_caption(bot: Client, message: Message):
    """Same album-caption backfill as /index: only one item in a Telegram
    album carries the caption, so pull it from the group for the rest."""
    if message.caption:
        return message.caption
    if not message.media_group_id:
        return None
    try:
        group = await bot.get_media_group(message.chat.id, message.id)
        for item in group:
            if item.caption:
                return item.caption
    except Exception as e:
        logger.warning(f"get_media_group failed for {message.id}: {e}")
    return None


if info.CHANNELS:
    @Client.on_message(filters.chat(info.CHANNELS) & (filters.video | filters.document | filters.audio | filters.animation))
    async def auto_index_new_file(bot: Client, message: Message):
        if not message.media or message.media not in MEDIA_TYPES:
            return
        media = getattr(message, message.media.value, None)
        if not media:
            return
        media.file_type = message.media.value
        media.caption = await _resolve_caption(bot, message)
        media.file_date = message.date

        ok, code = await save_file(media)
        if ok:
            logger.info(f"Auto-indexed new file from {message.chat.id}: {media.file_name}")
        elif code == 0:
            pass  # duplicate — already indexed, nothing to do
        else:
            logger.warning(f"Auto-index failed for a file in {message.chat.id}")
            await _notify_log(bot, f"⚠️ Auto-index failed for a file posted in <code>{message.chat.id}</code>.")
else:
    logger.info("CHANNELS is empty — auto-indexing of new channel posts is off.")
