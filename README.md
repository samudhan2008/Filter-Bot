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

---

## v4 fix

- **Deploy crash: `ImportError: cannot import name 'coroutine' from 'asyncio'`** — `motor==2.5.1` (required by the pinned `umongo==3.0.1`) calls `asyncio.coroutine`, which Python removed in 3.11. Koyeb's base image was on 3.11. Fixed by pinning the `Dockerfile` to `python:3.10-slim`, where that API still exists — no dependency changes needed. If you ever want to move to Python 3.11+, `motor`, `pymongo`, and `umongo` all need upgrading together (motor 3.x needs pymongo 4.x, which needs a newer umongo release) — that's a bigger jump, not a one-line pin.

---

## v5 fixes (found via real test transcripts)

### Fixed
- **`/index` produced zero response, every time** — two compounding bugs:
  1. A previous edit accidentally deleted the `@Client.on_message(...)` decorator above `index_cmd`, so it was never registered as a handler at all — no error, just silence.
  2. It also depended on `Client.ask()`, which — like `iter_messages()` before it — turned out not to be implemented on this pyrofork build. Rebuilt `/index` to not use `ask()`/`listen()` at all: it now tracks "waiting for a link from this admin" itself (a small in-memory pending-state dict with a TTL) and picks up their next message through a normal handler. You can also skip the prompt entirely with `/index <channel_link>` in one shot.
- **`/auth` inside a group always fell through to the "Usage:" message instead of authorizing** — and, worse, **all PM searches were silently getting blocked**. Root cause in both: `message.chat.type` is a Pyrogram *enum* (`ChatType.SUPERGROUP`), not the string `"supergroup"` — comparing it to a raw string is always false. That made `group_auth.py`'s `chat.type not in ("group", "supergroup")` always true (so `/auth` never recognized it was in a group), and made `search.py`'s `chat.type != "private"` always true too — which meant every private-chat search was incorrectly treated as needing group-authorization and got dropped. Both now compare against `pyrogram.enums.ChatType.GROUP` / `.SUPERGROUP` / `.PRIVATE` properly.
- **`/ban` and `/unban` usage messages silently lost their `<user_id>` placeholder** — sent as raw `<user_id>` under HTML parse mode, which Telegram's parser treats as an unrecognized tag and strips. Escaped to `&lt;user_id&gt;` so it actually displays.

### A note on the pyromod-style methods
This is the second time a method assumed to exist on `Client` (`iter_messages`, then `ask`) turned out to be missing on this specific pyrofork build, and both times the failure was *silent* — an AttributeError with no visible error to the user. If anything else in the bot ever goes quiet with no response and no log entry, that's the pattern to suspect first: check whether the method being called is actually a base Pyrogram method (safe) versus a pyromod/patched convenience method (worth testing directly, since these forks can vary on what they include).

---

## v6 addition: auto-index new channel posts

- **`CHANNELS`** env var (comma-separated channel IDs and/or usernames, e.g. `-1001111111111,-1002222222222,@somepublicchannel`) — any video, document, or audio file posted to these channels going forward is indexed automatically, in real time, no `/index` needed.
- The bot must be an **admin member** of each channel listed to receive its posts.
- `/index` is unchanged and still there for backfilling a channel's *existing* history — `CHANNELS` only covers new posts from here on. Typical setup: `/index` once to catch up, then list the channel in `CHANNELS` so everything after that is automatic.
- If `CHANNELS` is empty (default), this feature is simply off — no behavior change from before.

---

## v7 fix: wrong file being sent from search results

**Root cause:** once a result had more than 6 files, they got collapsed into one button per quality label (`720P (6 files)`), but that button could only actually link to *one* file_id — so tapping it sent an arbitrary pick from that quality bucket, not necessarily the one implied. That grouping is gone: every file now gets its own button with its real filename, always, no matter how many files there are.

**Also changed how tapping a button delivers the file**, closer to how VJ-FILTER-BOT does it: instead of a `t.me/bot?start=file_<id>` deep link (which re-enters through `/start` and re-looks-up the file), each button's `callback_data` is an exact index into that exact search result list, and the file is sent straight into whichever chat you searched in (group or PM) via `send_cached_media`. There's no reconstruction step in between — the button *is* the file, not a link to look it up again — so there's no path left for a mismatch. Result caches live for an hour (`_RESULTS_TTL`) and expire gracefully with a "please search again" if a button is tapped after that.

