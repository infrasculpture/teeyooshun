"""Render PDF pages to PNG with poppler, and rotate renders with Pillow."""

import hashlib
import re
import subprocess
from pathlib import Path

from PIL import Image


class RenderError(RuntimeError):
    pass


def file_sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _run(args: list) -> str:
    try:
        done = subprocess.run(args, capture_output=True, text=True, timeout=120)
    except FileNotFoundError:
        raise RenderError(f"{args[0]} not found: is poppler-utils installed in the image?") from None
    except subprocess.TimeoutExpired:
        raise RenderError(f"{args[0]} timed out") from None
    if done.returncode != 0:
        raise RenderError(f"{args[0]} failed: {done.stderr.strip()[:300]}")
    return done.stdout


def page_count(pdf) -> int:
    out = _run(["pdfinfo", str(pdf)])
    m = re.search(r"^Pages:\s+(\d+)", out, re.MULTILINE)
    if not m:
        raise RenderError(f"pdfinfo gave no page count for {pdf}")
    return int(m.group(1))


def render_page(pdf, page_no: int, out_png, max_edge: int) -> Path:
    """Render one page (1-based) so its long edge is max_edge pixels."""
    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    _run(["pdftoppm", "-png", "-f", str(page_no), "-l", str(page_no), "-scale-to", str(max_edge),
          "-singlefile", str(pdf), str(out_png.with_suffix(""))])
    if not out_png.exists():
        raise RenderError(f"pdftoppm wrote no image for page {page_no} of {pdf}")
    return out_png


def rotate_clockwise(png, degrees: int) -> None:
    """Turn the image clockwise by 90, 180 or 270 degrees, in place."""
    if degrees % 360 == 0:
        return
    with Image.open(png) as im:
        turned = im.rotate(-degrees, expand=True)  # Pillow turns anticlockwise for positive angles
    turned.save(png)


def image_size(png) -> tuple[int, int]:
    with Image.open(png) as im:
        return im.size
