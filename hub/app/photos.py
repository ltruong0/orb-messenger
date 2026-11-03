"""Photo pipeline (docs/hub-api.md "Media", decisions 6):
EXIF transpose -> center-crop square -> 466x466 LANCZOS -> RGB -> baseline JPEG."""

from __future__ import annotations

import io

from PIL import Image, ImageOps, UnidentifiedImageError

try:  # iOS hands over HEIC; accept it when pillow-heif is installed
    from pillow_heif import register_heif_opener

    register_heif_opener()
    HEIF_SUPPORTED = True
except ImportError:  # pragma: no cover
    HEIF_SUPPORTED = False

ORB_SIZE = 466
MAX_UPLOAD = 15 * 1024 * 1024
ACCEPTED_FORMATS = {"JPEG", "PNG", "HEIF", "WEBP", "MPO"}  # MPO: iPhone JPEGs with depth data


class BadImage(ValueError):
    pass


def process_photo(data: bytes, size: int = ORB_SIZE) -> bytes:
    if len(data) > MAX_UPLOAD:
        raise BadImage(f"file larger than {MAX_UPLOAD // (1024 * 1024)} MB")
    try:
        img = Image.open(io.BytesIO(data))
        fmt = img.format or ""
        if fmt not in ACCEPTED_FORMATS:
            raise BadImage(f"unsupported image format {fmt or 'unknown'}")
        img = ImageOps.exif_transpose(img) or img
        img.load()
    except UnidentifiedImageError as e:
        raise BadImage("not an image") from e
    except OSError as e:
        raise BadImage(f"could not decode image: {e}") from e
    # crop the centre square then fit the orb; LANCZOS for the downscale
    img = ImageOps.fit(img, (size, size), method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
    if img.mode != "RGB":
        if "A" in img.getbands():  # flatten transparency onto black (the orb background)
            bg = Image.new("RGB", img.size, (0, 0, 0))
            bg.paste(img.convert("RGBA"), mask=img.convert("RGBA").getchannel("A"))
            img = bg
        else:
            img = img.convert("RGB")
    out = io.BytesIO()
    # progressive=False is the point: JPEGDEC on the ESP32 can't decode progressive.
    img.save(out, "JPEG", quality=85, progressive=False, optimize=True)
    return out.getvalue()


def jpeg_is_baseline(data: bytes) -> bool:
    """True if the JPEG has a SOF0/SOF1 frame (not SOF2 progressive)."""
    if data[:2] != b"\xff\xd8":
        return False
    i = 2
    while i + 4 <= len(data):
        if data[i] != 0xFF:
            return False
        marker = data[i + 1]
        if marker == 0xD8 or 0xD0 <= marker <= 0xD7 or marker == 0x01:
            i += 2
            continue
        if marker in (0xC0, 0xC1):
            return True
        if marker in (0xC2, 0xC6, 0xCA, 0xCE):
            return False
        length = int.from_bytes(data[i + 2 : i + 4], "big")
        i += 2 + length
    return False
