"""Slide handouts: four slides to a page, cut into one image per slide (INGESTION_STAGE_C_SPEC section 3).

No API calls. A page is cut by quarters with a small inset; a quarter with nothing on it is
reported as blank so the model is never asked about it. Slides are numbered in reading order
across the whole file (s01, s02, ...), whichever order the handout is read in.
"""

from pathlib import Path

from PIL import Image

PER_PAGE = 4
INSET = 0.01  # fraction of the page's width and height trimmed from each edge of a quarter
INK_LEVEL = 200  # grey level below which a pixel counts as ink
BLANK_INK_FRACTION = 0.002  # a quarter with less ink than this is blank

# Quarters are numbered TL=0, TR=1, BL=2, BR=3. Reading order, as quarter numbers.
ORDERS = {
    "down_then_across": (0, 2, 1, 3),  # TL, BL, TR, BR
    "across_then_down": (0, 1, 2, 3),  # TL, TR, BL, BR
}


def slide_no(page_no: int, position: int) -> int:
    """The file-wide slide number (1-based) for the slide at `position` (0-3, reading order) of a page."""
    return (page_no - 1) * PER_PAGE + position + 1


def slide_page(slide: int) -> int:
    """The file page (1-based) a slide is on."""
    return (slide - 1) // PER_PAGE + 1


def slide_label(slide: int) -> str:
    return f"s{slide:02d}"


def _box(width: int, height: int, quarter: int) -> tuple:
    col, row = quarter % 2, quarter // 2
    pad_x, pad_y = round(width * INSET), round(height * INSET)
    left, top = col * width // 2 + pad_x, row * height // 2 + pad_y
    right, bottom = (col + 1) * width // 2 - pad_x, (row + 1) * height // 2 - pad_y
    return left, top, right, bottom


def is_blank(image) -> bool:
    grey = image.convert("L")
    ink = sum(grey.histogram()[:INK_LEVEL])
    return ink / (grey.width * grey.height) < BLANK_INK_FRACTION


def split_page(page_png, order: str, out_for, max_edge: int) -> list:
    """Cut a rendered page into its four slides. Returns four entries in reading order:
    the path written, or None for a blank quarter.

    out_for(position) gives the path for the slide at that position (0-3) in reading order.
    Each slide is shrunk (never enlarged) so its long edge is at most max_edge.
    """
    results = []
    with Image.open(page_png) as page:
        page = page.convert("RGB")
        for position, quarter in enumerate(ORDERS[order]):
            crop = page.crop(_box(page.width, page.height, quarter))
            if is_blank(crop):
                results.append(None)
                continue
            if max(crop.size) > max_edge:
                crop.thumbnail((max_edge, max_edge))
            out = Path(out_for(position))
            out.parent.mkdir(parents=True, exist_ok=True)
            crop.save(out)
            results.append(out)
    return results
