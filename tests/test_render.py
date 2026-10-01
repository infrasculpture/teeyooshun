"""Rendering with poppler and rotating with Pillow (made-up PDFs; needs poppler-utils, which is in the image)."""

from app.ingest.render import image_size, page_count, render_page, rotate_clockwise
from ingest_helpers import make_pdf

from PIL import Image


def _dark(png, xy) -> bool:
    with Image.open(png) as im:
        return sum(im.convert("RGB").getpixel(xy)) < 150


def test_page_count(tmp_path):
    assert page_count(make_pdf(tmp_path / "x.pdf", ["one", "two", "three"])) == 3


def test_render_downscales_long_edge(tmp_path):
    pdf = make_pdf(tmp_path / "x.pdf", ["one", "two"])
    png = render_page(pdf, 2, tmp_path / "out" / "p002.png", 1568)
    width, height = image_size(png)
    assert max(width, height) == 1568 and width < height


def test_render_respects_a_smaller_limit(tmp_path):
    png = render_page(make_pdf(tmp_path / "x.pdf", ["one"]), 1, tmp_path / "p.png", 800)
    assert max(image_size(png)) == 800


def test_rotate_180_moves_content(tmp_path):
    png = render_page(make_pdf(tmp_path / "x.pdf", ["one"]), 1, tmp_path / "p.png", 1568)
    width, height = image_size(png)
    assert _dark(png, (180, 270))  # inside the black square near the top left
    rotate_clockwise(png, 180)
    assert image_size(png) == (width, height)
    assert not _dark(png, (180, 270))
    assert _dark(png, (width - 1 - 180, height - 1 - 270))


def test_rotate_90_swaps_sides(tmp_path):
    png = render_page(make_pdf(tmp_path / "x.pdf", ["one"]), 1, tmp_path / "p.png", 1568)
    width, height = image_size(png)
    rotate_clockwise(png, 90)
    assert image_size(png) == (height, width)
