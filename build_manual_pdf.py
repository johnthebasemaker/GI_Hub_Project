#!/usr/bin/env python3
"""
build_manual_pdf.py — Render USER_MANUAL.md as a branded fpdf2 PDF.

Usage:
    python build_manual_pdf.py                       # writes GI_Hub_User_Manual.pdf
    python build_manual_pdf.py --in CUSTOM.md        # different source
    python build_manual_pdf.py --out path/to/x.pdf   # different output

Public API:
    build_manual_pdf(md_text: str) -> bytes
        Used by Admin Portal → Settings → "📄 Download User Manual PDF"
        so the manual can be regenerated without leaving the app.

Design choices
--------------
- fpdf2 only (no LaTeX / pandoc / WeasyPrint). The package is already
  in requirements.txt and produces a single binary blob.
- Cover page: brand navy panel + gold accent + title + version + date.
- Auto-generated TOC scanning `# ` and `## ` headings, with dotted
  leaders and page numbers (resolved in a second pass).
- Per-page header with the chapter title and a footer with page number.
- Markdown subset supported: headings (#..####), paragraphs, bold + italic
  + inline code (substring rendering inside paragraphs), bullet lists,
  GFM-style tables, fenced code blocks. Anything fancier degrades to
  plain text rather than crashing.
"""

from __future__ import annotations

import argparse
import datetime
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from fpdf import FPDF

# ---------------------------------------------------------------------------
# Brand tokens — must match config.py
# ---------------------------------------------------------------------------
BRAND_NAVY        = (0,   51,  102)   # #003366
BRAND_NAVY_DARK   = (0,   31,   64)   # #001F40
BRAND_GOLD        = (212, 175,  55)   # #D4AF37
BRAND_GOLD_LIGHT  = (240, 208,  96)   # #F0D060
TEXT_DARK         = (30,   30,  30)
TEXT_BODY         = (55,   55,  60)
TEXT_MUTED        = (120, 120, 130)
CODE_BG           = (245, 246, 248)
CODE_BORDER       = (210, 215, 225)
TABLE_HEADER_BG   = BRAND_NAVY
TABLE_ROW_ALT_BG  = (244, 247, 251)
TABLE_BORDER      = (210, 220, 232)
RULE_LINE         = (220, 224, 232)

APP_NAME    = "General Industries Hub"
APP_VERSION = "2.0"
DOC_TITLE   = "Product Manual & User Catalogue"
PAGE_W_MM   = 210
PAGE_H_MM   = 297
MARGIN_MM   = 18


# ---------------------------------------------------------------------------
# Phase 7F — Role-segregated manual recipes
# ---------------------------------------------------------------------------
# Each entry tells slice_markdown_for_role() which top-level chapters
# (matched against the literal text after "# " on top-level headings) to
# keep when building that role's booklet. Universal preamble (chapters 1–3)
# is included in every site-level role so the printed booklet stands alone.
# Admin = "ALL" → falls through to the master full PDF behaviour.
ROLE_MANUAL_RECIPES = {
    "store_keeper": {
        "title":    "Store Keeper Manual",
        "icon":     "🗝️",
        "audience": "Site-level material handling, daily entries, returnables.",
        "chapters": [
            "1. Introduction & System Overview",
            "2. Roles, Permissions & Page Access",
            "3. Login, Sidebar & Common Elements",
            "4. Store Keeper Manual",
            "10. Data Model & Concept Reference",
            "11. Status Codes, Reason Codes & Glossary",
            "12. FAQ — Master Index by Role",
        ],
    },
    "supervisor": {
        "title":    "Supervisor Manual",
        "icon":     "🛡️",
        "audience": "Field supervision, material requests for workers.",
        "chapters": [
            "1. Introduction & System Overview",
            "3. Login, Sidebar & Common Elements",
            "5. Supervisor Manual",
            "11. Status Codes, Reason Codes & Glossary",
            "12. FAQ — Master Index by Role",
        ],
    },
    "hod": {
        "title":    "HOD Manual",
        "icon":     "🏛️",
        "audience": "Head of Department oversight: approvals, EOD, reports.",
        "chapters": [
            "1. Introduction & System Overview",
            "2. Roles, Permissions & Page Access",
            "3. Login, Sidebar & Common Elements",
            "6. HOD (Head of Department) Manual",
            "8. Reports Module — Detailed Reference",
            "10. Data Model & Concept Reference",
            "11. Status Codes, Reason Codes & Glossary",
            "12. FAQ — Master Index by Role",
        ],
    },
    "logistics": {
        "title":    "Logistics Portal Manual",
        "icon":     "🚚",
        "audience": "Purchase orders, vendor management, procurement chain.",
        "chapters": [
            "1. Introduction & System Overview",
            "3. Login, Sidebar & Common Elements",
            "14. Logistics Portal Manual",
            "16. Cross-Role Procurement Walk-through",
            "11. Status Codes, Reason Codes & Glossary",
        ],
    },
    "warehouse_user": {
        "title":    "Warehouse Portal Manual",
        "icon":     "🏭",
        "audience": "Receive goods, prepare delivery notes, returns to vendor.",
        "chapters": [
            "1. Introduction & System Overview",
            "3. Login, Sidebar & Common Elements",
            "15. Warehouse Portal Manual",
            "16. Cross-Role Procurement Walk-through",
            "11. Status Codes, Reason Codes & Glossary",
        ],
    },
    "admin": {
        "title":    "Admin Manual (Full)",
        "icon":     "👑",
        "audience": "Full reference — every chapter, every page, every workflow.",
        "chapters": "ALL",
    },
}

