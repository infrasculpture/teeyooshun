"""Display renders: a sharper JPEG of each question page, for the question and review screens.

Made only for pages classed as question pages ("content"). Never for cover pages
(nothing is kept from them) and never for answer pages (they are not shown to the student).
No API calls: pages are rendered from the PDF, using the rotation recorded at ingest.
"""

from pathlib import Path

from app.ingest.render import file_sha256, render_display, rotate_clockwise


def ensure_display_renders(conn, folder, render_key: str, file_row, settings, out=None) -> int:
    """Create missing display renders for a file's question pages. Returns how many were made.

    render_key: the source's folder under data/renders/ (SourceConfig.render_key).
    A slide handout has none: its pages are slide crops, not PDF pages, and the crops are shown as they are."""
    say = out or (lambda _line: None)
    if file_row["layout"] == "slides_4up":
        return 0
    rows = conn.execute(
        "SELECT id, page_no, rotation FROM pages WHERE file_id = ? AND page_type IN ('content', 'image_only', 'sources') "
        "AND status = 'done' AND display_path IS NULL ORDER BY page_no", (file_row["id"],)).fetchall()
    if not rows:
        return 0
    pdf = Path(folder) / file_row["filename"]
    if not pdf.exists():
        say(f"{file_row['filename']}: PDF not found, so no display images were made")
        return 0
    if file_sha256(pdf) != file_row["sha256"]:
        say(f"{file_row['filename']}: the PDF has changed since this version was read, so no display images "
            "were made (ingest the file again)")
        return 0
    data_dir = Path(settings.data_dir)
    target = data_dir / "renders" / render_key / f"{pdf.stem}-v{file_row['version']}" / "display"
    for row in rows:
        jpg = render_display(pdf, row["page_no"], target / f"p{row['page_no']:03d}.jpg", settings.display_max_edge)
        rotate_clockwise(jpg, row["rotation"])
        conn.execute("UPDATE pages SET display_path = ? WHERE id = ?", (str(jpg.relative_to(data_dir)), row["id"]))
    conn.commit()
    return len(rows)
