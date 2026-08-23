from __future__ import annotations

import struct

from mediatracker import images


def _png(width: int, height: int) -> bytes:
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">II", width, height) + b"\x08\x06\x00\x00\x00"
    return sig + b"\x00\x00\x00\x0dIHDR" + ihdr + b"tail"


def _gif(width: int, height: int) -> bytes:
    return b"GIF89a" + struct.pack("<HH", width, height) + b"\x00" * 4


def test_sniff_png_dimensions():
    mime, w, h = images.sniff(_png(640, 480))
    assert mime == "image/png"
    assert (w, h) == (640, 480)


def test_sniff_gif_dimensions():
    mime, w, h = images.sniff(_gif(12, 34))
    assert mime == "image/gif"
    assert (w, h) == (12, 34)


def test_sniff_falls_back_to_content_type():
    mime, w, h = images.sniff(b"not an image", content_type="image/jpeg; charset=x")
    assert mime == "image/jpeg"


def test_blobstore_is_content_addressed_and_dedups(tmp_path):
    store = images.BlobStore(tmp_path)
    data = _png(2, 2)
    first = store.store(data)
    second = store.store(data)
    assert first.sha256 == second.sha256
    assert first.is_new is True
    assert second.is_new is False
    # fan-out path: ab/cd/<sha>.png
    assert first.storage_path.endswith(".png")
    assert (tmp_path / first.storage_path).read_bytes() == data
