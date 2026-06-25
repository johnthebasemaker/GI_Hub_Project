"""colors.py — SME's exact 7-scheme palette + per-location color mapping.

Per user directive (R19), background colors must be followed as per the
original SME project — these constants are the authoritative source.
"""
from __future__ import annotations

COLOR_SCHEMES = {
    "dashboard":   {"title_bg": "#1A2A3A", "header_bg": "#2D4A6A",
                    "total_bg": "#F0C040", "total_fg": "#000000"},
    "brown_field": {"title_bg": "#0F2D52", "header_bg": "#1E5799",
                    "total_bg": "#BDD7F0", "total_fg": "#000000"},
    "train_j":     {"title_bg": "#4A2E00", "header_bg": "#A0620A",
                    "total_bg": "#FDE8A0", "total_fg": "#000000"},
    "train_k":     {"title_bg": "#0A2E1A", "header_bg": "#1A6B48",
                    "total_bg": "#B3F0D8", "total_fg": "#000000"},
    "session":     {"title_bg": "#2D1A52", "header_bg": "#5B2D8E",
                    "total_bg": "#E5D0F0", "total_fg": "#000000"},
    "execution":   {"title_bg": "#3A0A0A", "header_bg": "#8E1A1A",
                    "total_bg": "#F5C6C6", "total_fg": "#000000"},
    "overview":    {"title_bg": "#0A2A2A", "header_bg": "#0E7490",
                    "total_bg": "#A5F3FC", "total_fg": "#000000"},
}

LOC_COLOR_MAP = {
    "Brown Field": "brown_field",
    "TRAIN J":     "train_j",
    "TRAIN K":     "train_k",
}

TABLE_COLOR_MAP = {
    "sme_equipment": "brown_field",
    "sme_recipe":    "train_j",
    "inventory":     "train_k",
}


def scheme_for_location(loc: str | None) -> str:
    """Return the SME color-scheme key for a location, defaulting to dashboard."""
    if not loc:
        return "dashboard"
    return LOC_COLOR_MAP.get(loc, "dashboard")


def scheme_for_table(table_name: str | None) -> str:
    """Return the SME color-scheme key for a Master Data table."""
    if not table_name:
        return "dashboard"
    return TABLE_COLOR_MAP.get(table_name, "dashboard")
