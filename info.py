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
CHANNELS = [int(c) if id_pattern.search(c) else c for c in environ.get('CHANNELS', '').split()]
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

# ---- URL shortener ----
SHORTLINK_MODE = is_enabled(environ.get('SHORTLINK_MODE', 'False'), False)
SHORTLINK_URL = environ.get('SHORTLINK_URL', '')     # e.g. tnshort.net
SHORTLINK_API = environ.get('SHORTLINK_API', '')

# ---- Misc behaviour ----
PICS = (environ.get('PICS', 'https://graph.org/file/ce1723991756e48c35aa1.jpg')).split()
CACHE_TIME = int(environ.get('CACHE_TIME', '1800'))
PROTECT_CONTENT = is_enabled(environ.get('PROTECT_CONTENT', 'False'), False)
MAX_RESULTS = int(environ.get('MAX_RESULTS', '10'))
PORT = environ.get('PORT', '8080')
USE_CAPTION_FILTER = is_enabled(environ.get('USE_CAPTION_FILTER', 'True'), True)

SUPPORT_CHAT = environ.get('SUPPORT_CHAT', '')
