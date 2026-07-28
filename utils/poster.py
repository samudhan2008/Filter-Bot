"""
Builds the landscape poster shown with every search result:
TMDB backdrop + a dark gradient at the bottom + the title logo overlaid
(or, if TMDB has no logo for that title, the title drawn as text instead).

Cache tiers, in order:
1. Telegram-channel-backed Mongo cache (POSTER_CHANNEL, database/postersdb.py)
   — once a poster's been generated, its file_id is archived and reused
   directly (photo=file_id) with no bytes touched at all on a hit. Survives
   restarts. Optional — off if POSTER_CHANNEL isn't set. Self-healing: each
   cached entry remembers whether it actually had a real backdrop/logo or
   fell back to a plain background/drawn title because TMDB didn't have
   one yet — if TMDB has since added what was missing, this regenerates
   and edits the existing archived message in place instead of serving the
   stale fallback forever.
2. On-disk cache (POSTER_CACHE_DIR) — fast, but local to this instance and
   lost on restart/redeploy. Skipped when self-healing kicks in, so it
   can't serve a stale incomplete poster either.
3. Generate fresh — backdrop + logo fetched concurrently, then composited
   in a worker thread (asyncio.to_thread) so it doesn't block the event
   loop for other concurrent requests.
"""

import asyncio
import io
import logging
import os
import time

import aiohttp
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

import info
from utils import tmdb
from utils.http import get_session

logger = logging.getLogger(__name__)

os.makedirs(info.POSTER_CACHE_DIR, exist_ok=True)

CANVAS_SIZE = (1280, 720)
FONT_PATH_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]


def _cache_path(tmdb_id, kind: str) -> str:
    return os.path.join(info.POSTER_CACHE_DIR, f"{kind}_{tmdb_id}.png")


def _cached(tmdb_id, kind: str):
    path = _cache_path(tmdb_id, kind)
    if not os.path.exists(path):
        return None
    if time.time() - os.path.getmtime(path) > info.POSTER_CACHE_TTL:
        return None  # stale — TMDB logo/backdrop may have changed
    try:
        with open(path, 'rb') as f:
            return f.read()
    except Exception:
        return None


def _store_cache(tmdb_id, kind: str, data: bytes):
    try:
        with open(_cache_path(tmdb_id, kind), 'wb') as f:
            f.write(data)
    except Exception as e:
        logger.warning(f"Could not write poster cache: {e}")


def _load_font(size):
    for path in FONT_PATH_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


async def _fetch_bytes(url: str):
    if not url:
        return None
    try:
        session = await get_session()
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                return None
            return await resp.read()
    except Exception as e:
        logger.warning(f"Failed to fetch image {url}: {e}")
        return None


def _fit_backdrop(img: Image.Image, blurred_fill: bool = False) -> Image.Image:
    img = img.convert("RGB")
    if not blurred_fill:
        return ImageOps.fit(img, CANVAS_SIZE, method=Image.LANCZOS)

    # Portrait source (a season poster, not a true landscape backdrop) —
    # hard-cropping this to a 16:9 canvas would chop off most of the art.
    # Instead: blurred, darkened, stretched-to-fill background, with the
    # actual poster placed sharp and uncropped, centered, on top.
    bg = ImageOps.fit(img, CANVAS_SIZE, method=Image.LANCZOS).filter(ImageFilter.GaussianBlur(30))
    dark = Image.new("RGB", CANVAS_SIZE, (0, 0, 0))
    bg = Image.blend(bg, dark, 0.35)
    dark.close()

    ratio = CANVAS_SIZE[1] / img.height
    new_w = min(int(img.width * ratio), CANVAS_SIZE[0])
    sharp = img.resize((new_w, CANVAS_SIZE[1]), Image.LANCZOS)
    x = (CANVAS_SIZE[0] - new_w) // 2
    bg.paste(sharp, (x, 0))
    sharp.close()
    return bg


def _add_gradient(base: Image.Image) -> Image.Image:
    w, h = base.size
    gradient = Image.new("L", (1, h), 0)
    for y in range(h):
        # darker towards the bottom, where the title/logo sits
        alpha = int(255 * max(0, (y - h * 0.35) / (h * 0.65)) ** 1.3)
        gradient.putpixel((0, y), min(alpha, 235))
    gradient = gradient.resize((w, h))
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 255))
    overlay.putalpha(gradient)
    return Image.alpha_composite(base.convert("RGBA"), overlay)


