"""Emoji -> PNG rendering and the sticker input rules."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from app.stickers import BadSticker, is_single_grapheme, render_emoji, render_upload, slugify
from PIL import Image


def test_single_grapheme_rules():
    assert is_single_grapheme("💜")
    assert is_single_grapheme("🫶")
    assert is_single_grapheme("👍🏽")  # skin tone modifier
    assert is_single_grapheme("👨‍👩‍👧")  # ZWJ sequence
    assert is_single_grapheme("🇳🇿")  # flag = two regional indicators
    assert not is_single_grapheme("💜💜")
    assert not is_single_grapheme("a")
    assert not is_single_grapheme("")
    assert not is_single_grapheme("hi")


def test_slugify():
    assert slugify("Purple heart") == "purple-heart"
    assert slugify("  Heart   Eyes!! ") == "heart-eyes"
    assert slugify("Café ☕") == "caf"
    with pytest.raises(BadSticker):
        slugify("☕")


def test_render_emoji_is_160_rgba_with_alpha(emoji_font: Path):
    png = render_emoji("💜", emoji_font)
    img = Image.open(io.BytesIO(png))
    assert img.format == "PNG"
    assert img.size == (160, 160)
    assert img.mode == "RGBA"
    alpha = img.getchannel("A")
    bbox = alpha.getbbox()
    assert bbox is not None
    # fills most of the box (trimmed + fitted), with transparent corners
    assert bbox[2] - bbox[0] > 140 or bbox[3] - bbox[1] > 140
    assert alpha.getpixel((0, 0)) == 0
    # it's purple-ish somewhere in the middle
    r, g, b, a = img.getpixel((80, 80))
    assert a > 200 and b > r > g


def test_render_emoji_rejects_text(emoji_font: Path):
    with pytest.raises(BadSticker, match="single character"):
        render_emoji("ok", emoji_font)
    with pytest.raises(BadSticker, match="single character"):
        render_emoji("💜💜", emoji_font)


def test_render_upload_fits_and_centres():
    img = Image.new("RGBA", (400, 100), (0, 0, 0, 0))
    img.paste((0, 255, 0, 255), (50, 20, 350, 80))  # 300x60 green bar
    out = io.BytesIO()
    img.save(out, "PNG")
    png = render_upload(out.getvalue())
    res = Image.open(io.BytesIO(png))
    assert res.size == (160, 160)
    bbox = res.getchannel("A").getbbox()
    assert bbox[0] == 0 and bbox[2] == 160  # full width
    assert 60 < bbox[1] < 70 and 90 < bbox[3] < 100  # vertically centred 32px bar


def test_render_upload_rejects_garbage():
    with pytest.raises(BadSticker):
        render_upload(b"\x00\x01nope")
