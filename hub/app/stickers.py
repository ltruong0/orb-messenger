"""Sticker rendering (decisions 5): emoji -> 160x160 RGBA PNG via Noto Color Emoji,
or an uploaded image fitted into the same box."""

from __future__ import annotations

import hashlib
import io
import re
from pathlib import Path

import regex
from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError

STICKER_SIZE = 160
EMOJI_INLINE_SIZE = 40  # emoji inside message text on the orb (text is 30-44 px)
EMOJI_RENDER_SIZE = 109  # the only size Pillow renders NotoColorEmoji at (CBDT bitmaps)

SEED_STICKERS: list[tuple[str, str, str]] = [  # (emoji, key, name)
    ("💜", "purple-heart", "Purple heart"),
    ("🥰", "heart-eyes", "Heart eyes"),
    ("☕", "coffee", "Coffee"),
    ("🌞", "sun", "Sun"),
    ("🫶", "heart-hands", "Heart hands"),
    ("✨", "sparkles", "Sparkles"),
    ("🐱", "cat", "Cat"),
]


class BadSticker(ValueError):
    pass


def slugify(name: str) -> str:
    key = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:32].strip("-")
    if not key:
        raise BadSticker("name must contain at least one letter or digit")
    return key


def is_single_grapheme(text: str) -> bool:
    """Exactly one user-perceived character (so flags, skin tones and ZWJ sequences pass)."""
    clusters = regex.findall(r"\X", text)
    return len(clusters) == 1 and not text.isascii()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _fit_into_box(img: Image.Image, size: int = STICKER_SIZE) -> Image.Image:
    img = img.convert("RGBA")
    bbox = img.getchannel("A").getbbox()
    if bbox is None:
        raise BadSticker("image is fully transparent")
    img = img.crop(bbox)
    scale = size / max(img.width, img.height)  # up or down: the 109 px glyph becomes 160 px
    img = img.resize(
        (max(1, round(img.width * scale)), max(1, round(img.height * scale))),
        Image.Resampling.LANCZOS,
    )
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(img, ((size - img.width) // 2, (size - img.height) // 2), img)
    return canvas


def _to_png(img: Image.Image) -> bytes:
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()


def render_emoji(
    emoji: str, font_path: Path, size: int = STICKER_SIZE, single: bool = True
) -> bytes:
    """Render one emoji at 109 px with embedded colour, trim, fit into size x size."""
    emoji = emoji.strip()
    if single and not is_single_grapheme(emoji):
        raise BadSticker("emoji must be a single character (one grapheme cluster)")
    font = ImageFont.truetype(str(font_path), EMOJI_RENDER_SIZE)
    canvas = Image.new("RGBA", (EMOJI_RENDER_SIZE * 2, EMOJI_RENDER_SIZE * 2), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    draw.text(
        (EMOJI_RENDER_SIZE // 2, EMOJI_RENDER_SIZE // 2), emoji, font=font, embedded_color=True
    )
    if canvas.getchannel("A").getbbox() is None:
        raise BadSticker("the emoji font has no glyph for that character")
    return _to_png(_fit_into_box(canvas, size))


def render_upload(data: bytes) -> bytes:
    if len(data) > 5 * 1024 * 1024:
        raise BadSticker("sticker image larger than 5 MB")
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError) as e:
        raise BadSticker("not an image") from e
    return _to_png(_fit_into_box(img))
