"""Photo pipeline: EXIF rotation, centre crop, 466x466, baseline JPEG, format gate."""

from __future__ import annotations

import io

import pytest
from app.photos import HEIF_SUPPORTED, BadImage, jpeg_is_baseline, process_photo
from PIL import Image


def png_bytes(w: int, h: int, color=(200, 30, 30)) -> bytes:
    img = Image.new("RGB", (w, h), color)
    # a white top-left quadrant so we can tell where "up" ended up
    img.paste((255, 255, 255), (0, 0, w // 2, h // 2))
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


def jpeg_with_orientation(w: int, h: int, orientation: int) -> bytes:
    img = Image.open(io.BytesIO(png_bytes(w, h)))
    exif = Image.Exif()
    exif[0x0112] = orientation
    out = io.BytesIO()
    img.save(out, "JPEG", exif=exif)
    return out.getvalue()


def test_landscape_becomes_466_baseline():
    jpeg = process_photo(png_bytes(1600, 900))
    img = Image.open(io.BytesIO(jpeg))
    assert img.format == "JPEG"
    assert img.size == (466, 466)
    assert img.mode == "RGB"
    assert jpeg_is_baseline(jpeg)
    assert "exif" not in img.info
    # centre crop: the white quadrant occupied the left half; after the centre
    # crop of a 16:9 image the white region should still be top-left-ish
    assert img.getpixel((10, 10)) == (255, 255, 255)
    assert img.getpixel((455, 455))[0] > 150 and img.getpixel((455, 455))[1] < 80


def test_progressive_is_never_emitted():
    img = Image.new("RGB", (800, 800), (0, 0, 255))
    out = io.BytesIO()
    img.save(out, "JPEG", progressive=True)
    assert not jpeg_is_baseline(out.getvalue())
    assert jpeg_is_baseline(process_photo(out.getvalue()))


def test_exif_orientation_applied():
    # orientation 6 = rotate 90 CW on display; the white quadrant moves to the top-right
    jpeg = process_photo(jpeg_with_orientation(900, 900, 6))
    img = Image.open(io.BytesIO(jpeg))
    assert img.getpixel((455, 10)) == (255, 255, 255)
    assert img.getpixel((10, 10)) != (255, 255, 255)


def test_small_images_are_upscaled_to_fill():
    jpeg = process_photo(png_bytes(100, 300))
    assert Image.open(io.BytesIO(jpeg)).size == (466, 466)


def test_transparency_flattened_to_black():
    img = Image.new("RGBA", (500, 500), (0, 0, 0, 0))
    out = io.BytesIO()
    img.save(out, "PNG")
    jpeg = process_photo(out.getvalue())
    assert Image.open(io.BytesIO(jpeg)).getpixel((233, 233)) == (0, 0, 0)


def test_garbage_rejected():
    with pytest.raises(BadImage):
        process_photo(b"not an image at all")


def test_unsupported_format_rejected():
    img = Image.new("RGB", (50, 50))
    out = io.BytesIO()
    img.save(out, "BMP")
    with pytest.raises(BadImage, match="unsupported"):
        process_photo(out.getvalue())


@pytest.mark.skipif(not HEIF_SUPPORTED, reason="pillow-heif not installed")
def test_heic_accepted():
    img = Image.new("RGB", (640, 480), (10, 200, 10))
    out = io.BytesIO()
    img.save(out, "HEIF")
    jpeg = process_photo(out.getvalue())
    assert Image.open(io.BytesIO(jpeg)).size == (466, 466)
