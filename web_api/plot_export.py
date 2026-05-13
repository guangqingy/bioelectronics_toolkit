from __future__ import annotations

from html import escape

import numpy as np

from services.output_manifest import svg_with_metadata
from services.output_naming import next_numbered_path as next_numbered_path


def svg_num(value: float) -> str:
    return f"{float(value):.6g}"


def svg_ticks(vmin: float, vmax: float, n: int = 5) -> np.ndarray:
    if not np.isfinite(vmin) or not np.isfinite(vmax):
        return np.asarray([], dtype=float)
    if vmax == vmin:
        pad = 1.0 if vmin == 0 else abs(vmin) * 0.05
        vmin -= pad
        vmax += pad
    return np.linspace(vmin, vmax, max(2, int(n)))


def clean_trace_svg(
    x,
    y,
    *,
    y_min=None,
    y_max=None,
    width: float = 720.0,
    height: float = 300.0,
    line_color: str = "#3E6AE1",
    line_width: float = 1.2,
    max_points: int = 50000,
    metadata: dict | None = None,
) -> bytes:
    """Build an ungrouped trace SVG with only axes, ticks, numbers, and data."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    finite = np.isfinite(x) & np.isfinite(y)
    x = x[finite]
    y = y[finite]
    if max_points > 0 and x.size > max_points:
        step = int(np.ceil(x.size / max_points))
        x = x[::step]
        y = y[::step]

    width = max(240.0, float(width))
    height = max(160.0, float(height))
    left, right, top, bottom = 58.0, 14.0, 12.0, 38.0
    plot_w = width - left - right
    plot_h = height - top - bottom

    if x.size:
        xmin, xmax = float(x[0]), float(x[-1])
        if xmax == xmin:
            xmax = xmin + 1.0
    else:
        xmin, xmax = 0.0, 1.0

    if y.size:
        ymin = float(np.nanmin(y)) if y_min is None else float(y_min)
        ymax = float(np.nanmax(y)) if y_max is None else float(y_max)
    else:
        ymin, ymax = 0.0, 1.0
    if y_min is not None:
        ymin = float(y_min)
    if y_max is not None:
        ymax = float(y_max)
    if not np.isfinite(ymin) or not np.isfinite(ymax):
        ymin, ymax = 0.0, 1.0
    if ymax == ymin:
        pad = 1.0 if ymin == 0 else abs(ymin) * 0.05
        ymin -= pad
        ymax += pad

    def sx(v):
        return left + (float(v) - xmin) / (xmax - xmin) * plot_w

    def sy(v):
        yy = top + (ymax - float(v)) / (ymax - ymin) * plot_h
        return max(top, min(top + plot_h, yy))

    points = " ".join(f"{svg_num(sx(xv))},{svg_num(sy(yv))}" for xv, yv in zip(x, y))
    axis_style = 'stroke="#222" stroke-width="1" vector-effect="non-scaling-stroke"'
    tick_style = 'stroke="#222" stroke-width="0.8" vector-effect="non-scaling-stroke"'
    text_style = 'font-family="Arial, Helvetica, sans-serif" font-size="11" fill="#222"'
    line_style = (
        f'fill="none" stroke="{escape(str(line_color), quote=True)}" '
        f'stroke-width="{svg_num(line_width)}" vector-effect="non-scaling-stroke"'
    )

    parts = [
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{int(width)}" '
            f'height="{int(height)}" viewBox="0 0 {int(width)} {int(height)}">'
        ),
        (
            f'<line x1="{svg_num(left)}" y1="{svg_num(top + plot_h)}" '
            f'x2="{svg_num(left + plot_w)}" y2="{svg_num(top + plot_h)}" '
            f"{axis_style}/>"
        ),
        (
            f'<line x1="{svg_num(left)}" y1="{svg_num(top)}" '
            f'x2="{svg_num(left)}" y2="{svg_num(top + plot_h)}" {axis_style}/>'
        ),
    ]
    for tick in svg_ticks(xmin, xmax):
        tx = sx(tick)
        parts.append(
            f'<line x1="{svg_num(tx)}" y1="{svg_num(top + plot_h)}" '
            f'x2="{svg_num(tx)}" y2="{svg_num(top + plot_h + 5)}" '
            f"{tick_style}/>"
        )
        parts.append(
            f'<text x="{svg_num(tx)}" y="{svg_num(top + plot_h + 20)}" '
            f'text-anchor="middle" {text_style}>{escape(f"{tick:g}")}</text>'
        )
    for tick in svg_ticks(ymin, ymax):
        ty = sy(tick)
        parts.append(
            f'<line x1="{svg_num(left - 5)}" y1="{svg_num(ty)}" '
            f'x2="{svg_num(left)}" y2="{svg_num(ty)}" {tick_style}/>'
        )
        parts.append(
            f'<text x="{svg_num(left - 8)}" y="{svg_num(ty + 4)}" '
            f'text-anchor="end" {text_style}>{escape(f"{tick:g}")}</text>'
        )
    if points:
        parts.append(f'<polyline points="{points}" {line_style}/>')
    parts.append("</svg>")
    payload = "\n".join(parts).encode("utf-8")
    return svg_with_metadata(payload, metadata) if metadata else payload
