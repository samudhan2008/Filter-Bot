# SC Files Bot — config
# Original file-sharing/indexing mechanics inspired by the VJ_Botz Filter-Bot,
# rewritten from scratch for SC Files.

import re
from os import environ

id_pattern = re.compile(r'^.\d+$')


def is_enabled(value, default):
    if value.lower() in ["true", "yes", "1", "enable", "y"]:
        return True
    elif value.lower() in ["false", "no", "0", "disable", "n"]:
        return False
    return default


# ---- Telegram / bot core ----
API_ID = int(environ.get('API_ID', '0'))
API_HASH = environ.get('API_HASH', '')
BOT_TOKEN = environ.get('BOT_TOKEN', '')
SESSION = environ.get('SESSION', 'SCFilesBot')

# ---- MongoDB ----
DATABASE_URI = environ.get('DATABASE_URI', '')
DATABASE_NAME = environ.get('DATABASE_NAME', 'scfilesbot')
COLLECTION_NAME = environ.get('COLLECTION_NAME', 'scfiles_media')

# ---- Admins / channels ----
LOG_CHANNEL = int(environ.get('LOG_CHANNEL', '0'))
ADMINS = [int(a) if id_pattern.search(a) else a for a in environ.get('ADMINS', '').split()]
# Channels to auto-index: any video/document/audio posted here gets saved
# to the file DB automatically, no /index needed. Comma-separated in the
# env (e.g. "-1001111111111,-1002222222222,@somepublicchannel").
CHANNELS = [
    (int(c) if id_pattern.search(c) else c)
    for c in (c.strip() for c in environ.get('CHANNELS', '').split(','))
    if c
]
INDEX_REQ_CHANNEL = int(environ.get('INDEX_REQ_CHANNEL', LOG_CHANNEL or 0))

# Admin-authorization flow (group access control): only these groups/users
# may use the bot's search unless the group is auth'd by an admin.
AUTH_GROUPS = [int(g) for g in environ.get('AUTH_GROUPS', '').split()] if environ.get('AUTH_GROUPS') else None

# ---- Force-subscribe ----
auth_channel = environ.get('AUTH_CHANNEL', '')
AUTH_CHANNEL = int(auth_channel) if auth_channel and id_pattern.search(auth_channel) else None
FSUB_INVITE_LINK = environ.get('FSUB_INVITE_LINK', '')  # optional, else bot creates one

# ---- SC Files website / backend ----
BACKEND_URL = environ.get('BACKEND_URL', '').rstrip('/')   # e.g. https://api.scfiles.example.com
WEBSITE_URL = environ.get('WEBSITE_URL', 'https://scfiles.vercel.app').rstrip('/')
BACKEND_CACHE_TTL = int(environ.get('BACKEND_CACHE_TTL', '300'))  # seconds

# ---- TMDB ----
TMDB_API_KEY = environ.get('TMDB_API_KEY', '')
TMDB_IMG_BASE = "https://image.tmdb.org/t/p"
TMDB_CACHE_TTL = int(environ.get('TMDB_CACHE_TTL', '600'))  # seconds, search + logo cache

# ---- Poster cache ----
POSTER_CACHE_DIR = environ.get('POSTER_CACHE_DIR', 'poster_cache')
POSTER_CACHE_TTL = int(environ.get('POSTER_CACHE_TTL', '86400'))  # 1 day on disk
# Optional: a channel the bot archives every generated poster into. Once a
# poster's been generated once, its Telegram file_id is stored in Mongo
# (see database/postersdb.py) keyed the same way as the disk cache, so a
# repeat request for the same poster/season/episode just re-sends that
# file_id — no re-download, no re-compositing, near-zero memory — and it
# survives restarts/redeploys, unlike the disk cache. Leave unset to keep
# disk-cache-only behavior.
poster_channel = environ.get('POSTER_CHANNEL', '')
POSTER_CHANNEL = int(poster_channel) if poster_channel and id_pattern.search(poster_channel) else None

# ---- Search engine ----
# Mongo text search scales far better than regex scans once the file
# collection is large. Needs a one-time text index (created automatically
# at startup by database/filesdb.py if this is True).
USE_MONGO_TEXT_SEARCH = is_enabled(environ.get('USE_MONGO_TEXT_SEARCH', 'False'), False)

# ---- Multi-client worker pool ----
# Extra bot tokens (space-separated) used purely to spread file-sending /
# broadcast load across multiple Telegram bot sessions, avoiding a single
# bot's flood limits during busy periods. Optional.
WORKER_BOT_TOKENS = environ.get('WORKER_BOT_TOKENS', '').split()