# Default screenshot library location (relative to repo root).
SCREENSHOTS_DIR = "docs/screenshots"


# ---------------------------------------------------------------------------
# Block types — internal IR after Markdown is parsed
# ---------------------------------------------------------------------------
@dataclass
class Block:
    kind: str                    # h1 | h2 | h3 | h4 | p | ul | code | table | hr | blank
    text: str = ""
    items: list = field(default_factory=list)   # ul → list[str]; table → list[list[str]]


# ---------------------------------------------------------------------------
# Markdown parser — line-based, line-by-line. Enough for our manual.
# ---------------------------------------------------------------------------
_HEADING_RE = re.compile(r"^(#{1,4})\s+(.*?)\s*$")
_FENCE_RE   = re.compile(r"^```")
_BULLET_RE  = re.compile(r"^\s*[-*]\s+(.+)$")
_NUMLI_RE   = re.compile(r"^\s*\d+\.\s+(.+)$")
_RULE_RE    = re.compile(r"^\s*-{3,}\s*$|^\s*\*{3,}\s*$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*)+\|?\s*$")
# Phase 7F — standalone Markdown image line: ![alt](path)
_IMAGE_RE   = re.compile(r"^!\[(.*?)\]\((.+?)\)\s*$")


def _is_table_row(line: str) -> bool:
    s = line.strip()
    return s.startswith("|") and s.count("|") >= 2


def _split_table_row(line: str) -> list[str]:
    s = line.strip().strip("|")
    return [c.strip() for c in s.split("|")]


def parse_markdown(md: str) -> list[Block]:
    """Convert raw markdown to a list of Block IR records."""
    lines = md.splitlines()
    blocks: list[Block] = []
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]
        stripped = line.strip()

        # blank line
        if not stripped:
            blocks.append(Block("blank"))
            i += 1
            continue

        # horizontal rule
        if _RULE_RE.match(line):
            blocks.append(Block("hr"))
            i += 1
            continue

        # fenced code block
        if _FENCE_RE.match(stripped):
            i += 1
            buf = []
            while i < n and not _FENCE_RE.match(lines[i].strip()):
                buf.append(lines[i])
                i += 1
            if i < n:  # skip the closing ```
                i += 1
            blocks.append(Block("code", "\n".join(buf)))
            continue

        # heading
        m = _HEADING_RE.match(line)
        if m:
            level = len(m.group(1))
            blocks.append(Block(f"h{level}", m.group(2)))
            i += 1
            continue

        # table — header row + separator + 1+ body rows
        if _is_table_row(line) and i + 1 < n and _TABLE_SEP_RE.match(lines[i + 1]):
            header = _split_table_row(line)
            i += 2  # past header + separator
            rows = []
            while i < n and _is_table_row(lines[i]):
                rows.append(_split_table_row(lines[i]))
                i += 1
            blocks.append(Block("table", items=[header] + rows))
            continue

        # Phase 7F — standalone image line: ![alt](docs/screenshots/foo.png)
        # Inline image references (mid-paragraph) are ignored — only own-line
        # images render as captioned screenshots in the PDF.
        im = _IMAGE_RE.match(line)
        if im:
            alt  = im.group(1).strip()
            path = im.group(2).strip()
            blocks.append(Block("img", text=path, items=[alt]))
            i += 1
            continue

        # bullet list (collect contiguous bullets)
        if _BULLET_RE.match(line) or _NUMLI_RE.match(line):
            items = []
            while i < n:
                bm = _BULLET_RE.match(lines[i])
                nm = _NUMLI_RE.match(lines[i])
                if bm:
                    items.append(("-", bm.group(1)))
                elif nm:
                    items.append((f"{len(items)+1}.", nm.group(1)))
                else:
                    break
                i += 1
            blocks.append(Block("ul", items=items))
            continue

        # paragraph — collect until blank or block-starter
        buf = [line]
        i += 1
        while i < n:
            nxt = lines[i]
            ns  = nxt.strip()
            if (not ns or _HEADING_RE.match(nxt) or _FENCE_RE.match(ns)
                    or _RULE_RE.match(nxt) or _BULLET_RE.match(nxt)
                    or _NUMLI_RE.match(nxt) or _is_table_row(nxt)):
                break
            buf.append(nxt)
            i += 1
        blocks.append(Block("p", " ".join(b.strip() for b in buf)))

    return blocks


# ---------------------------------------------------------------------------
# Phase 7F — Markdown slicer for role-segregated booklets
# ---------------------------------------------------------------------------
def slice_markdown_for_role(role_key: str, md_text: str) -> str:
    """Return only the chapters listed in ROLE_MANUAL_RECIPES[role_key].

    "Chapter" = a top-level `# N. Title` heading. The slicer walks the
    source line-by-line:
      - When a `# Title` line matches the recipe → enable "include" mode.
      - When the next `# ` line is encountered → re-evaluate.
      - Non-numbered top-level headings (e.g., `# Table of Contents`)
        are silently skipped — the rebuilt PDF gets its own TOC anyway.

    role_key == "admin" or unknown → returns md_text unchanged so the
    caller drops through to the existing full-PDF behaviour.
    """
    recipe = ROLE_MANUAL_RECIPES.get(role_key)
    if not recipe or recipe.get("chapters") == "ALL":
        return md_text
    wanted = set(recipe["chapters"])

    out_lines: list[str] = []
    include = False
    for line in md_text.splitlines():
        if line.startswith("# ") and not line.startswith("## "):
            title = line[2:].strip()
            include = title in wanted
            if include:
                out_lines.append(line)
            continue
        if include:
            out_lines.append(line)
    return "\n".join(out_lines)


