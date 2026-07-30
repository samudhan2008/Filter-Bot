import logging
from urllib.parse import quote

from pyrogram import Client, filters, enums
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

import info
from database import usersdb, filesdb, verifydb, settingsdb
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
    if len(args) > 1 and args[1] == 'verified':
        # Verification itself already happened server-side (the frontend's
        # /finish page called our API directly) — this is just the user
        # landing back in Telegram afterward, so there's nothing left to
        # redeem here, just acknowledge it.
        return await message.reply(
            f"✅ <b>You're verified!</b> Go ahead and tap the file button again — "
            f"you're good for the next {info.VERIFY_VALID_HOURS} hours."
        )

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
    # Anti-bypass gate: only active when SHORTLINK_MODE is on, and only if
    # the frontend that runs the cookie-continuity check is actually
    # configured. Sharing a raw file_id deep link (or even the raw
    # shortlink destination) doesn't help bypass this — the bot refuses to
    # hand over the file unless *this* user currently has a verification
    # that was obtained by genuinely completing the shortlink round trip,
    # proven via a cookie set at the start of that flow and checked again
    # at the end (see database/verifydb.py).
    if await settingsdb.is_shortlink_mode():
        if info.FRONTEND_URL and info.FRONTEND_API_SECRET:
            if not await verifydb.is_verified(message.from_user.id):
                return await send_verify_prompt(bot, message)
        else:
            logger.warning(
                "SHORTLINK_MODE is on but FRONTEND_URL/FRONTEND_API_SECRET aren't configured — "
                "skipping the verification gate (file links are still shortened, just not gated)."
            )

    doc = await filesdb.get_file_by_id(file_id)
    if not doc:
        return await message.reply("❌ File not found — it may have been removed.")
    caption = doc.caption
    if caption and len(caption) > 1024:
        caption = caption[:1021] + "..."
    try:
        await bot.send_cached_media(
            chat_id=message.chat.id,
            file_id=file_id,
            caption=caption,
            parse_mode=enums.ParseMode.HTML,
            protect_content=info.PROTECT_CONTENT,
        )
    except Exception as e:
        logger.warning(f"send_cached_media failed: {e}")
        await message.reply("❌ Couldn't send that file. It may have expired — please search again.")


async def send_verify_prompt(bot: Client, message: Message):
    if not await verifydb.can_create_session(message.from_user.id):
        return await message.reply(
            "⏳ Give it a few seconds and try again — a verification link was just requested for your account."
        )

    from utils.shortlink import shorten

    session_id = await verifydb.create_session(message.from_user.id)
    finish_url = f"{info.FRONTEND_URL}/finish/{session_id}"
    short_finish_url = await shorten(finish_url)
    go_url = f"{info.FRONTEND_URL}/go/{session_id}?next={quote(short_finish_url, safe='')}"

    await message.reply(
        "🔒 <b>One-time verification needed</b>\n\n"
        f"Tap below to verify — it's valid for {info.VERIFY_VALID_HOURS} hours, so you'll only need to "
        "do this occasionally, not on every file.\n\n"
        "⚠️ Please complete it normally (don't skip ahead using a saved link) — bypass attempts are "
        "detected and flagged.\n\n"
        "Once verified, come back and search or tap a file button again.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Verify Now", url=go_url)]]),
    )


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