def _draw_title_text(canvas: Image.Image, title: str) -> Image.Image:
    draw = ImageDraw.Draw(canvas)
    font = _load_font(72)
    x, y = 60, CANVAS_SIZE[1] - 140
    draw.text((x + 3, y + 3), title, font=font, fill=(0, 0, 0, 180))
    draw.text((x, y), title, font=font, fill=(255, 255, 255, 255))
    return canvas


def _composite_sync(title: str, backdrop_bytes, logo_bytes, backdrop_is_portrait: bool = False) -> bytes:
    """The actual CPU-bound work — run via asyncio.to_thread so it doesn't
    block the event loop. Explicitly closes/frees each intermediate image
    buffer rather than relying on GC timing, since a burst of concurrent
    searches can otherwise pile up several uncollected multi-MB bitmaps at
    once on a memory-constrained instance."""
    base = None
    if backdrop_bytes:
        try:
            with Image.open(io.BytesIO(backdrop_bytes)) as src:
                base = _fit_backdrop(src, blurred_fill=backdrop_is_portrait)
        except Exception:
            base = None
    if base is None:
        base = Image.new("RGB", CANVAS_SIZE, (20, 20, 20))

    canvas = _add_gradient(base)
    base.close()

    if logo_bytes:
        try:
            with Image.open(io.BytesIO(logo_bytes)) as logo_src:
                logo = logo_src.convert("RGBA")
            max_w, max_h = int(CANVAS_SIZE[0] * 0.55), int(CANVAS_SIZE[1] * 0.32)
            ratio = min(max_w / logo.width, max_h / logo.height, 1.0)
            logo = logo.resize((int(logo.width * ratio), int(logo.height * ratio)), Image.LANCZOS)
            pos = (60, CANVAS_SIZE[1] - logo.height - 60)
            canvas.paste(logo, pos, logo)
            logo.close()
        except Exception as e:
            logger.warning(f"Logo composite failed, falling back to text: {e}")
            canvas = _draw_title_text(canvas, title)
    else:
        canvas = _draw_title_text(canvas, title)

    buf = io.BytesIO()
    canvas.convert("RGB").save(buf, format="PNG")
    canvas.close()
    buf.seek(0)
    data = buf.read()
    buf.close()

    import gc
    gc.collect()
    return data


async def _resolve_with_channel_cache(bot, cache_key, kind: str, generate_coro,
                                       has_backdrop_now: bool = True, has_logo_now: bool = True):
    """
    3-tier lookup: Telegram-channel-backed Mongo cache (persistent,
    near-zero cost on hit, and self-healing — see below) -> on-disk cache
    (fast, this instance only) -> generate fresh (the only tier that
    actually costs memory/CPU/network).

    Self-healing: every cached poster remembers whether it actually had a
    real backdrop/logo when it was built, or fell back to a plain
    background / drawn-text title because TMDB didn't have one *yet*.
    TMDB entries get artwork added over time — a title searched the day it
    was announced might have neither, then gain a backdrop and logo weeks
    later. Without this check, a poster generated during that gap would
    stay stuck at the fallback version forever. So: if the cache says
    something was missing, and the caller's current has_backdrop_now/
    has_logo_now says it's available *now*, this regenerates and — instead
    of posting a new message to POSTER_CHANNEL — edits the existing
    cached message in place, updating its stored file_id.

    Returns (ref, is_file_id):
      is_file_id=True  -> ref is a Telegram file_id string; send via
                          `photo=ref` directly, no bytes touched at all.
      is_file_id=False -> ref is raw PNG bytes; wrap in BytesIO to send.

    generate_coro: async callable, no args, returning fresh PNG bytes (or
    None on failure).
    """
    mongo_doc = None
    mongo_key = f"{kind}_{cache_key}" if cache_key is not None else None  # namespaced so a movie and a series can't collide on the same tmdb_id

    if mongo_key is not None and info.POSTER_CHANNEL and bot is not None:
        from database import postersdb
        mongo_doc = await postersdb.get_poster_doc(mongo_key)

    needs_heal = False
    if mongo_doc:
        # Records from before this feature existed have neither field at
        # all — treat "unknown" the same as "was missing" so an existing
        # incomplete poster (built before self-healing existed) actually
        # gets re-checked the next time it's searched, not just ones
        # cached going forward. This means every pre-upgrade cached poster
        # gets one re-verification pass on its next hit — a bounded,
        # one-time cost, not a recurring one, since the doc gets the real
        # fields filled in immediately after.
        was_missing_backdrop = not mongo_doc.get('has_backdrop', False)
        was_missing_logo = not mongo_doc.get('has_logo', False)
        if (was_missing_backdrop and has_backdrop_now) or (was_missing_logo and has_logo_now):
            needs_heal = True
            logger.info(f"Self-healing poster {mongo_key}: backdrop {was_missing_backdrop}->{has_backdrop_now}, "
                        f"logo {was_missing_logo}->{has_logo_now}")

    if mongo_doc and not needs_heal:
        return mongo_doc['file_id'], True

    if cache_key is not None and not needs_heal:
        cached = _cached(cache_key, kind)
        if cached is not None:
            return cached, False

    data = await generate_coro()
    if not data:
        return None, False

    if cache_key is not None:
        _store_cache(cache_key, kind, data)

        if info.POSTER_CHANNEL and bot is not None:
            from database import postersdb
            from pyrogram.types import InputMediaPhoto

            can_edit = needs_heal and mongo_doc and mongo_doc.get('message_id')
            msg = None

            if can_edit:
                try:
                    buf = io.BytesIO(data)
                    buf.name = "poster.png"
                    msg = await bot.edit_message_media(
                        chat_id=info.POSTER_CHANNEL,
                        message_id=mongo_doc['message_id'],
                        media=InputMediaPhoto(buf, caption=f"🗄 {mongo_key} (healed — artwork updated)"),
                    )
                    buf.close()
                    logger.info(f"Healed poster {mongo_key}: edited existing message {mongo_doc['message_id']} in place.")
                except Exception as e:
                    logger.warning(f"edit_message_media failed for {mongo_key} (message {mongo_doc['message_id']}), "
                                    f"falling back to a new message: {e}")
                    msg = None

            if msg is None:
                if needs_heal and mongo_doc and not mongo_doc.get('message_id'):
                    logger.info(f"Healing {mongo_key}: no message_id on record (cached before self-healing "
                                f"existed) — posting a fresh archive message this one time. From its next "
                                f"update onward this will edit in place instead.")
                try:
                    buf = io.BytesIO(data)
                    buf.name = "poster.png"
                    msg = await bot.send_photo(info.POSTER_CHANNEL, photo=buf, caption=f"🗄 {mongo_key}")
                    buf.close()
                except Exception as e:
                    logger.warning(f"Could not archive poster {mongo_key} to POSTER_CHANNEL: {e}")

            if msg and msg.photo:
                try:
                    await postersdb.save_poster(mongo_key, msg.photo.file_id, msg.id, kind,
                                                 has_backdrop_now, has_logo_now)
                except Exception as e:
                    logger.warning(f"Archived/edited poster {mongo_key} in Telegram but failed to save the "
                                    f"record to Mongo — it'll be treated as needing another look next time: {e}")

    return data, False


