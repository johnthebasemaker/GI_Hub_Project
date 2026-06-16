#!/usr/bin/env python3
"""
build_sop_pdf.py — Render SOP.md as a branded fpdf2 PDF.

Usage:
    python build_sop_pdf.py                       # writes GI_Hub_SOP.pdf
    python build_sop_pdf.py --in CUSTOM.md        # different source
    python build_sop_pdf.py --out path/to/x.pdf   # different output

Implementation note
-------------------
The PDF rendering machinery (markdown parser, layout engine, table renderer,
TOC, cover, headers/footers) lives in build_manual_pdf.py. We import that
module and selectively rebind a few constants — DOC_TITLE, APP_VERSION,
the parameter defaults — before invoking the existing build_manual_pdf()
function. This keeps the SOP PDF visually identical to the manual PDF
(same brand panel, same typography, same TOC style) while staying DRY:
no 700+ lines of duplicated rendering code.

The SOP target audience is operators, not engineers. The manual stays
a strict UI catalogue; the SOP is for *what to do at 8 AM Tuesday*.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import build_manual_pdf as _engine


# ---------------------------------------------------------------------------
# SOP-specific branding overrides
# ---------------------------------------------------------------------------
# Latin-1-only: the cover renderer in build_manual_pdf.py does NOT funnel
# DOC_TITLE through _ascii() before rendering, so any non-Latin-1 character
# (em-dash, smart quotes, etc.) would crash the build. Keep the title ASCII.
SOP_DOC_TITLE   = "Standard Operating Procedure - Procurement Chain"
SOP_APP_VERSION = "3.0"   # tracks the Hub release the SOP was written against
SOP_DEFAULT_IN  = "SOP.md"
SOP_DEFAULT_OUT = "GI_Hub_SOP.pdf"


def build_sop_pdf(md_text: str) -> bytes:
    """Render SOP markdown to a PDF byte blob.

    Mutates the engine module's title/version constants temporarily so the
    cover page, header strip, and footer reflect the SOP rather than the
    user manual. The original values are restored before return so a later
    call to build_manual_pdf() in the same process still produces a
    correctly branded manual PDF.
    """
    saved = (_engine.DOC_TITLE, _engine.APP_VERSION)
    try:
        _engine.DOC_TITLE   = SOP_DOC_TITLE
        _engine.APP_VERSION = SOP_APP_VERSION
        return _engine.build_manual_pdf(md_text)
    finally:
        _engine.DOC_TITLE, _engine.APP_VERSION = saved


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render SOP.md as a branded GI Hub PDF.",
    )
    parser.add_argument("--in",  dest="src", default=SOP_DEFAULT_IN,
                        help=f"Markdown source (default: {SOP_DEFAULT_IN})")
    parser.add_argument("--out", dest="dst", default=SOP_DEFAULT_OUT,
                        help=f"PDF output path (default: {SOP_DEFAULT_OUT})")
    args = parser.parse_args(argv)

    src = Path(args.src)
    if not src.exists():
        print(f"❌ Source not found: {src}", file=sys.stderr)
        return 2

    md = src.read_text(encoding="utf-8")
    print(f"Reading  {src} ({len(md):,} bytes)")
    print(f"Parsing  {md.count(chr(10)):,} lines …")

    try:
        pdf_bytes = build_sop_pdf(md)
    except Exception as e:
        print(f"❌ PDF build failed: {type(e).__name__}: {e}",
              file=sys.stderr)
        return 1

    out = Path(args.dst)
    out.write_bytes(pdf_bytes)
    print(f"Written  {out} ({len(pdf_bytes):,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
