"""
One shared aiohttp.ClientSession for the whole bot process, instead of
every TMDB/backend/poster/shortlink call opening (and tearing down) its own
`async with aiohttp.ClientSession()`. Each fresh session pays a new TCP+TLS
handshake; reusing one with connection pooling/keep-alive cuts that latency
out of basically every external call the bot makes, which is where most of
the "search feels slow" time was actually going.
"""

import asyncio
import aiohttp

_session: aiohttp.ClientSession | None = None
_lock = asyncio.Lock()


async def get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        async with _lock:
            if _session is None or _session.closed:
                connector = aiohttp.TCPConnector(limit=100, limit_per_host=20, ttl_dns_cache=300)
                _session = aiohttp.ClientSession(connector=connector)
    return _session


async def close_session():
    global _session
    if _session and not _session.closed:
        await _session.close()
        _session = None
