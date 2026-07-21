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

# ---- Misc behaviour ----
PICS = (environ.get('PICS', 'https://graph.org/file/ce1723991756e48c35aa1.jpg')).split()
CACHE_TIME = int(environ.get('CACHE_TIME', '1800'))
PROTECT_CONTENT = is_enabled(environ.get('PROTECT_CONTENT', 'False'), False)
MAX_RESULTS = int(environ.get('MAX_RESULTS', '10'))
SUGGEST_MATCH_CUTOFF = float(environ.get('SUGGEST_MATCH_CUTOFF', '0.55'))  # difflib similarity, 0-1
SUGGEST_MAX_RESULTS = int(environ.get('SUGGEST_MAX_RESULTS', '5'))
PORT = environ.get('PORT', '8080')
# Pyrogram's update-dispatch worker pool. 50 (the old default) is overkill
# for a small instance and adds baseline memory overhead for headroom this
# bot doesn't need — most updates here are quick DB lookups, not long CPU
# work. Bump via env if you're actually seeing update-processing backlog
# under real load; that's a different symptom than an OOM kill.
WORKERS = int(environ.get('WORKERS', '8'))
USE_CAPTION_FILTER = is_enabled(environ.get('USE_CAPTION_FILTER', 'True'), True)

SUPPORT_CHAT = environ.get('SUPPORT_CHAT', '')
