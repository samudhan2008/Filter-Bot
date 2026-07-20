# SC Files Bot

A from-scratch Telegram file-sharing/auto-filter bot for the SC Files project.
Built independently (not a fork) — inspired by the general shape of file-sharing
bots, but the search engine, poster generation, and website-linking logic are
all new.

## What it does

- **Auto-filter search** — type a movie/series name in an authorized group or
  in PM, get back:
  - A Netflix-style landscape poster (TMDB backdrop + title logo, or a text
    fallback if TMDB has no logo for that title)
  - If the search is ambiguous (e.g. "Leo" matches both the Tamil film and an
    English one) and you didn't give a year, you get tappable
    `Title (Year) · LANG` buttons to disambiguate
  - Buttons to grab each matching file (delivered via bot PM)
  - A "Watch on SC Files" button/link **only if** the title is actually on
    scfiles.vercel.app — checked against your backend by `tmdb_id`, so slug
    quirks (`sannidhanam-po` vs `sannidhanam-p.o.`) never cause a false miss
  - If it's not on the website, the admin/log channel gets a heads-up instead
    of showing the user a dead link
- **Fixed search matching** — the old bot missed files that existed in the DB
  because `.`, `-`, `_`, `+` etc weren't stripped consistently between the
  query and stored filenames. This version normalizes both sides the same
  way before matching, so punctuation never breaks a search.
- **Fast `/index`** — bulk-inserts files in batches of 200 instead of one
  DB round-trip per file.
- **Admin-authorization flow** — group admins run `/authorize` to let the bot
  respond in their group; `/unauthorize` revokes it.
- **Force-subscribe** to a channel before anyone can search.
- **URL shortener** — optional, wraps the SC Files links if `SHORTLINK_MODE`
  is on.
- **Admin tools** — `/ban`, `/unban`, `/broadcast` (users or groups),
  `/stats`, `/logs`.

## What was intentionally removed

- The old `/filters` (per-group custom auto-reply) and `/gfilters` (global
  filter) commands are gone, per your call — `/index` is the only ingestion
  path now, and it's the one that got the speed work.

## Backend contract

Set `BACKEND_URL` to your SC Files API host. The bot calls:

- `GET {BACKEND_URL}/api/movies` → list of `{tmdb_id, id, downloads, subtitles, extras}`
- `GET {BACKEND_URL}/api/series` → list of `{tmdb_id, id, seasons: [...]}`

Both are cached in memory for `BACKEND_CACHE_TTL` seconds (default 300) to
avoid hammering your API on every search.

Matching is **primarily by `tmdb_id`** — the backend's own `id` field is used
verbatim for the website link, so the bot never has to guess a slug. A fuzzy
fallback (`difflib`, 82% similarity threshold) only kicks in if an entry has
no usable `tmdb_id`.

## Environment variables

| Variable | Required | Notes |
|---|---|---|
| `API_ID`, `API_HASH`, `BOT_TOKEN` | ✅ | from my.telegram.org / @BotFather |
| `DATABASE_URI` | ✅ | MongoDB connection string |
| `DATABASE_NAME`, `COLLECTION_NAME` | – | defaults provided |
| `ADMINS` | ✅ | space-separated Telegram user IDs |
| `LOG_CHANNEL` | recommended | where indexing logs + "not on website" alerts go |
| `AUTH_CHANNEL` | optional | force-subscribe channel ID |
| `AUTH_GROUPS` | optional | space-separated group IDs; if set, only these groups work and `/authorize` is bypassed. Leave unset to use the DB-driven `/authorize` flow instead. |
| `BACKEND_URL` | ✅ for website links | e.g. `https://api.scfiles.example.com` |
| `WEBSITE_URL` | – | default `https://scfiles.vercel.app` |
| `TMDB_API_KEY` | ✅ | https://www.themoviedb.org/settings/api |
| `SHORTLINK_MODE`, `SHORTLINK_URL`, `SHORTLINK_API` | optional | e.g. `tnshort.net` |
| `PROTECT_CONTENT` | optional | `True` to block forwarding/saving of files |
| `MAX_RESULTS` | – | default 10 |