# ---------------------------------------------------------------------------
# Inline markdown — handles **bold**, *italic*, `code` substrings safely.
# Returns a list of (text, style_dict) runs the renderer can write in order.
# ---------------------------------------------------------------------------
_INLINE_PATTERNS = [
    (re.compile(r"\*\*(.+?)\*\*"),   {"bold": True}),
    (re.compile(r"`([^`]+)`"),       {"code": True}),
    (re.compile(r"\*(.+?)\*"),       {"italic": True}),
    (re.compile(r"_([^_]+)_"),       {"italic": True}),
]


def _strip_md_punct(text: str) -> str:
    """Last-resort cleanup for any markdown punctuation that slipped through."""
    return (text
            .replace("**", "")
            .replace("`", "")
            .replace("~~", ""))


def inline_runs(text: str) -> list[tuple[str, dict]]:
    """Split a paragraph into styled runs. Greedy left-to-right."""
    runs: list[tuple[str, dict]] = []
    pos = 0
    while pos < len(text):
        best_match = None
        best_style = {}
        for pat, style in _INLINE_PATTERNS:
            m = pat.search(text, pos)
            if m and (best_match is None or m.start() < best_match.start()):
                best_match = m
                best_style = style
        if best_match is None:
            tail = text[pos:]
            if tail:
                runs.append((_strip_md_punct(tail), {}))
            break
        if best_match.start() > pos:
            runs.append((_strip_md_punct(text[pos:best_match.start()]), {}))
        runs.append((best_match.group(1), best_style))
        pos = best_match.end()
    return runs


