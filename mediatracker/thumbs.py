"""Cached downscales of the blob store, so 216,000 pictures can be browsed.

The store holds originals: a mean width of 1,705 pixels and 83 GB in total. A
grid of sixty of those is roughly 24 MB over the wire and several seconds of
decoding, which is not a picture browser, it is a stall. So this keeps a second
content-addressed tree of downscales beside the first, derived and disposable —
delete it and the next request rebuilds what it needs.

**Pillow is optional here, and that is the point.** The rest of this project is
stdlib-only on purpose (`images.py` sniffs JPEG headers by hand rather than take
the dependency), and a JPEG decoder is not something the standard library has.
Rather than force the dependency on the whole application, this module degrades:
without Pillow `available()` is false, the HTTP route serves the original bytes,
and every other feature is untouched. The browser gets slower, not broken.

Thumbnails are generated on demand rather than in a 216,000-image pre-pass,
because a browser asks for exactly the ones it is showing and the answer is
cached from then on. `build()` exists for warming a known set, not as a
precondition for anything.
"""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

# Longest edge, by name. "t" fills a contact-sheet cell; "m" is what the
# gallery shows full-screen, still an order of magnitude under the original.
SIZES = {"t": 320, "m": 1200}
QUALITY = 78

# Formats Pillow will not reliably open on this machine, or that are pointless
# to rasterise. Served as-is.
PASSTHROUGH_MIME = frozenset({"image/svg+xml", "image/avif"})

_PIL = None


def available() -> bool:
    """Whether downscaling is possible at all. Cached after the first look."""
    global _PIL
    if _PIL is None:
        try:
            from PIL import Image, ImageOps  # noqa: F401
            _PIL = True
        except ImportError:
            _PIL = False
            log.info("Pillow absent: the image browser will serve originals")
    return _PIL


def rel_path(sha256: str, size: str) -> Path:
    """Where a downscale lives, mirroring the blob store's fan-out."""
    return Path("thumbs") / size / sha256[:2] / sha256[2:4] / f"{sha256}.jpg"


def ensure(root: Path, *, sha256: str, storage_path: str, size: str = "t",
           mime: str | None = None) -> tuple[Path, str] | None:
    """Return (path, mime) for a downscale, generating it once if needed.

    Returns None when the caller should serve the original instead — no
    Pillow, an unrasterisable format, an image already smaller than the
    target, or a decode that failed. A missing thumbnail is never an error
    worth showing anyone; it is a slower picture.
    """
    if not available() or (mime in PASSTHROUGH_MIME):
        return None
    if size not in SIZES:
        size = "t"
    out = root / rel_path(sha256, size)
    if out.is_file():
        return out, "image/jpeg"

    src = (root / storage_path).resolve()
    if not str(src).startswith(str(Path(root).resolve())) or not src.is_file():
        return None

    from PIL import Image, ImageOps
    try:
        with Image.open(src) as im:
            # Phones and CMSes both write rotation into EXIF rather than into
            # the pixels; without this a portrait photo browses on its side.
            im = ImageOps.exif_transpose(im)
            edge = SIZES[size]
            if max(im.size) <= edge:
                return None            # upscaling would only cost bytes
            im.thumbnail((edge, edge), Image.LANCZOS)
            if im.mode not in ("RGB", "L"):
                # Flatten transparency onto white: a PNG logo on a dark grid
                # cell is otherwise a black rectangle.
                bg = Image.new("RGB", im.size, (255, 255, 255))
                bg.paste(im, mask=im.split()[-1] if im.mode in ("RGBA", "LA") else None)
                im = bg
            out.parent.mkdir(parents=True, exist_ok=True)
            tmp = out.with_suffix(".tmp")
            im.save(tmp, "JPEG", quality=QUALITY, optimize=True, progressive=True)
            tmp.replace(out)           # atomic, so a torn file is never served
    except Exception as exc:
        log.debug("thumbnail failed for %s: %s", sha256[:12], exc)
        return None
    return out, "image/jpeg"


def build(conn, root: Path, *, size: str = "t", limit: int | None = None,
          progress=None) -> dict:
    """Warm the cache for stored images, newest first. Optional, never required."""
    if not available():
        return {"generated": 0, "skipped": 0, "note": "Pillow not installed"}
    made = skipped = 0
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT sha256, storage_path, mime FROM image
            ORDER BY first_seen DESC {'LIMIT %s' if limit else ''}
        """, (limit,) if limit else ())
        rows = cur.fetchall()
    for i, (sha, path, mime) in enumerate(rows, 1):
        if ensure(root, sha256=sha, storage_path=path, size=size, mime=mime):
            made += 1
        else:
            skipped += 1
        if progress and i % 500 == 0:
            progress(i, len(rows), made, skipped)
    return {"generated": made, "skipped": skipped, "total": len(rows)}


def cache_size(root: Path) -> dict:
    """Bytes and file count per size, for the maintenance view."""
    out = {}
    for size in SIZES:
        base = Path(root) / "thumbs" / size
        n = total = 0
        if base.is_dir():
            for p in base.rglob("*.jpg"):
                n += 1
                total += p.stat().st_size
        out[size] = {"files": n, "bytes": total}
    return out


def main(argv=None) -> int:
    import argparse
    from . import db
    from .config import load_config

    p = argparse.ArgumentParser(prog="mediatracker.thumbs")
    p.add_argument("--size", default="t", choices=list(SIZES))
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--status", action="store_true")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    cfg = load_config()
    if a.status:
        print(f"Pillow: {'yes' if available() else 'no'}")
        for size, st in cache_size(cfg.blob_path).items():
            print(f"  {size}: {st['files']:,} files, {st['bytes']/1e6:,.0f} MB")
        return 0
    conn = db.connect(cfg)
    if conn is None:
        print("no database")
        return 1

    def show(i, n, made, skipped):
        print(f"  {i:,}/{n:,}  {made:,} made  {skipped:,} passed through", flush=True)

    out = build(conn, cfg.blob_path, size=a.size, limit=a.limit, progress=show)
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
