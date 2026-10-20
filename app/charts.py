"""Small server-rendered SVG charts for the parent dashboard. No library, no JavaScript.

Each function returns an SVG string (safe to mark as HTML: every label is escaped here).
The page always prints the same numbers as text next to the chart.
"""

from html import escape

PALETTE = ("#2a6f97", "#c2571a", "#4c8c4a", "#7b5ea7")   # one colour per line, never the only cue (see legend)


def line_chart(x_labels: list[str], series: list[dict], ref_lines: list[dict], width=560, height=220) -> str:
    """Lines of percentages (0-100) over x_labels.

    series: [{"name", "values": [float | None per x label]}]. ref_lines: [{"label", "value", "dash"}].
    A None value leaves a gap.
    """
    left, right, top, bottom = 34, 12, 12, 28
    w, h = width - left - right, height - top - bottom
    n = len(x_labels)

    def x(i):
        return left + (w / 2 if n <= 1 else w * i / (n - 1))

    def y(v):
        return top + h * (1 - max(0, min(100, v)) / 100)

    out = [f'<svg viewBox="0 0 {width} {height}" role="img" class="chart" xmlns="http://www.w3.org/2000/svg">']
    for tick in (0, 25, 50, 75, 100):
        out.append(f'<line x1="{left}" x2="{width - right}" y1="{y(tick):.1f}" y2="{y(tick):.1f}" stroke="#ccc" stroke-width="0.5"/>'
                   f'<text x="{left - 4}" y="{y(tick) + 3:.1f}" font-size="9" text-anchor="end" fill="currentColor">{tick}</text>')
    for ref in ref_lines:
        dash = ref.get("dash", "4 3")
        out.append(f'<line x1="{left}" x2="{width - right}" y1="{y(ref["value"]):.1f}" y2="{y(ref["value"]):.1f}" '
                   f'stroke="currentColor" stroke-width="1" stroke-dasharray="{dash}"/>'
                   f'<text x="{width - right}" y="{y(ref["value"]) - 3:.1f}" font-size="9" text-anchor="end" '
                   f'fill="currentColor">{escape(ref["label"])}</text>')
    step = max(1, n // 8)
    for i, label in enumerate(x_labels):
        if i % step == 0:
            out.append(f'<text x="{x(i):.1f}" y="{height - 10}" font-size="9" text-anchor="middle" '
                       f'fill="currentColor">{escape(label)}</text>')
    for k, s in enumerate(series):
        colour = PALETTE[k % len(PALETTE)]
        run = []
        for i, v in enumerate(s["values"] + [None]):
            if v is not None:
                run.append((x(i), y(v)))
                continue
            if len(run) > 1:
                out.append('<polyline fill="none" stroke="%s" stroke-width="2" points="%s"/>'
                           % (colour, " ".join(f"{px:.1f},{py:.1f}" for px, py in run)))
            for px, py in run:
                out.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="3" fill="{colour}"/>')
            run = []
    out.append("</svg>")
    return "".join(out)


def legend(series: list[dict]) -> list[dict]:
    return [{"name": s["name"], "colour": PALETTE[k % len(PALETTE)]} for k, s in enumerate(series)]


def heatmap(grid: list[list[int]], row_labels: list[str], col_labels: list[str], cell=18) -> str:
    """A grid of counts (rows x columns); darker means more. Each cell has a <title> with its count."""
    peak = max((max(row) for row in grid), default=0)
    left, header = 34, 16
    width, height = left + cell * len(col_labels), header + cell * len(row_labels)
    out = [f'<svg viewBox="0 0 {width} {height}" role="img" class="chart" xmlns="http://www.w3.org/2000/svg">']
    for c, label in enumerate(col_labels):
        if c % 3 == 0:
            out.append(f'<text x="{left + c * cell + cell / 2}" y="11" font-size="9" text-anchor="middle" '
                       f'fill="currentColor">{escape(label)}</text>')
    for r, row in enumerate(grid):
        out.append(f'<text x="{left - 4}" y="{header + r * cell + cell * 0.65:.1f}" font-size="9" text-anchor="end" '
                   f'fill="currentColor">{escape(row_labels[r])}</text>')
        for c, count in enumerate(row):
            opacity = 0.08 if not count else 0.25 + 0.75 * count / peak
            out.append(f'<rect x="{left + c * cell}" y="{header + r * cell}" width="{cell - 1}" height="{cell - 1}" '
                       f'fill="#2a6f97" fill-opacity="{opacity:.2f}"><title>{escape(row_labels[r])} '
                       f'{escape(col_labels[c])}: {count}</title></rect>')
    out.append("</svg>")
    return "".join(out)
