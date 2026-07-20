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

MEDIA_TYPES = (enums.MessageMediaType.VIDEO, enums.MessageMediaType.AUDIO, enums.MessageMediaType.DOCUMENT)


async def _notify_log(bot: Client, text: str):
    if info.LOG_CHANNEL:
        try:
            await bot.send_message(info.LOG_CHANNEL, text)
        except Exception as e:
            logger.warning(f"Could not post to LOG_CHANNEL: {e}")


if info.CHANNELS:
    @Client.on_message(filters.chat(info.CHANNELS) & (filters.video | filters.document | filters.audio))
    async def auto_index_new_file(bot: Client, message: Message):
        if not message.media or message.media not in MEDIA_TYPES:
            return
        media = getattr(message, message.media.value, None)
        if not media:
            return
        media.file_type = message.media.value
        media.caption = message.caption

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
