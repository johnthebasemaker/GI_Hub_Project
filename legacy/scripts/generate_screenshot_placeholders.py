#!/usr/bin/env python3
"""
scripts/generate_screenshot_placeholders.py — Phase 7F.

Generate the seed PIL placeholder PNGs under docs/screenshots/. Each
placeholder is a brand-coloured rectangle with the target filename
displayed centrally + a "Screenshot pending" caption. Replace any of
these files with a real capture from the running app at any time —
the manual PDF builder will pick up the new image automatically.

Usage:
    python scripts/generate_screenshot_placeholders.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# Brand tokens — must match config.py / build_manual_pdf.py
BRAND_NAVY = (0,   51, 102)
BRAND_GOLD = (212, 175, 55)
SURFACE    = (244, 247, 251)
TEXT_DARK  = (30,  30,  30)
TEXT_MUTED = (120, 120, 130)

OUT_DIR = Path(__file__).resolve().parents[1] / "docs" / "screenshots"

# Recipe-aligned seed list — 3 per primary role chapter per spec Q5(b).
# Filenames are stable contract surface — they're referenced from
# USER_MANUAL.md and the PDF builder will pick up real PNGs replacing
# these placeholders without code changes.
PLACEHOLDERS: list[tuple[str, str]] = [
    # Store Keeper (chapter 4)
    ("sk_consumption_log.png",        "Store Keeper · Consumption Log entry"),
    ("sk_receipt_staging.png",        "Store Keeper · Receipt Staging queue"),
    ("sk_supervisor_requests.png",    "Store Keeper · Supervisor Requests tab"),
    # Supervisor (chapter 5)
    ("supervisor_new_request.png",    "Supervisor · New Material Request form"),
    ("supervisor_my_requests.png",    "Supervisor · My Requests history"),
    ("supervisor_intent_vs_actual.png","Supervisor · Intent vs Actual report"),
    # HOD (chapter 6)
    ("hod_eod_commit.png",            "HOD · EOD Commit review"),
    ("hod_cross_site_inquiry.png",    "HOD · Cross-Site Inquiry with notification indicator"),
    ("hod_employees_tab.png",         "HOD · Site Employees roster"),
    # Logistics (chapter 14)
    ("logistics_create_po.png",       "Logistics · Create PO from PDF or manual entry"),
    ("logistics_assign_warehouse.png","Logistics · Assign PO to Warehouse"),
    ("logistics_open_pos.png",        "Logistics · Open POs dashboard"),
    # Warehouse (chapter 15)
    ("warehouse_receive_goods.png",   "Warehouse · Receive Goods workflow"),
    ("warehouse_prepare_dn.png",      "Warehouse · Prepare Delivery Note"),
    ("warehouse_outbound_dns.png",    "Warehouse · Outbound DN tracking"),
    # Common / shared
    ("notification_bell.png",         "Sidebar · Notifications bell + inbox modal"),
    ("live_dashboard_hero.png",       "Live Dashboard · Hero KPI strip + grid"),
    ("offline_pill.png",              "Connectivity · Offline indicator pill (top-left)"),
]


def _try_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Best-effort font load. macOS Helvetica → DejaVu → PIL default."""
    candidates = [
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/Library/Fonts/Arial.ttf",
    ]
    for p in candidates:
        try:
            return ImageFont.truetype(p, size=size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def _draw(path: Path, caption: str) -> None:
    W, H = 1280, 720  # 16:9 — matches typical desktop captures
    img  = Image.new("RGB", (W, H), SURFACE)
    draw = ImageDraw.Draw(img)

    # Top navy band
    draw.rectangle([(0, 0), (W, 70)], fill=BRAND_NAVY)
    # Gold accent strip
    draw.rectangle([(0, 70), (W, 76)], fill=BRAND_GOLD)
    # Bottom navy thin band
    draw.rectangle([(0, H - 50), (W, H)], fill=BRAND_NAVY)

    # Brand title (top-left)
    f_brand = _try_font(22)
    draw.text((28, 22), "GI Hub  ·  Manual Placeholder", fill=(255, 255, 255), font=f_brand)

    # Big "SCREENSHOT PENDING" centre text
    f_big = _try_font(56)
    msg1 = "SCREENSHOT PENDING"
    bbox1 = draw.textbbox((0, 0), msg1, font=f_big)
    w1 = bbox1[2] - bbox1[0]
    draw.text(((W - w1) / 2, 230), msg1, fill=BRAND_NAVY, font=f_big)

    # Target filename — monospace-ish presentation
    f_path = _try_font(32)
    msg2 = path.name
    bbox2 = draw.textbbox((0, 0), msg2, font=f_path)
    w2 = bbox2[2] - bbox2[0]
    draw.text(((W - w2) / 2, 320), msg2, fill=TEXT_DARK, font=f_path)

    # Caption (Audience hint)
    f_cap = _try_font(20)
    bbox3 = draw.textbbox((0, 0), caption, font=f_cap)
    w3 = bbox3[2] - bbox3[0]
    draw.text(((W - w3) / 2, 380), caption, fill=TEXT_MUTED, font=f_cap)

    # Footer hint
    f_foot = _try_font(16)
    hint = ("Replace this file with a real screenshot from the app — "
            "the manual PDF builder will pick it up on next render.")
    bbox4 = draw.textbbox((0, 0), hint, font=f_foot)
    w4 = bbox4[2] - bbox4[0]
    draw.text(((W - w4) / 2, H - 36), hint, fill=(255, 255, 255), font=f_foot)

    img.save(path, "PNG", optimize=True)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for fname, caption in PLACEHOLDERS:
        path = OUT_DIR / fname
        _draw(path, caption)
        print(f"  wrote {path.relative_to(OUT_DIR.parents[1])} ({path.stat().st_size:,} bytes)")
    print(f"\n✅ Generated {len(PLACEHOLDERS)} placeholder(s) at {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
