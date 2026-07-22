"""
Builds the landscape poster shown with every search result:
TMDB backdrop + a dark gradient at the bottom + the title logo overlaid
(or, if TMDB has no logo for that title, the title drawn as text instead).

Two speed changes vs the first version:
1. Backdrop + logo are fetched *concurrently* (asyncio.gather) instead of
   one after the other — halves the network wait on a cache miss.
2. The actual PIL compositing (resizing, gradient, pasting) is CPU-bound
   and synchronous; it now runs in a worker thread (asyncio.to_thread)
   instead of directly in the event loop, so building one poster doesn't
   stall every other concurrent request the bot is handling.
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


async def build_full_poster(poster_path: str, tmdb_id=None, kind: str = "series", cache_suffix: str = "") -> bytes:
    """Returns the raw TMDB poster image as-is — no canvas compositing, no
    blur-fill, no logo overlay. Used when we'd rather show the actual
    poster art directly than force a portrait image onto our landscape
    canvas (e.g. a season with no single episode picked, so there's no
    per-episode still to use instead)."""
    if not poster_path:
        return None
    cache_key = f"{tmdb_id}{cache_suffix}_full" if tmdb_id is not None else None
    if cache_key is not None:
        cached = _cached(cache_key, kind)
        if cached is not None:
            return cached

    data = await _fetch_bytes(tmdb.poster_url(poster_path))
    if data and cache_key is not None:
        _store_cache(cache_key, kind, data)
    return data


async def build_poster(title: str, backdrop_path: str, logo_url: str = None, tmdb_id=None, kind: str = "movie",
                        backdrop_is_portrait: bool = False, cache_suffix: str = "") -> bytes:
    """Returns PNG bytes ready to send as a photo. Cached on disk per
    (kind, tmdb_id[, cache_suffix]) — a repeat search for the same title
    (and, for series, the same season) skips all of this.

    backdrop_is_portrait: set True when `backdrop_path` is actually a
    portrait poster (e.g. a TV season poster — TMDB has no per-season
    landscape backdrop) rather than a true landscape backdrop, so it gets
    blur-filled instead of harshly center-cropped.
    """
    cache_key = f"{tmdb_id}{cache_suffix}" if cache_suffix else tmdb_id
    if tmdb_id is not None:
        cached = _cached(cache_key, kind)
        if cached is not None:
            return cached

    img_url = tmdb.poster_url(backdrop_path) if backdrop_is_portrait else tmdb.backdrop_url(backdrop_path)
    backdrop_bytes, logo_bytes = await asyncio.gather(
        _fetch_bytes(img_url if backdrop_path else None),
        _fetch_bytes(logo_url),
    )

    data = await asyncio.to_thread(_composite_sync, title, backdrop_bytes, logo_bytes, backdrop_is_portrait)

    if tmdb_id is not None:
        _store_cache(cache_key, kind, data)
    return data