# ---- Structured logging ----
JSON_LOGS = is_enabled(environ.get('JSON_LOGS', 'False'), False)

# ---- URL shortener ----
SHORTLINK_MODE = is_enabled(environ.get('SHORTLINK_MODE', 'False'), False)
SHORTLINK_URL = environ.get('SHORTLINK_URL', '')     # e.g. tnshort.net
SHORTLINK_API = environ.get('SHORTLINK_API', '')

# When SHORTLINK_MODE is on, file delivery goes through a cookie-verified
# shortlink round trip (anti-bypass — see database/verifydb.py and
# utils/frontend_api.py) instead of handing files over directly. No
# separate toggle: SHORTLINK_MODE alone controls both the link shortening
# and this verification gate.
VERIFY_VALID_HOURS = int(environ.get('VERIFY_VALID_HOURS', '24'))     # how long a verification lasts
VERIFY_SESSION_TTL = int(environ.get('VERIFY_SESSION_TTL', '900'))     # seconds to complete the whole shortlink round trip
VERIFY_SESSION_COOLDOWN = int(environ.get('VERIFY_SESSION_COOLDOWN', '15'))  # min seconds between new sessions per user

# If True (default), a verification whose IP moved to a different /24
# (IPv4) or /64 (IPv6) network between /go and /finish is rejected
# outright, same as a cookie mismatch. If False, it's still accepted — the
# cookie is the strong proof — but logged for visibility instead of
# rejected. Worth knowing: with this on, a legitimate user whose mobile
# network genuinely reassigns their IP mid-flow (switching towers, wifi to
# data, etc.) can occasionally get rejected and have to verify again — a
# real tradeoff for the extra strictness, not a bug if you see it happen.
STRICT_IP_CHECK = is_enabled(environ.get('STRICT_IP_CHECK', 'True'), True)

# The Vercel frontend that runs the cookie-continuity check (see the
# separate frontend/ project). Both must be set for the verification flow
# to run — if either is missing, SHORTLINK_MODE still shortens the file
# link but skips the verification gate (fails open, with a warning logged,
# rather than silently blocking every file).
FRONTEND_URL = environ.get('FRONTEND_URL', '').rstrip('/')     # e.g. https://scfiles-verify.vercel.app
FRONTEND_API_SECRET = environ.get('FRONTEND_API_SECRET', '')    # shared secret the frontend sends on every verify API call

# Separate, higher-privilege secret for the admin dashboard's API calls
# (ban, broadcast, indexing, etc.) — deliberately not the same value as
# FRONTEND_API_SECRET above, so a compromise of the (lower-stakes) verify
# flow's secret doesn't also grant admin control.
ADMIN_API_SECRET = environ.get('ADMIN_API_SECRET', '')

# ---- Misc behaviour ----
PICS = (environ.get('PICS', 'https://graph.org/file/ce1723991756e48c35aa1.jpg')).split()
CACHE_TIME = int(environ.get('CACHE_TIME', '1800'))
PROTECT_CONTENT = is_enabled(environ.get('PROTECT_CONTENT', 'False'), False)
MAX_RESULTS = int(environ.get('MAX_RESULTS', '10'))
SUGGEST_MATCH_CUTOFF = float(environ.get('SUGGEST_MATCH_CUTOFF', '0.55'))  # difflib similarity, 0-1
SUGGEST_MAX_RESULTS = int(environ.get('SUGGEST_MAX_RESULTS', '5'))

# How long a search's in-flight state (result pages / disambiguation picks)
# stays valid, stored in Mongo (see database/statedb.py) so it survives a
# bot restart instead of living only in process memory.
RESULTS_TTL = int(environ.get('RESULTS_TTL', '3600'))   # file/pagination buttons
PENDING_TTL = int(environ.get('PENDING_TTL', '600'))     # disambiguation picker buttons
INDEX_WAIT_TTL = int(environ.get('INDEX_WAIT_TTL', '300'))  # /index "waiting for channel link" window
PORT = environ.get('PORT', '8080')
# Pyrogram's update-dispatch worker pool. 50 (the old default) is overkill
# for a small instance and adds baseline memory overhead for headroom this
# bot doesn't need — most updates here are quick DB lookups, not long CPU
# work. Bump via env if you're actually seeing update-processing backlog
# under real load; that's a different symptom than an OOM kill.
WORKERS = int(environ.get('WORKERS', '8'))
USE_CAPTION_FILTER = is_enabled(environ.get('USE_CAPTION_FILTER', 'True'), True)

SUPPORT_CHAT = environ.get('SUPPORT_CHAT', '')
