"""Covers that fit the library's 2:3 cards: art of another shape set on a 2:3 canvas, and a poster for a game with no art.

The original image is never cut: it is set whole in the middle over a blurred, darkened copy of itself (what a streaming service
does with a banner in a tall slot). The poster is the game's name over a gradient of colours its name picks, so the same game
always gets the same one."""

from __future__ import annotations

import colorsys
import hashlib
import io

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

from logger.logger import log

WIDTH, HEIGHT = 600, 900
RATIO_MIN, RATIO_MAX = 0.55, 0.80  # width / height of a cover that is left as it is (SteamGridDB's are 2:3, IGDB's 3:4)
GENERATED = "generated"
_BLUR = 24
_DIM = 0.5
_BACKGROUND = (24, 26, 32)
_MARGIN = 48
_MAX_LINES = 6


def fits(width: int, height: int) -> bool:
    return height > 0 and RATIO_MIN <= width / height <= RATIO_MAX


def size_of(data: bytes) -> tuple[int, int] | None:
    try:
        with Image.open(io.BytesIO(data)) as image:
            return image.size
    except Exception:  # noqa: BLE001 - not an image Pillow reads
        return None


def _open(data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(data))
    image.load()  # an animated GIF is its first frame
    if image.mode in ("RGBA", "LA", "P"):  # transparency over the dark of the cards, not black
        flat = Image.new("RGB", image.size, _BACKGROUND)
        rgba = image.convert("RGBA")
        flat.paste(rgba, mask=rgba.getchannel("A"))
        return flat
    return image.convert("RGB")


def _jpeg(image: Image.Image) -> bytes:
    out = io.BytesIO()
    image.save(out, "JPEG", quality=88, optimize=True)
    return out.getvalue()


def fit(data: bytes) -> bytes | None:
    """`data` (any art) whole in the middle of a 2:3 canvas over a blurred copy of itself, as a JPEG; None when it will not decode."""
    try:
        art = _open(data)
    except Exception as e:  # noqa: BLE001 - a file that only starts like an image
        log.warning(f"Could not read an image to fit it into a cover: {e}")
        return None
    canvas = ImageOps.fit(art, (WIDTH, HEIGHT), Image.LANCZOS).filter(ImageFilter.GaussianBlur(_BLUR))
    canvas = ImageEnhance.Brightness(canvas).enhance(_DIM)
    scale = min(WIDTH / art.width, HEIGHT / art.height)
    size = (max(1, round(art.width * scale)), max(1, round(art.height * scale)))
    canvas.paste(art.resize(size, Image.LANCZOS), ((WIDTH - size[0]) // 2, (HEIGHT - size[1]) // 2))
    return _jpeg(canvas)


def _font(size: int) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    try:
        return ImageFont.load_default(size=size)
    except (TypeError, OSError):  # a Pillow without FreeType: the small bitmap font
        return ImageFont.load_default()


def _lines(draw: ImageDraw.ImageDraw, text: str, font, room: int) -> list[str]:
    lines: list[str] = []
    line = ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if line and draw.textlength(trial, font=font) > room:
            lines.append(line)
            line = word
        else:
            line = trial
    return [*lines, line] if line else lines


def poster(title: str) -> bytes:
    """A 2:3 cover with the game's name on a gradient the name decides, as a JPEG."""
    digest = hashlib.sha1(title.casefold().encode()).digest()
    hue = digest[0] / 255
    top = tuple(round(c * 255) for c in colorsys.hsv_to_rgb(hue, 0.55, 0.55))
    bottom = tuple(round(c * 255) for c in colorsys.hsv_to_rgb((hue + 0.08 + digest[1] / 255 * 0.12) % 1, 0.7, 0.22))
    image = Image.new("RGB", (WIDTH, HEIGHT))
    draw = ImageDraw.Draw(image)
    for y in range(HEIGHT):
        t = y / (HEIGHT - 1)
        draw.line([(0, y), (WIDTH, y)], fill=tuple(round(a + (b - a) * t) for a, b in zip(top, bottom, strict=True)))
    room = WIDTH - 2 * _MARGIN
    size = 76
    while True:  # the largest type that keeps the name within a few lines
        font = _font(size)
        lines = _lines(draw, title, font, room)
        if (len(lines) <= _MAX_LINES and all(draw.textlength(line, font=font) <= room for line in lines)) or size <= 26:
            break
        size -= 6
    lines = lines[:_MAX_LINES]
    line_height = round(size * 1.2)
    y = (HEIGHT - line_height * len(lines)) // 2
    for line in lines:
        x = (WIDTH - draw.textlength(line, font=font)) / 2
        draw.text((x + 2, y + 3), line, font=font, fill=(0, 0, 0))
        draw.text((x, y), line, font=font, fill=(255, 255, 255))
        y += line_height
    return _jpeg(image)
