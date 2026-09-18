"""Tiny dependency-free HTML price chart built from the stored history."""

from datetime import datetime
from html import escape

WIDTH = 760
HEIGHT = 300
PAD_L = 70
PAD_R = 24
PAD_T = 28
PAD_B = 46


def _rows_to_points(rows) -> list[tuple[datetime, float]]:
    """Turn db rows into (when, price) pairs, oldest first."""
    points: list[tuple[datetime, float]] = []
    for row in rows:
        when = datetime.fromisoformat(row["checked_at"]).astimezone()
        points.append((when, float(row["price"])))
    points.sort(key=lambda p: p[0])
    return points


def _money(value: float) -> str:
    return "₹" + format(value, ",.0f")


def build_svg(points: list[tuple[datetime, float]]) -> str:
    """Draw the price line as an inline SVG (no chart library needed)."""
    inner_w = WIDTH - PAD_L - PAD_R
    inner_h = HEIGHT - PAD_T - PAD_B
    prices = [price for _, price in points]
    low, high = min(prices), max(prices)
    if high == low:
        high, low = high + 1.0, low - 1.0

    def x_at(index: int) -> float:
        if len(points) == 1:
            return PAD_L + inner_w / 2
        return PAD_L + inner_w * index / (len(points) - 1)

    def y_at(price: float) -> float:
        return PAD_T + inner_h * (high - price) / (high - low)

    coords = [
        (x_at(i), y_at(price)) for i, (_, price) in enumerate(points)
    ]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)

    dots = []
    for (x, y), (when, price) in zip(coords, points):
        tip = f"{when.strftime('%d %b %Y %H:%M')} — {_money(price)}"
        dots.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.5" fill="#2f81f7">'
            f"<title>{escape(tip)}</title></circle>"
        )

    gridlines = []
    for step in range(3):
        y = PAD_T + inner_h * step / 2
        value = high - (high - low) * step / 2
        gridlines.append(
            f'<line x1="{PAD_L}" y1="{y:.1f}" x2="{WIDTH - PAD_R}" y2="{y:.1f}"'
            ' stroke="#30363d" stroke-width="1" stroke-dasharray="4 4"/>'
        )
        gridlines.append(
            f'<text x="{PAD_L - 10}" y="{y + 4:.1f}" fill="#8b949e" font-size="12"'
            f' text-anchor="end">{escape(_money(value))}</text>'
        )

    first = points[0][0].strftime("%d %b")
    last = points[-1][0].strftime("%d %b")
    return f"""<svg viewBox="0 0 {WIDTH} {HEIGHT}" width="100%" height="{HEIGHT}"
     role="img" aria-label="price history chart" xmlns="http://www.w3.org/2000/svg">
  <rect x="0" y="0" width="{WIDTH}" height="{HEIGHT}" fill="#0d1117" rx="8"/>
  {''.join(gridlines)}
  <polyline points="{line}" fill="none" stroke="#2f81f7" stroke-width="2"/>
  {''.join(dots)}
  <text x="{PAD_L}" y="{HEIGHT - 16}" fill="#8b949e" font-size="12">{escape(first)}</text>
  <text x="{WIDTH - PAD_R}" y="{HEIGHT - 16}" fill="#8b949e" font-size="12"
        text-anchor="end">{escape(last)}</text>
</svg>"""


def build_table(points: list[tuple[datetime, float]], limit: int = 10) -> str:
    """The most recent checks as a plain table, newest first."""
    rows = []
    for when, price in reversed(points[-limit:]):
        rows.append(
            f"<tr><td>{escape(when.strftime('%d %b %Y %H:%M'))}</td>"
            f"<td class='num'>{escape(_money(price))}</td></tr>"
        )
    return (
        "<table><thead><tr><th>checked at</th><th>price</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def render_html(product: str, rows) -> str:
    """Full standalone HTML page: heading, stats, chart, recent checks."""
    points = _rows_to_points(rows)
    if not points:
        raise ValueError("no price history rows to chart")
    prices = [price for _, price in points]
    stats = (
        f"{len(points)} checks · lowest {_money(min(prices))} · "
        f"highest {_money(max(prices))} · latest {_money(prices[-1])}"
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(product)} — price history</title>
<style>
  body {{ background: #010409; color: #e6edf3; font: 14px/1.5 system-ui, sans-serif;
         margin: 0; padding: 24px; }}
  h1 {{ font-size: 20px; margin: 0 0 4px; }}
  p.stats {{ color: #8b949e; margin: 0 0 18px; }}
  .card {{ max-width: 820px; margin: 0 auto; }}
  table {{ border-collapse: collapse; width: 100%; margin-top: 20px; }}
  th, td {{ border-bottom: 1px solid #30363d; padding: 6px 8px; text-align: left; }}
  th {{ color: #8b949e; font-weight: 500; }}
  td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
</style>
</head>
<body>
<div class="card">
  <h1>{escape(product)}</h1>
  <p class="stats">{escape(stats)}</p>
  {build_svg(points)}
  {build_table(points)}
</div>
</body>
</html>
"""


def slugify(product: str) -> str:
    """A safe file name chunk for a product name."""
    keep = [ch.lower() if ch.isalnum() else "-" for ch in product.strip()]
    slug = "".join(keep).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug[:40] or "product"
