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

# tz_aware=True: Motor/PyMongo returns naive datetimes by default (BSON
# stores UTC instants with no tzinfo attached on read), while every write
# in this codebase uses timezone-aware datetime.now(timezone.utc). Naive
# and aware datetimes can't be compared in Python — it raises TypeError —
# which was silently breaking any check that read a stored datetime back
# and compared it (e.g. verifydb.is_verified()'s verified_until check,
# which is exactly why search went silent right after someone verified,
# and why the admin panel's Verification Lookup errored on any user who'd
# ever verified). This makes every read consistently aware too, matching
# every write.
client = AsyncIOMotorClient(info.DATABASE_URI, tz_aware=True)
db = client[info.DATABASE_NAME]
