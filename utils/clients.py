"""
Optional multi-client support. If WORKER_BOT_TOKENS is set, spins up
additional Pyrogram Client instances (separate bot accounts) purely to
send files / broadcast messages, round-robin, so a single bot doesn't hit
Telegram's per-bot flood limits during a big broadcast or a traffic spike.

The primary bot (from BOT_TOKEN) still handles all commands and search —
workers are only ever used for outbound sends via `get_sender()`.
"""

import logging
import itertools

from pyrogram import Client

import info

logger = logging.getLogger(__name__)

_workers = []
_round_robin = None


async def start_workers():
    global _round_robin
    if not info.WORKER_BOT_TOKENS:
        logger.info("No WORKER_BOT_TOKENS set — running single-client.")
        return []

    for i, token in enumerate(info.WORKER_BOT_TOKENS, start=1):
        try:
            client = Client(
                name=f"scfiles_worker_{i}",
                api_id=info.API_ID,
                api_hash=info.API_HASH,
                bot_token=token,
                in_memory=True,
            )
            await client.start()
            _workers.append(client)
            logger.info(f"Worker bot {i} started: @{(await client.get_me()).username}")
        except Exception as e:
            logger.warning(f"Worker bot {i} failed to start: {e}")

    if _workers:
        _round_robin = itertools.cycle(_workers)
    return _workers


async def stop_workers():
    for w in _workers:
        try:
            await w.stop()
        except Exception:
            pass


def get_sender(primary: Client) -> Client:
    """Returns the client to use for the next outbound send — a worker if
    any are available, else the primary bot."""
    if _round_robin is None:
        return primary
    return next(_round_robin)


def worker_count() -> int:
    return len(_workers)