---

## v8 additions

### Captions now sent with the file
The caption stored at index time (from `Media.caption`) was already in the DB but never actually attached when a file was delivered. Both delivery paths — the callback-based buttons in search results and the legacy `/start?start=file_<id>` deep link — now send it back as the file's caption (HTML-parsed, truncated to Telegram's 1024-char caption limit if needed).

### Album/media-group indexing
Telegram represents "several files sent together" (an album) as multiple separate messages sharing a `media_group_id` — but **only one item in the group actually carries the caption text**; the rest arrive with `caption = None`. Both `/index` and the real-time `CHANNELS` auto-indexer now detect this (`media_group_id` present, own caption empty) and pull the caption from whichever item in the group has it, via `get_media_group()`, cached per group so it's only fetched once no matter how many files are in that album. Every file from an album post is now searchable by that shared caption text, not just the one message that technically carried it.

Also added `.gif`/animation files (`MessageMediaType.ANIMATION`) to both indexing paths — previously only video/audio/document were indexed.

---

## v9: pagination, recency sorting, substring-match fix

### Pagination
Search results were always capped at `MAX_RESULTS` (10) with no way to see the rest, even when the header said "Found 54 files". Every result message (both the plain file list and the poster+caption result) now gets a `◀️ Prev  📄 2/6  Next ▶️` row whenever there are more matches than fit on one page. Tapping Next/Prev edits the same message's buttons in place (no new message spam) and re-runs the same underlying search at the new offset — including correctly across the boundary between the "combined match" and "individual word" passes, so page numbers stay accurate no matter which pass a given page's results come from.

