"""Content-addressed image blob store.

Images are stored once, keyed by the SHA-256 of their bytes, under a two-level
fan-out directory (blobs/ab/cd/<sha256>.<ext>). Re-downloading the same image is
a no-op. Dimensions and mime are sniffed from the bytes with the standard library
only (no Pillow) — enough for JPEG/PNG/GIF/WebP, the formats journals actually
serve. The web app rewrites article <img src> to /blob/<sha256> so reproduction
works fully offline.
"""
from __future__ import annotations

import hashlib
import logging
import struct
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

_EXT_BY_MIME = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/svg+xml": "svg",
    "image/avif": "avif",
}


@dataclass(frozen=True)
class StoredImage:
    sha256: str
    byte_size: int
    mime: str | None
    width: int | None
    height: int | None
    storage_path: str  # relative to blob root
    is_new: bool


class BlobStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def store(self, data: bytes, *, content_type: str | None = None) -> StoredImage:
        sha = hashlib.sha256(data).hexdigest()
        mime, width, height = sniff(data, content_type)
        ext = _EXT_BY_MIME.get(mime or "", "bin")
        rel = Path(sha[:2]) / sha[2:4] / f"{sha}.{ext}"
        abs_path = self.root / rel
        is_new = not abs_path.exists()
        if is_new:
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = abs_path.with_suffix(abs_path.suffix + ".tmp")
            tmp.write_bytes(data)
            tmp.replace(abs_path)  # atomic publish
        return StoredImage(
            sha256=sha,
            byte_size=len(data),
            mime=mime,
            width=width,
            height=height,
            storage_path=str(rel),
            is_new=is_new,
        )

    def path_for(self, sha256: str, storage_path: str) -> Path:
        return self.root / storage_path


# --------------------------------------------------------------------------- #
# Stdlib-only format sniffing
# --------------------------------------------------------------------------- #

def sniff(data: bytes, content_type: str | None = None) -> tuple[str | None, int | None, int | None]:
    """Return (mime, width, height). Falls back to content_type / None."""
    mime = _magic_mime(data) or _normalize_ct(content_type)
    w = h = None
    try:
        if mime == "image/png":
            w, h = _png_size(data)
        elif mime == "image/gif":
            w, h = _gif_size(data)
        elif mime == "image/jpeg":
            w, h = _jpeg_size(data)
        elif mime == "image/webp":
            w, h = _webp_size(data)
    except Exception:  # never let a malformed header break ingestion
        w = h = None
    return mime, w, h


def _normalize_ct(content_type: str | None) -> str | None:
    if not content_type:
        return None
    return content_type.split(";", 1)[0].strip().lower() or None


def _magic_mime(data: bytes) -> str | None:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    head = data[:256].lstrip()
    if head[:5].lower() == b"<?xml" or head[:4].lower() == b"<svg":
        return "image/svg+xml"
    return None


def _png_size(data: bytes) -> tuple[int, int]:
    # IHDR width/height are the two big-endian uint32 at offset 16.
    w, h = struct.unpack(">II", data[16:24])
    return w, h


def _gif_size(data: bytes) -> tuple[int, int]:
    w, h = struct.unpack("<HH", data[6:10])
    return w, h


def _webp_size(data: bytes) -> tuple[int | None, int | None]:
    fmt = data[12:16]
    if fmt == b"VP8 ":  # lossy
        w = struct.unpack("<H", data[26:28])[0] & 0x3FFF
        h = struct.unpack("<H", data[28:30])[0] & 0x3FFF
        return w, h
    if fmt == b"VP8L":  # lossless
        b = data[21:25]
        bits = int.from_bytes(b, "little")
        w = (bits & 0x3FFF) + 1
        h = ((bits >> 14) & 0x3FFF) + 1
        return w, h
    if fmt == b"VP8X":  # extended
        w = int.from_bytes(data[24:27], "little") + 1
        h = int.from_bytes(data[27:30], "little") + 1
        return w, h
    return None, None


def _jpeg_size(data: bytes) -> tuple[int | None, int | None]:
    # Walk JPEG markers to the Start-Of-Frame segment.
    i = 2
    n = len(data)
    while i + 9 < n:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        seg_len = struct.unpack(">H", data[i + 2:i + 4])[0]
        # SOF0..SOF15 except DHT/JPG/DAC carry frame dimensions.
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                      0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            h, w = struct.unpack(">HH", data[i + 5:i + 9])
            return w, h
        i += 2 + seg_len
    return None, None