async def build_full_poster(bot, poster_path: str, tmdb_id=None, kind: str = "series", cache_suffix: str = ""):
    """Returns (ref, is_file_id) — see _resolve_with_channel_cache. The raw
    TMDB poster image as-is when generated fresh: no canvas compositing, no
    blur-fill, no logo overlay. Used when we'd rather show the actual
    poster art directly than force a portrait image onto our landscape
    canvas (e.g. a season with no single episode picked, so there's no
    per-episode still to use instead)."""
    if not poster_path:
        return None, False
    cache_key = f"{tmdb_id}{cache_suffix}_full" if tmdb_id is not None else None

    async def _generate():
        return await _fetch_bytes(tmdb.poster_url(poster_path))

    # No logo concept in this mode — only backdrop (i.e. the poster itself)
    # completeness is tracked.
    return await _resolve_with_channel_cache(bot, cache_key, kind, _generate, has_backdrop_now=True, has_logo_now=True)


async def build_poster(bot, title: str, backdrop_path: str, logo_url: str = None, tmdb_id=None, kind: str = "movie",
                        backdrop_is_portrait: bool = False, cache_suffix: str = ""):
    """Returns (ref, is_file_id) — see _resolve_with_channel_cache.

    backdrop_is_portrait: set True when `backdrop_path` is actually a
    portrait poster rather than a true landscape backdrop, so it gets
    blur-filled instead of harshly center-cropped when generated fresh.
    """
    cache_key = f"{tmdb_id}{cache_suffix}" if tmdb_id is not None else None

    async def _generate():
        img_url = tmdb.poster_url(backdrop_path) if backdrop_is_portrait else tmdb.backdrop_url(backdrop_path)
        backdrop_bytes, logo_bytes = await asyncio.gather(
            _fetch_bytes(img_url if backdrop_path else None),
            _fetch_bytes(logo_url),
        )
        return await asyncio.to_thread(_composite_sync, title, backdrop_bytes, logo_bytes, backdrop_is_portrait)

    return await _resolve_with_channel_cache(
        bot, cache_key, kind, _generate,
        has_backdrop_now=bool(backdrop_path), has_logo_now=bool(logo_url),
    )
