"""charts.py — SME custom SVG charts + plotly helpers.

Ports SME's `render_design_gauge` (coverage gauge), `render_design_hbar`
(horizontal bar chart with labels), and a plotly stacked horizontal bar.
"""
from __future__ import annotations

import streamlit as st


def _color_for_pct(pct: float) -> str:
    if pct >= 100:
        return "#10B981"
    if pct >= 90:
        return "#F97316"
    if pct >= 80:
        return "#EAB308"
    return "#EF4444"


def render_design_gauge(pct: float, can: float, total: float) -> None:
    """SVG semi-circular gauge — exact SME look."""
    try:
        p = max(0.0, min(100.0, float(pct)))
    except (TypeError, ValueError):
        p = 0.0
    color = _color_for_pct(p)
    sweep = (p / 100) * 180
    import math
    r = 110
    cx, cy = 130, 120
    # Compute end-point of arc
    rad = math.radians(180 - sweep)
    x2 = cx + r * math.cos(rad)
    y2 = cy - r * math.sin(rad)
    large = 1 if sweep > 180 else 0
    arc_path = (
        f"M {cx - r} {cy} A {r} {r} 0 {large} 1 {x2:.2f} {y2:.2f}"
    )
    track_path = f"M {cx - r} {cy} A {r} {r} 0 0 1 {cx + r} {cy}"

    html = f"""
    <div style="display:flex;justify-content:center;align-items:center;">
      <svg width="260" height="160" viewBox="0 0 260 160" xmlns="http://www.w3.org/2000/svg">
        <path d="{track_path}" stroke="#E5E7EB" stroke-width="18" fill="none" stroke-linecap="round"/>
        <path d="{arc_path}" stroke="{color}" stroke-width="18" fill="none" stroke-linecap="round"/>
        <text x="{cx}" y="100" text-anchor="middle" font-size="32" font-weight="700"
              fill="{color}" font-family="Inter,Arial,sans-serif">{p:.1f}%</text>
        <text x="{cx}" y="125" text-anchor="middle" font-size="11"
              fill="#6B7280" font-family="Inter,Arial,sans-serif">Overall Coverage</text>
        <text x="{cx}" y="148" text-anchor="middle" font-size="10"
              fill="#9CA3AF" font-family="Inter,Arial,sans-serif">
          {can:,.1f} / {total:,.1f} SQM
        </text>
      </svg>
    </div>
    """
    st.markdown(html, unsafe_allow_html=True)


def render_design_hbar(
    items: list[dict],
    *,
    height_per_row: int = 26,
    width: int = 480,
    label_width: int = 160,
    value_fmt: str = "{:.1f}%",
) -> None:
    """SVG horizontal bar chart with right-aligned labels.

    items = [{label, pct, value?}, ...]
    """
    if not items:
        st.caption("(no data)")
        return
    n = len(items)
    height = max(60, n * height_per_row + 16)
    bar_w = width - label_width - 70
    rows_svg = []
    for i, it in enumerate(items):
        try:
            pct = max(0.0, min(100.0, float(it.get("pct", 0))))
        except (TypeError, ValueError):
            pct = 0.0
        color = _color_for_pct(pct)
        y = 12 + i * height_per_row
        bar_len = (pct / 100) * bar_w
        rows_svg.append(
            f'<text x="{label_width - 6}" y="{y + 13}" text-anchor="end" '
            f'font-size="11" fill="#374151" '
            f'font-family="Inter,Arial,sans-serif">{it["label"]}</text>'
            f'<rect x="{label_width}" y="{y}" width="{bar_w}" height="14" '
            f'fill="#F3F4F6" rx="3"/>'
            f'<rect x="{label_width}" y="{y}" width="{bar_len:.1f}" height="14" '
            f'fill="{color}" rx="3"/>'
            f'<text x="{label_width + bar_w + 6}" y="{y + 12}" '
            f'font-size="11" font-weight="600" fill="{color}" '
            f'font-family="Inter,Arial,sans-serif">'
            f'{value_fmt.format(pct)}</text>'
        )
    svg = (
        f'<svg width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" '
        f'xmlns="http://www.w3.org/2000/svg">'
        + "".join(rows_svg)
        + "</svg>"
    )
    st.markdown(
        f'<div style="overflow-x:auto;">{svg}</div>',
        unsafe_allow_html=True,
    )


def render_plotly_stacked_hbar(
    *,
    items: list[dict],
    title: str = "",
    height: int = None,
) -> None:
    """Plotly horizontal stacked bar: Available (green) + Shortage (red).
    items = [{label, available, shortage}, ...]
    """
    try:
        import plotly.graph_objects as go
    except ImportError:
        st.caption("(plotly not installed)")
        return
    if not items:
        st.caption("(no shortages)")
        return
    labels = [i["label"] for i in items]
    avail = [float(i.get("available", 0) or 0) for i in items]
    short = [float(i.get("shortage", 0) or 0) for i in items]
    fig = go.Figure()
    fig.add_bar(
        y=labels, x=avail, orientation="h", name="Available",
        marker_color="#10B981",
    )
    fig.add_bar(
        y=labels, x=short, orientation="h", name="Shortage",
        marker_color="#EF4444",
    )
    fig.update_layout(
        barmode="stack", title=title,
        margin=dict(l=40, r=20, t=40 if title else 10, b=10),
        height=height or max(180, 32 * len(items) + 80),
        showlegend=True,
        plot_bgcolor="#FFFFFF",
        paper_bgcolor="#FFFFFF",
    )
    st.plotly_chart(fig, use_container_width=True)


def render_plotly_grouped_bar_by_location(
    *,
    rows: list[dict],
    title: str = "",
    height: int = 320,
) -> None:
    """Stacked bar per location: Available (green) + Shortage (red), SQM."""
    try:
        import plotly.graph_objects as go
    except ImportError:
        return
    if not rows:
        st.caption("(no data)")
        return
    locs = [r["location"] for r in rows]
    avail = [float(r.get("available", 0) or 0) for r in rows]
    short = [float(r.get("shortage", 0) or 0) for r in rows]
    fig = go.Figure()
    fig.add_bar(x=locs, y=avail, name="Available SQM", marker_color="#10B981")
    fig.add_bar(x=locs, y=short, name="Shortage SQM", marker_color="#EF4444")
    fig.update_layout(
        barmode="stack", title=title,
        height=height, plot_bgcolor="#FFFFFF", paper_bgcolor="#FFFFFF",
        margin=dict(l=40, r=20, t=40 if title else 10, b=40),
    )
    st.plotly_chart(fig, use_container_width=True)