## Running

```bash
pip install -r requirements.txt
python3 bot.py
```

## File layout

```
bot.py              entrypoint
info.py             all config (env vars)
database/
  filesdb.py         indexed Telegram files + search
  usersdb.py          users, groups, bans, group authorization
  backend.py          SC Files website backend lookups
utils/
  tmdb.py             TMDB search + logo lookup
  poster.py           landscape poster compositor (Pillow)
  shortlink.py         URL shortener
  query.py            year extraction from search text
  texts.py            all user-facing message templates
  filesize.py           human-readable file sizes
plugins/
  start.py            /start, deep-link file delivery
  force_sub.py         force-subscribe check
  group_auth.py         /authorize, /unauthorize
  index.py              /index (fast bulk indexing)
  search.py             the core auto-filter search + disambiguation
  admin.py               /ban /unban /broadcast /stats /logs
```

## Notes / things worth double-checking before you go live

- I haven't run this against a live Telegram bot token or your real MongoDB/
  backend — I don't have credentials or network access to those services
  here, so this hasn't been integration-tested end-to-end. Compiles clean,
  logic is straightforward, but please test `/index` on a small channel and
  a few searches (including a deliberately ambiguous one like "Leo") before
  pointing it at your real audience.
- The TMDB "which language wins" heuristic is Tamil-first (matches your use
  case) — tweak `PREFERRED_LANGS` in `utils/tmdb.py` if that's ever wrong.
- Disambiguation buttons currently always show when TMDB returns more than
  one candidate and no year was given, per what you asked for. If that ends
  up too chatty for very common titles, `_is_unambiguous()` in
  `plugins/search.py` is the one place to loosen it.

---

## v2 additions (reliability, scale, and search improvements)

### Search & matching
- **Spell-check suggestions** — when a search returns zero local files (in PM), the bot samples titles sharing a word with your query and ranks them by similarity (`difflib`), suggesting close matches instead of a flat "not found."
- **TMDB result caching** — `search_multi()` and `get_logo_url()` are cached in memory per `TMDB_CACHE_TTL` seconds (default 600), so a trending title doesn't cost a fresh TMDB call on every search.
- **Season/episode-aware search** — queries like `"GOT S02E05"` or `"Money Heist season 3"` are parsed into season/episode filters and applied directly against the indexed files (which are now tagged with `season_number`/`ep_number` at index time).

### Poster/UX
- **On-disk poster cache** — composited posters are cached to `POSTER_CACHE_DIR` (default `poster_cache/`) keyed by `(kind, tmdb_id)`, TTL `POSTER_CACHE_TTL` (default 1 day). Repeat searches for the same title skip compositing entirely.
- **Quality-grouped file buttons** — once a result has more than 6 matching files (typical for a full series), buttons collapse to one row per resolution (`720P (6 files)`) instead of one row per raw file.

### Reliability
- **Retry + circuit breaker** (`utils/netutil.py`) wraps TMDB and backend HTTP calls: 3 attempts with exponential backoff, and after repeated consecutive failures the breaker "opens" for a cooldown window so a dead service can't stall every incoming search — cached data is served instead.
- **`/reindex_check`** (admin-only) — refreshes the backend cache and scans indexed titles for ones that don't obviously exist on the website, so gaps can be caught in bulk instead of only when a user happens to search for that exact title. It's a text-heuristic pass meant to flag candidates for a human to verify, not a definitive tmdb_id-level check.

### Admin/ops
- **`/stats`** now breaks down indexed files (movie vs series/episode files), shows backend cache age, worker-bot count, and which search mode is active.
- **Structured JSON logs** — set `JSON_LOGS=True` to switch the root logger to single-line JSON output (good for log shipping / querying).

