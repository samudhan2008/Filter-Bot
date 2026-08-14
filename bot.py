import json
import logging
import logging.config
import time

from aiohttp import web
from pyrogram import Client

import info

logging.config.fileConfig('logging.conf')
logging.getLogger().setLevel(logging.INFO)
logging.getLogger("pyrogram").setLevel(logging.WARNING)

if info.JSON_LOGS:
    class JsonFormatter(logging.Formatter):
        def format(self, record):
            payload = {
                "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            }
            if record.exc_info:
                payload["exc_info"] = self.formatException(record.exc_info)
            return json.dumps(payload)

    for handler in logging.getLogger().handlers:
        handler.setFormatter(JsonFormatter())

logger = logging.getLogger(__name__)


class SCFilesBot(Client):
    def __init__(self):
        super().__init__(
            name=info.SESSION,
            api_id=info.API_ID,
            api_hash=info.API_HASH,
            bot_token=info.BOT_TOKEN,
            plugins={"root": "plugins"},
            workers=info.WORKERS,
            sleep_threshold=60,
        )

    async def start(self):
        await super().start()
        me = await self.get_me()
        self.username = me.username
        self.uptime = time.time()
        logger.info(f"SC Files Bot started as @{self.username}")

        # Search-index setup (safe to call every startup -- Mongo no-ops if
        # the index already exists with the same spec).
        from database.filesdb import ensure_indexes
        await ensure_indexes()

        from database.statedb import ensure_state_indexes
        await ensure_state_indexes()

        from database.verifydb import ensure_verify_indexes
        await ensure_verify_indexes()

        from database.auditdb import ensure_audit_indexes
        await ensure_audit_indexes()

        from database.filedeliverydb import ensure_delivery_indexes
        await ensure_delivery_indexes()

        # Optional multi-client worker pool for send/broadcast load spreading.
        from utils.clients import start_workers
        workers = await start_workers()
        if workers:
            logger.info(f"{len(workers)} worker bot(s) online for outbound sends.")

        # Keep the "/" command menu in sync automatically — no manual
        # @BotFather /setcommands step needed after adding a command.
        from utils.commands import sync_bot_commands
        await sync_bot_commands(self)

        if info.LOG_CHANNEL:
            try:
                await self.send_message(
                    info.LOG_CHANNEL,
                    f"✅ <b>SC Files Bot restarted</b>\n\n@{self.username} is now online."
                    + (f"\n👷 {len(workers)} worker bot(s) active." if workers else ""),
                )
            except Exception as e:
                logger.warning(f"Could not message LOG_CHANNEL: {e}")

    async def stop(self, *args):
        from utils.clients import stop_workers
        from utils.http import close_session
        from utils.keepalive import stop_keepalive
        await stop_keepalive()
        await stop_workers()
        await close_session()
        await super().stop()
        logger.info("SC Files Bot stopped.")


async def _web_server(bot: "SCFilesBot"):
    async def ping(request):
        return web.Response(text="SC Files Bot is alive.")
    app = web.Application()
    app.add_routes([web.get('/', ping)])

    from utils.frontend_api import register_routes
    register_routes(app, bot)

    from utils.admin_api import register_admin_routes
    register_admin_routes(app, bot)

    from utils.dbms_api import register_dbms_routes
    register_dbms_routes(app, bot)

    return app


if __name__ == '__main__':
    from pyrogram import idle
    import asyncio

    async def main():
        bot = SCFilesBot()
        await bot.start()
        runner = web.AppRunner(await _web_server(bot))
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", int(info.PORT))
        await site.start()

        from utils.keepalive import start_keepalive
        await start_keepalive()

        await idle()
        await bot.stop()

    asyncio.run(main())
