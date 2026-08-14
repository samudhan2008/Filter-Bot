"""
Self-pings PING_URL (this same bot's own public web server URL) on an
interval, purely to stop free-tier hosts like Render/Koyeb from spinning
the instance down after a period of no inbound HTTP traffic. Telegram
updates alone don't count as "traffic" to these platforms — only actual
HTTP requests to the exposed port do — so without this, a bot that's
idle in chat (even if actively used) can still get cold-stopped.

No-ops entirely if PING_URL isn't set, so this is safe to leave wired in
even on hosts that don't need it (paid tiers, always-on instances, etc.).
"""

import asyncio
import logging

import aiohttp

from utils.http import get_session
import info

logger = logging.getLogger(__name__)

_task: asyncio.Task | None = None
_TIMEOUT = aiohttp.ClientTimeout(total=30)


async def _ping_loop():
    session = await get_session()
    while True:
        # Sleep first: the web server has only just started when this loop
        # kicks off, and PING_URL points at that same server, so give it a
        # beat before the very first self-request instead of racing it.
        await asyncio.sleep(info.PING_INTERVAL)
        try:
            async with session.get(info.PING_URL, timeout=_TIMEOUT) as resp:
                if resp.status >= 400:
                    logger.warning(f"Self-ping to {info.PING_URL} returned HTTP {resp.status}")
                else:
                    logger.info(f"Self-ping OK ({resp.status})")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # Never let a flaky ping (network blip, brief cold-start delay,
            # DNS hiccup) kill the loop — just log and try again next cycle.
            logger.warning(f"Self-ping to {info.PING_URL} failed: {e}")


async def start_keepalive():
    global _task
    if not info.PING_URL:
        logger.info("PING_URL not set — self-ping keepalive disabled.")
        return None
    if _task and not _task.done():
        return _task
    _task = asyncio.create_task(_ping_loop())
    logger.info(f"Self-ping keepalive started: {info.PING_URL} every {info.PING_INTERVAL}s.")
    return _task


async def stop_keepalive():
    global _task
    if _task and not _task.done():
        _task.cancel()
        try:
            await _task
        except (asyncio.CancelledError, Exception):
            pass
    _task = None
