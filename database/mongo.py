"""
Every database/*.py module used to call `AsyncIOMotorClient(info.DATABASE_URI)`
independently — filesdb, usersdb, statedb, postersdb each opening their own
separate connection pool to the *same* MongoDB deployment. That's pure
overhead (extra TCP connections, extra heartbeat/monitoring threads, extra
connection-pool warmup) for no benefit, since Motor/PyMongo clients are
already safe to share across coroutines. One client for the whole process.
"""

from motor.motor_asyncio import AsyncIOMotorClient

import info

client = AsyncIOMotorClient(info.DATABASE_URI)
db = client[info.DATABASE_NAME]
