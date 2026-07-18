"""
Builds the landscape poster shown with every search result:
TMDB backdrop + a dark gradient at the bottom + the title logo overlaid
(or, if TMDB has no logo for that title, the title drawn as text instead).
"""

import io
import logging

import aiohttp
from PIL import Image, ImageDraw, ImageFont, ImageOps

import info
from utils import tmdb

logger = logging.getLogger(__name__)

CANVAS_SIZE = (1280, 720)
FONT_PATH_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]


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
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    return None
                return await resp.read()
    except Exception as e:
        logger.warning(f"Failed to fetch image {url}: {e}")
        return None


def _fit_backdrop(img: Image.Image) -> Image.Image:
    return ImageOps.fit(img.convert("RGB"), CANVAS_SIZE, method=Image.LANCZOS)


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


async def build_poster(title: str, backdrop_path: str, logo_url: str = None) -> bytes:
    """Returns PNG bytes ready to send as a photo."""
    backdrop_bytes = await _fetch_bytes(tmdb.backdrop_url(backdrop_path)) if backdrop_path else None

    if backdrop_bytes:
        try:
            base = _fit_backdrop(Image.open(io.BytesIO(backdrop_bytes)))
        except Exception:
            base = Image.new("RGB", CANVAS_SIZE, (20, 20, 20))
    else:
        base = Image.new("RGB", CANVAS_SIZE, (20, 20, 20))

    canvas = _add_gradient(base)

    logo_bytes = await _fetch_bytes(logo_url) if logo_url else None
    if logo_bytes:
        try:
            logo = Image.open(io.BytesIO(logo_bytes)).convert("RGBA")
            max_w, max_h = int(CANVAS_SIZE[0] * 0.55), int(CANVAS_SIZE[1] * 0.32)
            ratio = min(max_w / logo.width, max_h / logo.height, 1.0)
            logo = logo.resize((int(logo.width * ratio), int(logo.height * ratio)), Image.LANCZOS)
            pos = (60, CANVAS_SIZE[1] - logo.height - 60)
            canvas.paste(logo, pos, logo)
        except Exception as e:
            logger.warning(f"Logo composite failed, falling back to text: {e}")
            canvas = _draw_title_text(canvas, title)
    else:
        canvas = _draw_title_text(canvas, title)

    buf = io.BytesIO()
    canvas.convert("RGB").save(buf, format="PNG")
    buf.seek(0)
    return buf.read()


def _draw_title_text(canvas: Image.Image, title: str) -> Image.Image:
    draw = ImageDraw.Draw(canvas)
    font = _load_font(72)
    x, y = 60, CANVAS_SIZE[1] - 140
    # simple shadow for legibility
    draw.text((x + 3, y + 3), title, font=font, fill=(0, 0, 0, 180))
    draw.text((x, y), title, font=font, fill=(255, 255, 255, 255))
    return canvas
