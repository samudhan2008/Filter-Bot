import logging
import logging.config
from aiohttp import web
from pyrogram import Client
import info

logging.config.fileConfig('logging.conf')
logging.getLogger().setLevel(logging.INFO)
logging.getLogger("pyrogram").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


class SCFilesBot(Client):
    def __init__(self):
        super().__init__(
            name=info.SESSION,
            api_id=info.API_ID,
            api_hash=info.API_HASH,
            bot_token=info.BOT_TOKEN,
            plugins={"root": "plugins"},
            workers=50,
            sleep_threshold=60,
        )

    async def start(self):
        await super().start()
        me = await self.get_me()
        self.username = me.username
        self.uptime = None
        logger.info(f"SC Files Bot started as @{self.username}")
        if info.LOG_CHANNEL:
            try:
                await self.send_message(info.LOG_CHANNEL, f"✅ <b>SC Files Bot restarted</b>\n\n@{self.username} is now online.")
            except Exception as e:
                logger.warning(f"Could not message LOG_CHANNEL: {e}")

    async def stop(self, *args):
        await super().stop()
        logger.info("SC Files Bot stopped.")


async def _web_server():
    async def ping(request):
        return web.Response(text="SC Files Bot is alive.")
    app = web.Application()
    app.add_routes([web.get('/', ping)])
    return app


if __name__ == '__main__':
    from pyrogram import idle
    import asyncio

    async def main():
        bot = SCFilesBot()
        await bot.start()
        runner = web.AppRunner(await _web_server())
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", int(info.PORT))
        await site.start()
        await idle()
        await bot.stop()

    asyncio.run(main())