### Scaling
- **Mongo text search option** — set `USE_MONGO_TEXT_SEARCH=True` to switch from regex scans to a proper Mongo text index (created automatically at startup). Worth flipping once the file collection gets into the hundreds of thousands; regex scans stay fine below that.
- **Multi-client worker pool** — set `WORKER_BOT_TOKENS` (space-separated extra bot tokens from @BotFather) to spin up additional bot clients used purely for broadcast sends, round-robin, so a big broadcast doesn't eat into the primary bot's flood limits. The primary bot still handles all commands/search.

### New/changed environment variables

| Variable | Default | Purpose |
|---|---|---|
| `TMDB_CACHE_TTL` | `600` | seconds to cache TMDB search/logo results |
| `POSTER_CACHE_DIR` | `poster_cache` | disk path for cached composited posters |
| `POSTER_CACHE_TTL` | `86400` | seconds before a cached poster is rebuilt |
| `USE_MONGO_TEXT_SEARCH` | `False` | switch search from regex to Mongo `$text` |
| `WORKER_BOT_TOKENS` | *(empty)* | space-separated extra bot tokens for send load-spreading |
| `JSON_LOGS` | `False` | structured JSON logging instead of plain text |
| `SUGGEST_MATCH_CUTOFF` | `0.55` | similarity threshold (0–1) for "did you mean" suggestions |
| `SUGGEST_MAX_RESULTS` | `5` | max suggestions shown |

### Still not integration-tested

Same caveat as before, now more so: circuit breakers, the worker pool, and
`USE_MONGO_TEXT_SEARCH` all need a real Telegram bot token, MongoDB, and
TMDB/backend access to verify end-to-end — I don't have those here. Suggested
test pass before going live:
1. `/index` a small channel, confirm season/episode tagging on a series (check
   a doc in Mongo for `season_number`/`ep_number`).
2. Search something ambiguous ("Leo"), something with a season/episode
   ("... S01E03"), and something guaranteed to miss (gibberish) to see the
   suggestion path.
3. Toggle `USE_MONGO_TEXT_SEARCH=True` once there's enough indexed data to
   notice a difference, and confirm the text index got created (`/stats`
   shows the active mode).
4. If you add `WORKER_BOT_TOKENS`, run a small `/broadcast` and check
   `/stats` reports the right worker count.

---

## v3 fixes/additions

### Fixed
- **`/index` crash (`AttributeError: 'SCFilesBot' object has no attribute 'iter_messages'`)** — this pyrofork build doesn't mix `iter_messages` into `Client`. Replaced with a manual ID-batch fetcher (`_iter_messages_by_id` in `plugins/index.py`) built on `get_messages`, which is always present. Functionally identical, and this was also the root cause of "no response in the group" — the DB was empty because indexing never completed, so there was nothing to match against. Once `/index` finishes cleanly, group search responds normally (it stays silent on zero matches in groups, by design, to avoid noise on unrelated chat).

### Added
- **Auto command-menu sync** — on every startup the bot pushes its command list to Telegram automatically (`utils/commands.py`), so the "/" menu is always current with no manual `@BotFather /setcommands` step. Admins (from `ADMINS`) get an extended menu (index, ban, broadcast, etc.) scoped to their own private chat; everyone else sees only `/start`, `/auth`, `/unauth`.
- **`/authorize` → `/auth`, `/unauthorize` → `/unauth`**, with a new second mode:
  - `/auth` (no args, sent inside a group) — authorizes that group. Usable by group admins or bot admins.
  - `/auth <group_id>` (sent from *anywhere* — PM to the bot, another chat, wherever) — bot admins only, authorizes the given group ID directly without needing to be in it.
  - Same pattern for `/unauth` / `/unauth <group_id>`.
  - New `/authlist` (admin-only) lists every currently authorized group.
  - Every authorize/unauthorize action now also posts to `LOG_CHANNEL` with who did it.