### Newest files first
Results were sorted by MongoDB's `$natural` order, which approximates insertion order but isn't a real guarantee, and doesn't reflect the file's actual original post date if a channel was ever backfilled out of order. Added two real fields to every indexed file: `file_date` (the message's original timestamp in its source channel) and `indexed_at` (when this bot saved it, as a fallback). Both `/index` and the real-time `CHANNELS` auto-indexer now capture `file_date`, and search results sort by it (newest first), falling back to `indexed_at` for any ties or for files indexed before this update (which won't have a `file_date` yet — they'll just sort to the end until re-indexed).

### Fixed: "Blast" matching "Blasters"/"Blastor"
The combined-word match regex only required a word *boundary before* each search word, not after — so "blast" matched as a prefix of "blasters" or "blastor". Added the trailing boundary; searching "Blast" now only matches files where "blast" appears as its own whole word (still matches fine inside names like `Blast.2023.1080p.mkv`, since normalization already turns punctuation into word boundaries).

---

## v10: speed + files-to-PM fix

### Speed
The biggest cost by far was invisible: **every single external call (TMDB, backend, poster images, shortlink) was opening a brand-new `aiohttp.ClientSession()`**, meaning a fresh TCP+TLS handshake per call instead of reusing a connection. That's now a single shared, connection-pooled session (`utils/http.py`) reused for the whole bot process. This alone should be the largest visible improvement.

On top of that:
- **Poster building** — backdrop and logo images are now fetched *concurrently* (`asyncio.gather`) instead of one after another, and the actual PIL compositing (CPU-bound, synchronous) now runs in a worker thread (`asyncio.to_thread`) instead of directly in the event loop — so building one poster no longer stalls every other concurrent search the bot is handling.
- **TMDB movie+TV search** now runs both lookups concurrently instead of sequentially.
- **Backend lookup** (`find_entry`) now runs *concurrently* with the poster build in `_show_result`, instead of waiting for the poster to finish first — its latency is now hidden behind the (longer) poster-building step rather than adding on top of it.

None of this changes MongoDB query cost (still governed by `USE_MONGO_TEXT_SEARCH` — worth turning on if the file collection is large and regex scans still feel slow after this).

### Fixed: files landing in the group instead of PM
Tapping a file button now always delivers into the **clicking user's own PM**, regardless of where the search happened — matching the intended behavior. If the bot can't message them (they haven't started it in PM yet), it shows an alert asking them to start the bot first rather than silently dropping the file into the group.

---

## v11: OOM crash during search

The Koyeb logs showed a clean startup followed by `Application exited with code 9` (SIGKILL — the OS/container runtime killing the process for using too much memory) right after a search was attempted. A few changes to cut peak memory during the poster-building step, which is the part of a search request that actually allocates meaningful memory:

- **TMDB logo images were being fetched at `original` size.** TMDB's "original" tier can be several MB and several thousand pixels on a side for some titles — once Pillow decompresses that into an in-memory bitmap, a single logo could balloon to tens of MB. Switched to `w500`, which is already larger than what gets composited onto the poster (logos are scaled down to ~700px max width on the 1280×720 canvas anyway), so there's no visible quality loss.
- **Poster compositing now explicitly closes each intermediate image buffer** (`with Image.open(...)`, `.close()` on the backdrop/logo/canvas) and forces a `gc.collect()` at the end of the compositing step, instead of waiting on Python's normal GC timing. On a memory-constrained instance, a burst of a few concurrent searches could otherwise leave several uncollected multi-MB bitmaps alive at once.
- **Pyrogram's worker pool dropped from a hardcoded 50 to a configurable `WORKERS` env var, default 8.** 50 concurrent update-handling workers is sized for much higher traffic than a small instance needs, and each one carries baseline overhead. Bump it via env if you're actually seeing update-processing backlog under real load (a different symptom — messages queuing up — not a crash).

**Worth checking on your end too:** if the instance is on Koyeb's smallest/free tier (often 512MB or less), Python + Pyrogram + MongoDB driver + Pillow's baseline footprint can already eat a meaningful chunk of that before any request-specific work happens. These changes reduce the *peak* usage during a search, but if crashes continue after this, the next step is either bumping the instance size a notch or watching Koyeb's memory graph during a search to see exactly where the spike lands.

---

## v12: image sizing, buffer cleanup, cancel/close buttons

### Image sizes
- **Backdrop**: `w1280` → `w780`. This is a real TMDB size (their backdrop tiers are `w300/w780/w1280/original`), noticeably lighter, and close to what was asked for.
- **Logo**: stayed at `w500`, *not* `w750` — worth knowing why: TMDB's logo tiers are only `w45/w92/w154/w185/w300/w500/original`. There's no `w750` (or anything between `w500` and `original`) for logos specifically, so `w500` is already the largest bounded option short of jumping back to the multi-MB `original` size we were trying to get away from.

### Buffer cleanup after sending
The poster's in-memory bytes (and the `BytesIO` wrapper around them) are now explicitly closed/deleted right after `send_photo` returns, instead of just falling out of scope for GC to notice eventually. **Note:** this only clears the one-off send buffer for that request — the on-disk poster cache (`POSTER_CACHE_DIR`, keyed by tmdb_id) is left alone, since that's a small, deliberate cache that's what makes repeat searches for the same title fast. If you actually meant the disk cache should be cleared after every send too, say so — that's a one-line change, just a different (and slower) tradeoff.

### Cancel button while checking TMDB
Right before the bot calls TMDB, it now sends a "🔎 Checking TMDB for a match…" status message with a **❌ Cancel** button — tappable by anyone, not just the requester or an admin. Cancelling stops the search from proceeding to a result once TMDB responds.

### Close button on results, admin-only
Every result message (both the plain file list and the poster+caption result, including every page while paginating) now ends with a **✖️ Close** button. Tapping it deletes the message, but only if the tapper is a bot admin (`ADMINS`) or — in a group — one of that group's own Telegram admins/creator. Anyone else gets a "only an admin can close this" alert and the message stays.

---

## v13: "This result has expired" fix — moved search state out of process memory

### Root cause
The pagination/file-delivery/disambiguation/cancel state (`_RESULTS`, `_PENDING`, `_CANCEL_FLAGS` in `plugins/search.py`, and `_PENDING_INDEX` in `plugins/index.py`) all lived in plain Python dicts, in the bot process's memory. That meant:
- **Any restart wipes everything instantly** — a redeploy, a crash, or one of the OOM kills from a couple rounds back. A button from 5 seconds before a restart would show "expired" even though nothing about it had actually gone stale.
- **Running more than one instance would break the same way** — an instance handling the callback wouldn't have the token an *different* instance created when it sent the result.

### Fix
All four moved into MongoDB (`database/statedb.py`), keyed the same way (by token), but now surviving restarts and shared correctly across instances:
- `search_results` — file-delivery + pagination state. TTL: `RESULTS_TTL` (default 3600s).
- `search_pending` — disambiguation picker candidates. TTL: `PENDING_TTL` (default 600s).
- `search_cancel` — "checking TMDB" cancel flags. TTL: 180s (short-lived by nature).
- `index_pending` — `/index`'s "waiting for the channel link" state. TTL: `INDEX_WAIT_TTL` (default 300s).

Each collection has a TTL index on `created_at`, so Mongo expires stale entries on its own — no more manual GC sweep needed in the bot code. If you still see "expired" after this, it's a genuinely stale token (older than its TTL), not a restart wiping things out.

---

## v14: series season/episode browsing + search reliability

### New: season → episode picker for series
Searching a series with many seasons/episodes used to dump every matching file into one flat list. Now, for any series search where you didn't already pin down a season (e.g. you searched "Breaking Bad" rather than "Breaking Bad S02E05"):

1. **Season picker** — if the show has more than one season, you get `Season 1 / Season 2 / ...` buttons. Skipped automatically if there's only one season.
2. **Poster switches to that season** — built from TMDB's season poster. Worth knowing: TMDB doesn't provide a per-season *landscape* backdrop, only a portrait poster, so hard-cropping it to our 16:9 canvas would chop off most of the artwork. Instead it's shown blur-filled — a softened, darkened, stretched copy of the poster fills the background, with the actual poster centered on top, uncropped. Falls back to the show's normal backdrop if a season has no poster of its own.
3. **Episode picker** — built from what's *actually indexed*, not a guessed episode count: it scans the DB for every episode number tagged for that title+season and shows real `E01 / E02 / ...` buttons, plus an "All episodes in this season" option. If nothing in that season has a recognizable episode number (e.g. it's stored as one full-season pack), it skips straight to showing everything for that season.
4. If you already typed a full `S02E05`-style query, all of this is skipped — same fast path as before, straight to the result.

This reuses (and required broadening) the episode-detection patterns from an earlier round to also catch `Season.1.EP1`, `S01 EP1`, `S01.EP.05`, and similar variants, in both filenames at index time and search queries — see the `_EPISODE_PATTERNS` note below.

### Broader filename convention detection
`utils/query.py`'s season/episode patterns (used both to parse a search query and to tag every indexed file) now handle flexible separators between the pieces — space, dot, underscore, dash, or nothing — and the `EP`/`Episode`/`E` abbreviations interchangeably. Confirmed matching: `S01E05`, `S01.EP.05`, `S01 EP1`, `S01EP01`, `Season.1.EP1`, `Season 2 Episode 10`, `1x05` — all resolve to the same (season, episode) pair now, however they're punctuated.

**Note:** episode tagging only happens at index time, so files already in the DB before this update keep whatever season/episode tagging they got under the old, narrower patterns. Re-running `/index` on a channel will re-tag everything with the broader rules (duplicates are skipped automatically, so this is safe to re-run).

### "Sometimes not giving results" — added a last-resort search pass
Root cause (best guess without a specific failing example to test against): the search's word-boundary matching is strict by design — it's what fixed "Blast" wrongly matching "Blasters" a few rounds back — but that same strictness means a query like "spider man" (two words) won't match a file actually named `SpiderMan.2021.mkv` (one word, no space), since neither "spider" nor "man" appears as its own whole word inside "spiderman". Added a third, last-resort pass that only runs when the normal two passes find nothing at all: it strips *all* spaces from both the query and the stored name and checks for a plain substring match. Requires the file to have been indexed (or re-indexed) after this update, since it depends on a new field computed at index time.

If searches are still coming up empty after this, the most useful next step is a specific example (the exact text searched + confirmation the file is actually indexed under a name you'd expect to match) — "sometimes" without a reproducible case is hard to chase further than the two concrete gaps above.

---

## v15: episode-specific art instead of the season poster

Better fix than the blur-fill workaround: TMDB episodes have their own real landscape image (`still_path` — an actual frame/still from the episode), unlike seasons which only have a portrait poster. So now:

- **A specific episode picked** → poster uses that episode's still (true landscape, no cropping tricks needed), and the caption gains the episode's name, air date, and a trimmed overview straight from TMDB.
- **"All episodes in this season" picked** → no single episode to show a still for, so it falls back to the season poster (still blur-filled, as before) — that case is unchanged.
- **An episode has no still available** (happens for some unaired/obscure episodes) → falls back to the season poster, then ultimately the show's own backdrop if neither exists.

Everything from the previous round (season poster fetch, blur-fill compositing, per-season disk cache keys) is still there and still used for the season-level case — this just adds a better-fitting option one level down, for when a specific episode is actually selected.

---

## v16: season poster shown as-is, no more blur-fill

For the "all episodes in this season" case (and as a fallback if a specific episode has no still available), the season poster is now sent **as the actual TMDB poster image, untouched** — no more forcing it onto the 1280×720 landscape canvas with a blurred background fill. Simpler, and just shows the real artwork.

`utils/poster.py` gained `build_full_poster()` for this — a plain fetch-and-cache with no canvas compositing, no gradient, no logo overlay. The blur-fill compositing mode in `build_poster()` still exists in the code (it's a generic capability), it's just not used by this flow anymore. The episode-still path from the previous round is unchanged — a specific episode still gets its own landscape still image composited with the logo, same as a movie result.

---

## v17: poster archive channel — fixes OOM risk on repeats AND cache durability

Great idea from you here — this solves two problems at once:

1. **OOM risk on repeat posters** — the disk cache helped, but only within one running instance; regenerating a poster still costs the same memory every time it's *not* already on that instance's disk.
2. **Disk cache doesn't survive a redeploy anyway** — most container platforms (including Koyeb) give you an ephemeral filesystem per instance, so the disk cache was quietly rebuilding itself after every restart regardless.

### New env var
`POSTER_CHANNEL` — a channel ID the bot archives every freshly-generated poster into. Optional; leave unset to keep the old disk-cache-only behavior unchanged. The bot needs to be an admin of this channel (same requirement as any indexed channel).

### How it works
Three tiers now, checked in order:
1. **Disk cache** (unchanged from before) — fastest, but instance-local and lost on restart.
2. **Telegram-channel cache** (new) — `database/postersdb.py` stores `{cache_key: telegram_file_id}` in MongoDB. On a hit, the bot sends that `file_id` straight to the user — **no download, no re-compositing, next to zero memory used**, and it survives restarts/redeploys since it's just a Mongo lookup + a Telegram-side file reference.
3. **Generate fresh** — same compositing pipeline as before (concurrent fetch, threaded PIL work, explicit buffer cleanup) — only reached the very first time a given poster/season/episode combination is needed. The result gets pushed to `POSTER_CHANNEL` and its `file_id` saved to Mongo before being sent to the user, so every subsequent request for that same poster hits tier 2 instead.

Cache keys are namespaced by kind (`movie_12345`, `series_12345_s2`, `series_12345_s2e5`, `series_12345_s2_full`, etc.) so a movie and an unrelated series can never collide even if they happen to share a TMDB ID (movie and TV IDs are separate spaces on TMDB).

**Nothing else changes for you** — the poster channel just quietly fills up with archived posters over time; there's no reason to look at it unless you're curious.

---

## v18: series returning nothing — root cause + full logic audit

### The main bug: series search forced a season filter onto untagged content
Movies never pass a `season`/`episode` filter through the DB search. Series, since the season/episode picker feature was added, always did — once TMDB confirmed a show has seasons, the bot picked (or asked for) a season number and then hard-filtered the DB search to `season_number: <that number>`. If a show's indexed files were never tagged with a recognizable season marker (different naming convention, or none at all — genuinely common), that filter silently excluded every one of them, and every season/episode you picked dead-ended into "no files found." Movies, never being filtered this way, always worked. This is very likely what you were seeing.

**Fix, two layers:**
1. **`filesdb.has_season_data(query)`** — before entering the season/episode picker at all, the bot now checks whether the DB actually has *any* season-tagged file for that title. If not, it skips the picker entirely and shows a flat, movie-style result instead — same behavior series always should have had when there's no season structure to browse.
2. **Defensive fallback inside the episode picker** — even when some season data exists, if the *specific* season chosen (via TMDB's list or its own default) turns out to have zero matching files in the DB (numbering mismatch between TMDB and however the files happened to get tagged), it now drops the season filter and shows everything for the title, rather than a guaranteed-empty result.

### Two more real bugs found in the same full pass (not related to the above, but definitely bugs)
Same root cause as the `chat.type` issue from a few rounds back — comparing a Pyrogram **enum** to a plain string, which is always `False`/always `True` depending on direction, never actually checking anything:

- **`plugins/group_auth.py`** — `member.status in ("administrator", "creator")` never matched, since `member.status` is a `ChatMemberStatus` enum. This meant **real Telegram group admins (who aren't also in the bot's global `ADMINS` list) have never actually been able to run `/auth`/`/unauth` in their own group, or use the "Close" button on results** — only bot-wide admins could. Fixed to compare against `ChatMemberStatus.ADMINISTRATOR` / `.OWNER`.
- **`plugins/force_sub.py`** — `member.status not in ("left", "kicked")` was always `True` regardless of actual status, silently disabling that check. The primary "are they subscribed" signal (catching the `UserNotParticipant` exception) still worked, but this secondary check — meant to catch a member object explicitly reporting `left`/`banned` instead of raising — did nothing. Fixed to compare against `ChatMemberStatus.LEFT` / `.BANNED`.

### Audit scope
Went through every plugin and database module end-to-end for this pass: `search.py`, `index.py`, `auto_index.py`, `group_auth.py`, `force_sub.py`, `admin.py`, `start.py`, `bot.py`, `filesdb.py`, `statedb.py`, `backend.py`, `postersdb.py`, and the `utils/` helpers — specifically re-checking pagination offset math, cache-key construction, and every remaining comparison against a Pyrogram-typed attribute for the same enum-vs-string mistake. No other instances of that pattern remained after the two fixes above.

---

## v19: backdrop quality restored

`backdrop_url()` back to `w1280` (from `w780`). That downgrade only made sense when every search regenerated the poster from scratch — now that `POSTER_CHANNEL` means a given poster/season/episode is only ever composited once and reused as a `file_id` after that, the memory tradeoff no longer applies, so there's no reason not to use the sharper source image. This also improves episode stills, since they go through the same `backdrop_url()` path.

Logos stay at `w500` — as covered earlier, TMDB simply doesn't offer anything between `w500` and the much larger `original` for that specific image type, so there's no equivalent "step up" available there.

---

## v20: deep-link mode restored + anti-bypass shortlink, shared Mongo client, speed, stalling messages

### Deep-link file delivery is back
File buttons are plain `t.me/<bot>?start=file_<id>` deep links again (removed the callback-based delivery from a couple rounds back). No new env var for this — it's just how file delivery works now. This is actually strictly safe against the two bugs that callback delivery was originally built to fix:
- **One file per link, no ambiguity** — a deep link always maps to exactly one `file_id`, so there's no "wrong file sent" risk.
- **Always opens PM** — `t.me/bot?start=...` links are handled by Telegram itself and always open a private chat with the bot, never post into whatever group the button was tapped in.

### Anti-bypass shortlink verification — controlled entirely by `SHORTLINK_MODE`
No new toggle needed, as requested — your existing `SHORTLINK_MODE` env var does double duty:
- **`SHORTLINK_MODE=False` (default)** — file deep links work directly, no shortening, no verification. Exactly like a plain deep-link bot.
- **`SHORTLINK_MODE=True`** — file deep links get shortened, *and* `deliver_file` (in `plugins/start.py`) refuses to hand over any file unless the requesting user currently has a valid verification. This is the actual anti-bypass mechanism, and it's worth understanding honestly: a raw, unshortened deep link can always be copied and shared — there's no way for a plain URL button to check anything before Telegram opens it. So instead of trying to protect the *link*, the bot protects the *file*: it checks verification status server-side at delivery time, regardless of how someone arrived at that link. Verification itself (`database/verifydb.py`) is a single-use, per-user token — created fresh each time it's needed, bound to that specific Telegram user_id, and only obtainable by completing one shortlink round trip. Once verified, that user can grab files freely for `VERIFY_VALID_HOURS` (default 24) before needing to verify again. Sharing a "verified" status doesn't work either, since it's tied to a real Telegram user_id, not something transferable.
- New env vars (only relevant when `SHORTLINK_MODE=True`): `VERIFY_VALID_HOURS` (default `24`), `VERIFY_TOKEN_TTL` (default `600` seconds to complete the shortlink click before the token expires).

### Speed
- **Consolidated to a single shared MongoDB client** (`database/mongo.py`) — `filesdb`, `usersdb`, `statedb`, `postersdb`, and the new `verifydb` were each opening their *own* `AsyncIOMotorClient` (separate connection pool, separate heartbeat threads) to the same database. Real, if invisible, overhead; now there's exactly one.
- **Removed a redundant DB round-trip per search** — `on_search_text` used to run a `max_results=1` pre-check, then (on the "no TMDB match" path) run the *same* query again at full result count. Now it fetches at full count once and reuses that data.
- **`has_season_data` is now cached** (5 min TTL) — it's checked on every series search since a couple rounds ago, so repeated searches for the same show don't re-run the same DB check.

### Stalling / status messages
- `utils/texts.py` gained rotating message pools: `wait_message()` ("⏳ Please wait…", "🔄 Processing…", etc.), `fetching_toast()` (used for the quick toast when tapping a disambiguation/season/episode button), and `not_found()` — several friendlier phrasings for "no files found", including a casual "Sry, pls check the spelling and try again" variant, picked at random so repeat misses don't feel like the same canned error every time.
- The "🔎 Checking TMDB for a match…" status message no longer just gets deleted once TMDB responds — on the direct-to-result path (a single, unambiguous match), it's now *edited* into a rotating "please wait / processing" message and only cleared right before the actual poster+file result is sent, so there's continuous feedback through what's usually the slowest part of a search (TMDB lookup + poster generation) instead of a gap in between. For paths that show a picker next (disambiguation, season/episode) or come up empty, the status message is just cleared as before, since new content follows immediately anyway.

---

## v21: two real bugs from live testing

### Bug 1: episode click → poster archived, but no response to the user
Root cause: `ctx["extra_buttons"]` (the "🌐 Watch on SC Files" button) was being stored as raw `InlineKeyboardButton` objects inside the result entry saved to Mongo (`statedb.store_results`). Pyrogram objects aren't BSON-serializable, so that call threw every time a website match was found — which happened *after* the poster had already been generated and archived to `POSTER_CHANNEL`, and the exception was never caught, so Pyrogram swallowed it with no visible error and no reply to the user. This is exactly the symptom described: poster shows up in the channel, nothing reaches the person who clicked.

Fixed by storing `extra_buttons` as plain `{"text":..., "url":...}` dicts (BSON-safe) and reconstructing real `InlineKeyboardButton`s only when building the markup for display. Also fixed a related inconsistency while in there: the button was using the *un*-shortened website link while the caption text next to it showed the shortened one — both now use the same (shortened, when `SHORTLINK_MODE` is on) link.

**This means any result where a "Watch on SC Files" button appeared was broken before this fix** — not just series specifically; it likely happened to surface first on a series search purely because that title had a website match and the movie you'd tested earlier didn't.

### Bug 2: "and" causing unrelated false matches (e.g. "Pritam and Pedro" → also "Cousins and Kalyanams")
Two compounding issues in the search's word-level fallback pass:
1. It was running (to "top up" a partial page) even when the exact/combined match already had plenty of results — so an unrelated file sharing just one word could get pulled in alongside genuinely correct matches.
2. It treated every word in the query as equally meaningful, including connector words like "and", which appear in huge numbers of unrelated titles and provide no real signal.

Fixed both: the word-level fallback now **only ever runs when the exact/combined match finds absolutely nothing** — if there's an exact match, that's the *only* thing shown, full stop. And when the fallback does run (a genuine zero-match case), it now excludes a stopword list (`and, the, a, an, of, in, on, at, to, for, with, is, it, as, by, or, from, this, that`) so a query like "X and Y" can't accidentally match on "and" alone.

Verified directly: searching "Pritam and Pedro" now matches `Pritam.and.Pedro.mkv` and correctly rejects `Cousins.and.Kalyanams.mkv`, both under the exact-match pass and (as a backup) under the corrected fallback pass.

---

## v22: cookie-continuity anti-bypass + admin dashboard

A big upgrade to the shortlink verification system from last round, plus a new companion project: a separate Vercel frontend (`scfiles_verify_frontend/`, its own zip/repo — not part of this bot's codebase or Docker image) that does two things: runs a proper cookie-continuity check for shortlink verification, and hosts a password-protected admin dashboard.

### Anti-bypass, upgraded: cookie continuity instead of just "verified recently"
The previous round's verification only checked "does this user currently have a valid verified-until timestamp" — real, but it couldn't tell whether *this specific attempt* actually walked through the shortlink, only whether they'd done so at some point in the last N hours. The new flow proves continuity across the shortlink hop itself:

1. `deliver_file` creates a session and sends a **"✅ Verify Now"** button pointing at the frontend's `/go/<session>` page (not the shortlink directly).
2. `/go` sets a random, `HttpOnly` cookie in the browser, registers it with the bot, then redirects to the actual (pre-shortened) shortlink.
3. After the shortlink's ads/countdown, the user lands on `/finish/<session>` — the shortlink's real destination.
4. `/finish` reads the cookie back and sends it to the bot for comparison. Match → verified, redirected back into Telegram. Mismatch (missing/wrong cookie — someone jumped straight to `/finish`, skipping the actual shortlink) → flagged as a bypass attempt, on both the web page and as a Telegram warning message to that user.
5. One-time use — the session record is deleted the instant it's checked, success or failure either way.

Same honest caveat as always: this stops casual bypass (copy-pasting the final link, reusing an old one) but not a fully scripted/automated browser session replaying a captured cookie — no cookie/token scheme fully prevents that.

**Bot-side additions:**
- `database/verifydb.py` rewritten around sessions instead of simple tokens.
- `utils/frontend_api.py` — new HTTP API (`/api/verify/set-cookie`, `/api/verify/confirm`) the frontend calls, secured by `FRONTEND_API_SECRET`.
- `plugins/start.py`'s `deliver_file` now builds the `/go` link (with a pre-shortened `/finish` link baked in) instead of a bare shortened deep link.

**New env vars:** `FRONTEND_URL`, `FRONTEND_API_SECRET`, `VERIFY_SESSION_TTL` (default 900s to complete the whole round trip). `VERIFY_VALID_HOURS` (default 24) carries over from before. If `FRONTEND_URL`/`FRONTEND_API_SECRET` aren't set, `SHORTLINK_MODE` still shortens links but skips verification entirely (fails open, logged) rather than breaking file delivery.

### New: `/admin` dashboard on the frontend
A password-protected page for bot management without opening Telegram — stats, broadcast, ban/unban, group authorization, triggering `/index` on a channel, the website gap check, and a log viewer. Full details in the frontend project's own README. Deliberately secured with its **own** secret (`ADMIN_API_SECRET`), separate from the verify flow's `FRONTEND_API_SECRET` — the admin API can ban users and broadcast to everyone, meaningfully higher stakes than a cookie check, so a leak of one credential doesn't hand over the other.

**New bot-side file:** `utils/admin_api.py`, registered alongside the verify API in `bot.py`.

### The frontend project
Delivered separately (`scfiles_verify_frontend/`) since it deploys to Vercel, not this bot's Docker image. Built and verified with a real `next build` (not just a syntax check) — all 9 routes compile cleanly, including the JSX-heavy admin dashboard. Also bumped to Next.js `14.2.35` (the latest patched 14.x release; the version initially scaffolded, 14.2.5, had a known security advisory). Full setup/deployment instructions are in that project's own `README.md`.