# ---------------------------------------------------------------------------
# PDF class — header, footer, cover, TOC, content rendering
# ---------------------------------------------------------------------------
class ManualPDF(FPDF):
    def __init__(self):
        super().__init__(orientation="P", unit="mm", format="A4")
        self.set_margins(MARGIN_MM, MARGIN_MM, MARGIN_MM)
        self.set_auto_page_break(auto=True, margin=22)
        self.alias_nb_pages()
        self.current_chapter = ""
        self.is_cover = False
        self.skip_header = False  # set True on cover + TOC

    # ── Header + footer ────────────────────────────────────────────────────
    def header(self):
        if self.skip_header:
            return
        # Small gold accent line
        self.set_draw_color(*BRAND_GOLD)
        self.set_line_width(0.4)
        self.line(MARGIN_MM, 10, PAGE_W_MM - MARGIN_MM, 10)
        # Brand title left, chapter right
        self.set_xy(MARGIN_MM, 12)
        self.set_font("helvetica", "B", 9)
        self.set_text_color(*BRAND_NAVY)
        self.cell(0, 5, APP_NAME, align="L")
        if self.current_chapter:
            self.set_x(MARGIN_MM)
            self.set_font("helvetica", "I", 9)
            self.set_text_color(*TEXT_MUTED)
            self.cell(0, 5, _ascii(self.current_chapter), align="R")
        self.ln(8)

    def footer(self):
        if self.skip_header:
            return
        self.set_y(-13)
        self.set_draw_color(*RULE_LINE)
        self.set_line_width(0.2)
        self.line(MARGIN_MM, self.get_y(), PAGE_W_MM - MARGIN_MM, self.get_y())
        self.ln(2)
        self.set_font("helvetica", "", 8)
        self.set_text_color(*TEXT_MUTED)
        self.cell(0, 5, f"{APP_NAME}  ·  v{APP_VERSION}", align="L")
        self.set_y(-10)
        self.cell(0, 5, f"Page {self.page_no()} / {{nb}}", align="R")

    # ── Cover page ─────────────────────────────────────────────────────────
    def render_cover(self):
        self.skip_header = True
        # Cover renders content below the normal page-break margin — disable
        # auto-break for this page so the footer band stays put.
        self.set_auto_page_break(auto=False)
        self.add_page()
        # Top navy panel
        self.set_fill_color(*BRAND_NAVY)
        self.rect(0, 0, PAGE_W_MM, 110, "F")
        # Gold accent strip
        self.set_fill_color(*BRAND_GOLD)
        self.rect(0, 110, PAGE_W_MM, 3, "F")
        # Bottom subtle navy
        self.set_fill_color(*BRAND_NAVY_DARK)
        self.rect(0, PAGE_H_MM - 30, PAGE_W_MM, 30, "F")

        # Brand text
        self.set_text_color(255, 255, 255)
        self.set_xy(MARGIN_MM, 40)
        self.set_font("helvetica", "B", 28)
        self.cell(0, 14, APP_NAME, align="L")
        self.ln(16)
        self.set_x(MARGIN_MM)
        self.set_font("helvetica", "", 14)
        self.set_text_color(*BRAND_GOLD_LIGHT)
        self.cell(0, 8, "Enterprise Inventory Management", align="L")

        # Doc title block
        self.set_xy(MARGIN_MM, 145)
        self.set_text_color(*BRAND_NAVY)
        self.set_font("helvetica", "B", 24)
        self.multi_cell(0, 12, DOC_TITLE, align="L")
        self.ln(4)
        self.set_x(MARGIN_MM)
        self.set_font("helvetica", "", 12)
        self.set_text_color(*TEXT_MUTED)
        self.cell(0, 7, _ascii(f"Version {APP_VERSION}  ·  "
                                f"Generated {datetime.date.today().isoformat()}"), align="L")

        # Footer band text
        self.set_xy(MARGIN_MM, PAGE_H_MM - 20)
        self.set_text_color(*BRAND_GOLD_LIGHT)
        self.set_font("helvetica", "I", 10)
        self.cell(0, 6, _ascii("Confidential — for authorized personnel only"), align="L")
        self.set_x(MARGIN_MM)
        self.set_y(PAGE_H_MM - 14)
        self.set_text_color(255, 255, 255)
        self.set_font("helvetica", "", 9)
        self.cell(0, 5, _ascii("GI Hub  ·  Streamlit + SQLite + Twilio + Ollama  ·  Multi-Site ERP"), align="L")
        self.skip_header = False
        # Re-enable auto-break for subsequent pages
        self.set_auto_page_break(auto=True, margin=22)

    # ── Role-personalised cover (Phase 7F) ─────────────────────────────────
    def render_cover_for_role(self, recipe: dict) -> None:
        """Variant of render_cover that swaps DOC_TITLE for the role-specific
        title ("Store Keeper Manual") and surfaces an audience subtitle. Same
        navy/gold visual treatment so booklets look like one family."""
        title = recipe.get("title") or DOC_TITLE
        audience = recipe.get("audience") or ""
        # Strip emoji from the title body (latin-1 sanitiser would drop them
        # anyway, but doing it explicitly keeps spacing clean).
        title_clean = _ascii(title)

        self.skip_header = True
        self.set_auto_page_break(auto=False)
        self.add_page()
        # Top navy panel
        self.set_fill_color(*BRAND_NAVY)
        self.rect(0, 0, PAGE_W_MM, 110, "F")
        # Gold accent strip
        self.set_fill_color(*BRAND_GOLD)
        self.rect(0, 110, PAGE_W_MM, 3, "F")
        # Bottom subtle navy
        self.set_fill_color(*BRAND_NAVY_DARK)
        self.rect(0, PAGE_H_MM - 30, PAGE_W_MM, 30, "F")

        # Brand text
        self.set_text_color(255, 255, 255)
        self.set_xy(MARGIN_MM, 40)
        self.set_font("helvetica", "B", 28)
        self.cell(0, 14, APP_NAME, align="L")
        self.ln(16)
        self.set_x(MARGIN_MM)
        self.set_font("helvetica", "", 14)
        self.set_text_color(*BRAND_GOLD_LIGHT)
        self.cell(0, 8, "Enterprise Inventory Management", align="L")

        # Role-specific title
        self.set_xy(MARGIN_MM, 140)
        self.set_text_color(*BRAND_NAVY)
        self.set_font("helvetica", "B", 26)
        self.multi_cell(0, 13, title_clean, align="L")
        if audience:
            self.ln(2)
            self.set_x(MARGIN_MM)
            self.set_font("helvetica", "I", 12)
            self.set_text_color(*TEXT_BODY)
            self.multi_cell(0, 6, _ascii(audience), align="L")
        self.ln(4)
        self.set_x(MARGIN_MM)
        self.set_font("helvetica", "", 11)
        self.set_text_color(*TEXT_MUTED)
        self.cell(0, 6, _ascii(f"Version {APP_VERSION}  ·  "
                                f"Generated {datetime.date.today().isoformat()}"),
                  align="L")

        # Footer band text
        self.set_xy(MARGIN_MM, PAGE_H_MM - 20)
        self.set_text_color(*BRAND_GOLD_LIGHT)
        self.set_font("helvetica", "I", 10)
        self.cell(0, 6, _ascii("Confidential — for authorized personnel only"),
                  align="L")
        self.set_x(MARGIN_MM)
        self.set_y(PAGE_H_MM - 14)
        self.set_text_color(255, 255, 255)
        self.set_font("helvetica", "", 9)
        self.cell(0, 5, _ascii(f"GI Hub  ·  Role booklet  ·  {title_clean}"),
                  align="L")
        self.skip_header = False
        self.set_auto_page_break(auto=True, margin=22)

    # ── TOC (rendered after content with resolved page numbers) ────────────
    def render_toc(self, entries: list[tuple[int, str, int]]):
        """entries: list of (level, title, page_no). level ∈ {1,2}."""
        self.skip_header = True
        self.add_page()
        self.set_text_color(*BRAND_NAVY)
        self.set_font("helvetica", "B", 22)
        self.cell(0, 12, "Contents", align="L")
        self.ln(14)
        self.set_draw_color(*BRAND_GOLD)
        self.set_line_width(0.6)
        self.line(MARGIN_MM, self.get_y(), MARGIN_MM + 30, self.get_y())
        self.ln(8)

        for lvl, title, pg in entries:
            if lvl == 1:
                self.ln(2)
                self.set_font("helvetica", "B", 12)
                self.set_text_color(*BRAND_NAVY)
                indent = 0
            else:
                self.set_font("helvetica", "", 10.5)
                self.set_text_color(*TEXT_BODY)
                indent = 8

            x_start = MARGIN_MM + indent
            self.set_x(x_start)
            usable = PAGE_W_MM - 2 * MARGIN_MM - indent

            title_clean = _ascii(title)
            page_str = str(pg)
            # Width of the page number cell
            self.set_font("helvetica", "B" if lvl == 1 else "", 11 if lvl == 1 else 10.5)
            pg_w = self.get_string_width(page_str) + 4

            # Truncate title if too long
            title_max_w = usable - pg_w - 4
            self.set_font("helvetica", "B" if lvl == 1 else "", 12 if lvl == 1 else 10.5)
            while self.get_string_width(title_clean) > title_max_w and len(title_clean) > 4:
                title_clean = title_clean[:-2]
            t_w = self.get_string_width(title_clean)

            self.cell(t_w, 6, title_clean, align="L")
            # Dotted leader
            dots_w = max(2, usable - t_w - pg_w - 4)
            self.set_font("helvetica", "", 9)
            self.set_text_color(*TEXT_MUTED)
            dot_str = "." * max(2, int(dots_w / 1.6))
            self.cell(dots_w, 6, " " + dot_str + " ", align="C")
            # Page number, right-aligned
            self.set_font("helvetica", "B" if lvl == 1 else "", 11 if lvl == 1 else 10.5)
            self.set_text_color(*BRAND_NAVY)
            self.cell(pg_w, 6, page_str, align="R", new_x="LMARGIN", new_y="NEXT")
        self.skip_header = False

    # ── Block renderers ───────────────────────────────────────────────────
    def render_h1(self, text: str):
        # Each H1 starts a new chapter on a new page.
        self.add_page()
        self.current_chapter = text
        self.set_fill_color(*BRAND_NAVY)
        self.rect(MARGIN_MM, self.get_y(), 4, 14, "F")
        self.set_xy(MARGIN_MM + 7, self.get_y())
        self.set_text_color(*BRAND_NAVY)
        self.set_font("helvetica", "B", 22)
        self.multi_cell(0, 11, _ascii(text), align="L")
        self.ln(2)
        self.set_draw_color(*BRAND_GOLD)
        self.set_line_width(0.5)
        self.line(MARGIN_MM, self.get_y(), MARGIN_MM + 30, self.get_y())
        self.ln(6)

    def render_h2(self, text: str):
        self.ln(4)
        self.set_text_color(*BRAND_NAVY)
        self.set_font("helvetica", "B", 15)
        self.multi_cell(0, 8, _ascii(text), align="L")
        self.ln(1)
        self.set_draw_color(*RULE_LINE)
        self.set_line_width(0.3)
        self.line(MARGIN_MM, self.get_y(), PAGE_W_MM - MARGIN_MM, self.get_y())
        self.ln(3)

    def render_h3(self, text: str):
        self.ln(3)
        self.set_text_color(*BRAND_NAVY)
        self.set_font("helvetica", "B", 12)
        self.multi_cell(0, 7, _ascii(text), align="L")
        self.ln(1)

    def render_h4(self, text: str):
        self.ln(2)
        self.set_text_color(*TEXT_DARK)
        self.set_font("helvetica", "BI", 11)
        self.multi_cell(0, 6, _ascii(text), align="L")
        self.ln(1)

    def render_paragraph(self, text: str):
        # Use a single multi_cell so wrapping respects right margin even
        # when the paragraph spans a page break. We render inline styles by
        # stripping the markdown punctuation rather than trying to mix
        # fonts mid-paragraph — fpdf2's write() can overflow the right edge
        # when fonts change between runs.
        self.set_text_color(*TEXT_BODY)
        self.set_font("helvetica", "", 10.5)
        usable_w = PAGE_W_MM - 2 * MARGIN_MM
        # Always start at the left margin
        self.set_x(MARGIN_MM)
        self.multi_cell(usable_w, 5.5, _ascii(_strip_md_punct(text)))
        self.ln(3)

    def render_list(self, items: list):
        self.set_text_color(*TEXT_BODY)
        usable_w = PAGE_W_MM - 2 * MARGIN_MM
        bullet_w = 6
        body_w   = usable_w - bullet_w
        for bullet, body in items:
            # Always re-anchor to the left margin before drawing the bullet
            self.set_x(MARGIN_MM)
            # Capture y BEFORE the bullet so the body can align even if it wraps.
            y_start = self.get_y()
            # Bullet (navy, bold)
            self.set_font("helvetica", "B", 10.5)
            self.set_text_color(*BRAND_NAVY)
            self.cell(bullet_w, 5.5, _ascii(bullet), align="L")
            # Body (regular)
            self.set_font("helvetica", "", 10.5)
            self.set_text_color(*TEXT_BODY)
            self.set_xy(MARGIN_MM + bullet_w, y_start)
            self.multi_cell(body_w, 5.5, _ascii(_strip_md_punct(body)))
            # No manual ln — multi_cell already advanced. Just nudge spacing.
            self.ln(0.5)
        self.ln(2)

    def render_code(self, text: str):
        self.ln(1)
        # Compute height needed
        lines = text.splitlines() or [""]
        line_h = 5
        pad = 3
        h = line_h * len(lines) + pad * 2
        # Page-break if needed
        if self.get_y() + h > PAGE_H_MM - 25:
            self.add_page()
        x = MARGIN_MM
        y = self.get_y()
        w = PAGE_W_MM - 2 * MARGIN_MM
        # Box background + border
        self.set_fill_color(*CODE_BG)
        self.set_draw_color(*CODE_BORDER)
        self.set_line_width(0.2)
        self.rect(x, y, w, h, "DF")
        # Left accent
        self.set_fill_color(*BRAND_GOLD)
        self.rect(x, y, 1.2, h, "F")
        # Text
        self.set_xy(x + 5, y + pad)
        self.set_font("courier", "", 9)
        self.set_text_color(*TEXT_DARK)
        for ln_ in lines:
            self.set_x(x + 5)
            self.cell(w - 6, line_h, _ascii(ln_), align="L")
            self.ln(line_h)
        self.ln(2)

    def render_table(self, rows: list[list[str]]):
        if not rows:
            return
        header, body = rows[0], rows[1:]
        n_cols = len(header)
        if n_cols == 0:
            return
        usable = PAGE_W_MM - 2 * MARGIN_MM
        col_w = usable / n_cols
        line_h = 5.5

        # Page break if header doesn't fit
        if self.get_y() + line_h * 3 > PAGE_H_MM - 25:
            self.add_page()

        # Header row
        self.set_fill_color(*TABLE_HEADER_BG)
        self.set_text_color(255, 255, 255)
        self.set_font("helvetica", "B", 9.5)
        self.set_draw_color(*TABLE_BORDER)
        self.set_line_width(0.2)
        for cell in header:
            self.cell(col_w, 7, _ascii(_strip_md_punct(cell))[:60],
                      border=1, align="L", fill=True)
        self.ln(7)

        # Body rows
        self.set_font("helvetica", "", 9)
        self.set_text_color(*TEXT_BODY)
        for ri, row in enumerate(body):
            if self.get_y() + line_h > PAGE_H_MM - 25:
                self.add_page()
                # Re-draw header on new page for readability
                self.set_fill_color(*TABLE_HEADER_BG)
                self.set_text_color(255, 255, 255)
                self.set_font("helvetica", "B", 9.5)
                for cell in header:
                    self.cell(col_w, 7, _ascii(_strip_md_punct(cell))[:60],
                              border=1, align="L", fill=True)
                self.ln(7)
                self.set_font("helvetica", "", 9)
                self.set_text_color(*TEXT_BODY)
            fill = (ri % 2 == 0)
            if fill:
                self.set_fill_color(*TABLE_ROW_ALT_BG)
            cells = (row + [""] * n_cols)[:n_cols]
            # Always anchor table x to the left margin to avoid drift
            x_start = MARGIN_MM
            y_start = self.get_y()
            # Find tallest cell for this row (helvetica char width ~ 1.7mm @ 9pt)
            heights = []
            for cell in cells:
                cell_clean = _ascii(_strip_md_punct(cell))
                w_per_char = 1.7
                chars_per_line = max(1, int((col_w - 3) / w_per_char))
                n_lines = max(1, (len(cell_clean) + chars_per_line - 1) // chars_per_line)
                heights.append(line_h * min(4, n_lines))
            row_h = max(heights)
            # Background + borders first, content next, so wrapping never
            # paints over neighbouring columns.
            for i in range(n_cols):
                self.rect(x_start + i * col_w, y_start, col_w, row_h, "DF" if fill else "D")
            for i, cell in enumerate(cells):
                cell_clean = _ascii(_strip_md_punct(cell))
                # Hard truncate so multi_cell can never overflow into the next col
                w_per_char = 1.7
                max_chars = int((col_w - 3) / w_per_char) * 4
                if len(cell_clean) > max_chars:
                    cell_clean = cell_clean[:max_chars - 1] + "..."
                self.set_xy(x_start + i * col_w + 1.5, y_start + 0.8)
                self.multi_cell(col_w - 3, line_h, cell_clean, align="L")
            self.set_xy(MARGIN_MM, y_start + row_h)
        self.ln(2)

    def render_hr(self):
        self.ln(2)
        self.set_draw_color(*RULE_LINE)
        self.set_line_width(0.3)
        self.line(MARGIN_MM, self.get_y(), PAGE_W_MM - MARGIN_MM, self.get_y())
        self.ln(4)

    # ── Phase 7F — screenshot rendering ────────────────────────────────────
    def render_image(self, path: str, caption: str = "") -> None:
        """Render a screenshot scaled to 80% of body width with a caption.

        Missing files (or any other PIL/fpdf error) render a small grey
        placeholder card rather than crashing the build — lets us ship
        the recipes + chapter refs before every screenshot exists on disk.
        """
        usable_w  = PAGE_W_MM - 2 * MARGIN_MM
        img_w     = usable_w * 0.8
        max_h     = 90  # mm — keeps two images per page comfortable

        # Page-break guard: estimate target height and break early if needed.
        target_h = max_h + 12  # image + caption + spacing
        if self.get_y() + target_h > (PAGE_H_MM - 25):
            self.add_page()

        x_start = MARGIN_MM + (usable_w - img_w) / 2
        y_start = self.get_y()

        from pathlib import Path as _P
        ok = False
        try:
            if path and _P(path).exists():
                # Determine intrinsic aspect via PIL so the image doesn't
                # distort — fpdf accepts h=0 to preserve aspect, but we
                # also want to clamp to max_h.
                try:
                    from PIL import Image as _PILImage
                    with _PILImage.open(path) as _im:
                        iw, ih = _im.size
                    if iw > 0 and ih > 0:
                        h_at_w = img_w * (ih / iw)
                        if h_at_w > max_h:
                            scale = max_h / h_at_w
                            draw_w = img_w * scale
                            draw_h = max_h
                            x_draw = MARGIN_MM + (usable_w - draw_w) / 2
                        else:
                            draw_w = img_w
                            draw_h = h_at_w
                            x_draw = x_start
                        self.image(path, x=x_draw, y=y_start,
                                   w=draw_w, h=draw_h)
                        self.set_y(y_start + draw_h)
                        ok = True
                except Exception:
                    pass
                if not ok:
                    # PIL probe failed — let fpdf size it natively, clamped width.
                    self.image(path, x=x_start, y=y_start, w=img_w)
                    self.set_y(y_start + max_h)
                    ok = True
        except Exception:
            ok = False

        if not ok:
            # Placeholder card — neutral grey rectangle with the missing path.
            self.set_fill_color(244, 246, 248)
            self.set_draw_color(*RULE_LINE)
            self.set_line_width(0.3)
            box_h = 40
            self.rect(x_start, y_start, img_w, box_h, "DF")
            self.set_xy(x_start, y_start + box_h / 2 - 6)
            self.set_font("helvetica", "I", 9)
            self.set_text_color(*TEXT_MUTED)
            label = f"[Screenshot pending: {path}]"
            self.cell(img_w, 5, _ascii(label), align="C")
            self.set_y(y_start + box_h)

        # Caption — italic + muted + centred
        if caption:
            self.ln(1)
            self.set_x(MARGIN_MM)
            self.set_font("helvetica", "I", 9)
            self.set_text_color(*TEXT_MUTED)
            self.multi_cell(usable_w, 4.5, _ascii(caption), align="C")
        self.ln(4)


# ---------------------------------------------------------------------------
# Latin-1 sanitiser — fpdf2 core fonts don't support Unicode glyphs like
# em-dashes, smart quotes, emoji, etc. Map the common ones; drop the rest.
# (We could load a TTF unicode font, but that doubles the bundle size.)
# ---------------------------------------------------------------------------
_REPLACE = {
    "—": "-", "–": "-", "‐": "-", "‑": "-",
    "“": '"', "”": '"', "„": '"', "‟": '"',
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "•": "*", "·": "-", "…": "...",
    "→": "->", "←": "<-", "↑": "^", "↓": "v",
    "↩": "<-", "↪": "->",
    "✅": "[OK]", "✓": "[OK]", "✔": "[OK]",
    "❌": "[X]",  "✗": "[X]",  "✘": "[X]",
    "⚠️": "[!]", "⚠": "[!]", "🚫": "[X]",
    "📦": "",   "📋": "",   "📥": "",  "📤": "",
    "📊": "",   "📈": "",   "📉": "",  "📎": "",
    "📷": "",   "📱": "",   "📝": "",  "🔔": "",
    "🛡️": "",   "🛡": "",   "🗝️": "",  "🗝": "",
    "🏛️": "",   "🏛": "",   "🤖": "",  "💰": "",
    "🏷️": "",   "🏷": "",   "🔄": "",  "🔧": "",
    "⚙️": "",    "⚙": "",   "⚡": "",   "🟢": "",
    "🟡": "",    "🔴": "",  "🔥": "",  "🎉": "",
    "👤": "",    "👑": "",  "❄": "",   "📂": "",
    "🧮": "",    "💾": "",  "📨": "",  "🌐": "",
    "🗑": "",    "📍": "",  "✉️": "",  "✉": "",
    "👆": "",    "🚪": "",  "♾": "inf",
    "₪": "SAR", "﷼": "SAR",
    " ": " ", "​": "", "‌": "", "‍": "",
    "﻿": "",
}


def _ascii(text: str) -> str:
    if not text:
        return ""
    for k, v in _REPLACE.items():
        if k in text:
            text = text.replace(k, v)
    # Strip any remaining non-latin1 char so fpdf doesn't crash
    return text.encode("latin-1", "replace").decode("latin-1")


# ---------------------------------------------------------------------------
# Builder — two passes: render content collecting toc → render TOC inserted
# ---------------------------------------------------------------------------
def build_manual_pdf(md_text: str) -> bytes:
    """
    Render the markdown into a branded PDF. Two passes:
      1. Render cover + content, recording (level, title, page_no) per heading.
      2. Generate the TOC page from the recorded entries.

    Because fpdf2 builds pages in order, we render the TOC pages AFTER the
    content and then post-process the PDF by moving them in front of the
    content. fpdf2 doesn't support page moves, so we instead render the
    document twice: first pass to learn page numbers (mostly stable since
    fonts don't change), second pass to inject the TOC at the front.
    """
    blocks = parse_markdown(md_text)

    # ── Pass 1 — render everything, record TOC entries ─────────────────────
    def render(insert_toc: Optional[list] = None) -> tuple[bytes, list]:
        pdf = ManualPDF()
        pdf.render_cover()
        toc: list[tuple[int, str, int]] = []
        if insert_toc is not None:
            pdf.render_toc(insert_toc)

        for blk in blocks:
            if blk.kind == "h1":
                pdf.render_h1(blk.text)
                toc.append((1, blk.text, pdf.page_no()))
            elif blk.kind == "h2":
                pdf.render_h2(blk.text)
                toc.append((2, blk.text, pdf.page_no()))
            elif blk.kind == "h3":
                pdf.render_h3(blk.text)
            elif blk.kind == "h4":
                pdf.render_h4(blk.text)
            elif blk.kind == "p":
                pdf.render_paragraph(blk.text)
            elif blk.kind == "ul":
                pdf.render_list(blk.items)
            elif blk.kind == "code":
                pdf.render_code(blk.text)
            elif blk.kind == "table":
                pdf.render_table(blk.items)
            elif blk.kind == "hr":
                pdf.render_hr()
            elif blk.kind == "img":
                # Phase 7F — caption = first item in items list (alt text)
                pdf.render_image(blk.text,
                                 caption=(blk.items[0] if blk.items else ""))
            # blank → ignored (paragraphs already have ln(8))
        return bytes(pdf.output()), toc

    # Pass 1: learn page numbers WITHOUT the TOC page inserted.
    _, toc_entries = render(insert_toc=None)
    # Shift every entry by +1 page since the TOC will live between cover and
    # the first chapter. (Cover is always page 1; TOC will be page 2; chapter
    # 1 page numbers move from p2 → p3.)
    shifted = [(lvl, title, pg + 1) for (lvl, title, pg) in toc_entries]

    # Pass 2: render with the TOC inserted at the correct slot.
    final_bytes, _ = render(insert_toc=shifted)
    return final_bytes


# ---------------------------------------------------------------------------
# Phase 7F — Role-segregated booklet builder
# ---------------------------------------------------------------------------
def build_role_manual_pdf(
    role_key: str,
    md_text: str | None = None,
) -> bytes:
    """Render the role-specific manual booklet.

    role_key=='admin' or unknown → falls through to the master full PDF
    (existing build_manual_pdf behaviour). All others get a personalised
    cover ("Store Keeper Manual 🗝️" style) + only their chapters per
    ROLE_MANUAL_RECIPES.

    md_text=None → loads USER_MANUAL.md from the repo root. Pass the string
    explicitly in unit tests so the harness can inject minimal fixtures.
    """
    if md_text is None:
        src = Path("USER_MANUAL.md")
        md_text = src.read_text(encoding="utf-8") if src.exists() else ""

    recipe = ROLE_MANUAL_RECIPES.get(role_key)
    if not recipe or recipe.get("chapters") == "ALL":
        return build_manual_pdf(md_text)

    sliced = slice_markdown_for_role(role_key, md_text)
    blocks = parse_markdown(sliced)

    def render(insert_toc: Optional[list] = None) -> tuple[bytes, list]:
        pdf = ManualPDF()
        pdf.render_cover_for_role(recipe)   # personalised cover
        toc: list[tuple[int, str, int]] = []
        if insert_toc is not None:
            pdf.render_toc(insert_toc)

        for blk in blocks:
            if blk.kind == "h1":
                pdf.render_h1(blk.text)
                toc.append((1, blk.text, pdf.page_no()))
            elif blk.kind == "h2":
                pdf.render_h2(blk.text)
                toc.append((2, blk.text, pdf.page_no()))
            elif blk.kind == "h3":
                pdf.render_h3(blk.text)
            elif blk.kind == "h4":
                pdf.render_h4(blk.text)
            elif blk.kind == "p":
                pdf.render_paragraph(blk.text)
            elif blk.kind == "ul":
                pdf.render_list(blk.items)
            elif blk.kind == "code":
                pdf.render_code(blk.text)
            elif blk.kind == "table":
                pdf.render_table(blk.items)
            elif blk.kind == "hr":
                pdf.render_hr()
            elif blk.kind == "img":
                pdf.render_image(blk.text,
                                 caption=(blk.items[0] if blk.items else ""))
        return bytes(pdf.output()), toc

    _, toc_entries = render(insert_toc=None)
    shifted = [(lvl, title, pg + 1) for (lvl, title, pg) in toc_entries]
    final_bytes, _ = render(insert_toc=shifted)
    return final_bytes


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--in",  dest="src", default="USER_MANUAL.md",
                        help="Markdown source (default: USER_MANUAL.md)")
    parser.add_argument("--out", dest="dst", default="GI_Hub_User_Manual.pdf",
                        help="PDF output path (default: GI_Hub_User_Manual.pdf)")
    parser.add_argument("--role", dest="role", default=None,
                        choices=list(ROLE_MANUAL_RECIPES.keys()) + ["all"],
                        help="Role-segregated booklet. 'all' regenerates "
                             "every role PDF + master in one run.")
    args = parser.parse_args(argv)

    src = Path(args.src)
    if not src.exists():
        print(f"ERROR: source not found: {src}", file=sys.stderr)
        return 1

    print(f"Reading  {src} ({src.stat().st_size:,} bytes)")
    md = src.read_text(encoding="utf-8")
    print(f"Parsing  {len(md.splitlines()):,} lines …")

    today = datetime.date.today().isoformat()

    if args.role == "all":
        # Master + every role booklet in one run.
        master_path = Path(args.dst)
        master_bytes = build_manual_pdf(md)
        master_path.write_bytes(master_bytes)
        print(f"Written  {master_path} ({len(master_bytes):,} bytes)")
        for rk in ROLE_MANUAL_RECIPES:
            short = {
                "store_keeper":   "SK",
                "supervisor":     "Supervisor",
                "hod":            "HOD",
                "logistics":      "Logistics",
                "warehouse_user": "Warehouse",
                "admin":          "Admin",
            }.get(rk, rk)
            out = Path(f"GI_{short}_Manual_{today}.pdf")
            pdf = build_role_manual_pdf(rk, md)
            out.write_bytes(pdf)
            print(f"Written  {out} ({len(pdf):,} bytes)")
        return 0

    if args.role:
        pdf_bytes = build_role_manual_pdf(args.role, md)
    else:
        pdf_bytes = build_manual_pdf(md)
    out = Path(args.dst)
    out.write_bytes(pdf_bytes)
    print(f"Written  {out} ({len(pdf_bytes):,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
