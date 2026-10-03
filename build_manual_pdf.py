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
import unicodedata
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
# Matches the shipped application version (frontend/src-tauri/tauri.conf.json,
# frontend/package.json, frontend/src-tauri/Cargo.toml). A manual whose cover
# disagrees with the installer someone just ran is worse than an undated one.
APP_VERSION = "1.2.0"
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
    "auditor": {
        "title":    "Auditor Manual",
        "icon":     "\U0001F50D",
        "audience": "Read-only review across every site: reports, records, KPIs.",
        "chapters": [
            "1. Introduction & System Overview",
            "2. Roles, Permissions & Page Access",
            "3. Login, Sidebar & Common Elements",
            "8. Reports Module — Detailed Reference",
            "9. Automated Notifications — WhatsApp & Email & In-app Bell",
            "10. Data Model & Concept Reference",
            "11. Status Codes, Reason Codes & Glossary",
            "12. FAQ — Master Index by Role",
            "16. Cross-Role Procurement Walk-through",
            "20. Auditor (View-Only) Manual",
            "21. 2026-08 Feature Update — What Changed",
            "22. Quality, Safety, Employees & Procurement (QSEP)",
            # Slice 11c: AI Traces is one of the auditor's own pages.
            "25. Phase 11 — AI Traces",
            # Rule 17 — every role has a practice account.
            "26. Practice Mode — learn the system without touching Live",
        ],
    },
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
            # Chapter 21 carries the Locator, the serialised Assets register and
            # the note about apostrophes in downloads. Both of those pages are
            # minLevel 0 and a Store Keeper uses them daily, so leaving 21 out
            # shipped a booklet that never mentioned two of their own menu items.
            "21. 2026-08 Feature Update — What Changed",
            # 22 is not optional for this role: the QC block and the PPE fields
            # both fire on the Store Keeper's OWN issue form. A booklet that
            # omitted it would leave the one person who meets those refusals
            # with no written explanation of them.
            "22. Quality, Safety, Employees & Procurement (QSEP)",
            # Rule 17 — every role has a practice account.
            "26. Practice Mode — learn the system without touching Live",
        ],
    },
    "qc": {
        "title":    "Quality Control Manual",
        "icon":     "🧪",
        "audience": "Inspect controlled material, approve or reject, release it for issue.",
        "chapters": [
            "1. Introduction & System Overview",
            "2. Roles, Permissions & Page Access",
            "3. Login, Sidebar & Common Elements",
            "11. Status Codes, Reason Codes & Glossary",
            "12. FAQ — Master Index by Role",
            "15. Warehouse Portal Manual",
            "22. Quality, Safety, Employees & Procurement (QSEP)",
            # Rule 17 — every role has a practice account.
            "26. Practice Mode — learn the system without touching Live",
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
            "22. Quality, Safety, Employees & Procurement (QSEP)",
            # Rule 17 — every role has a practice account.
            "26. Practice Mode — learn the system without touching Live",
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
            "22. Quality, Safety, Employees & Procurement (QSEP)",
            # Rule 17 — every role has a practice account.
            "26. Practice Mode — learn the system without touching Live",
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
            "22. Quality, Safety, Employees & Procurement (QSEP)",
            # Rule 17 — every role has a practice account.
            "26. Practice Mode — learn the system without touching Live",
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
            # Same reason as the Store Keeper booklet: scanning a rack to get a
            # count checklist, and the serialised asset register, are warehouse
            # work described only in chapter 21.
            "21. 2026-08 Feature Update — What Changed",
            "22. Quality, Safety, Employees & Procurement (QSEP)",
            # Rule 17 — every role has a practice account.
            "26. Practice Mode — learn the system without touching Live",
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
        #
        # ⚠️ CONTINUATION LINES BELONG TO THEIR BULLET. A wrapped list item —
        #
        #     - The connection runs outward, not inward. The server opens
        #       a connection to the edge; the edge never opens one back.
        #
        # is ONE item. Reading only the first line and letting the indented
        # remainder fall through to the paragraph branch split every wrapped
        # bullet into a bullet plus an unindented orphan paragraph, which is
        # what made lists look broken wherever a point ran past one line.
        if _BULLET_RE.match(line) or _NUMLI_RE.match(line):
            items: list[tuple[str, str]] = []
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
                # Absorb the item's own wrapped lines: indented, non-blank, and
                # not themselves the start of a new bullet or block.
                while i < n:
                    nxt = lines[i]
                    if (not nxt.strip() or not nxt.startswith((" ", "\t"))
                            or _BULLET_RE.match(nxt) or _NUMLI_RE.match(nxt)
                            or _HEADING_RE.match(nxt) or _FENCE_RE.match(nxt.strip())
                            or _is_table_row(nxt)):
                        break
                    bullet, body = items[-1]
                    items[-1] = (bullet, f"{body} {nxt.strip()}")
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
    fence: str | None = None
    for line in md_text.splitlines():
        # Fence tracking: the Operations chapter documents shell scripts whose
        # comments are literally "# 1. Pull the new code". Without this, such a
        # line reads as a chapter heading and silently ends the chapter being
        # collected. (Only latent today — no non-admin recipe includes that
        # chapter — but it is the same defect that corrupted the assistant's
        # context, so it is fixed at both sites.)
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            mark = stripped[:3]
            fence = mark if fence is None else (None if fence == mark else fence)
            if include:
                out_lines.append(line)
            continue
        if fence is None and line.startswith("# ") and not line.startswith("## "):
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


_EMPHASIS_RE = re.compile(r"(?<![A-Za-z0-9])[*_]([^*_\n]+)[*_](?![A-Za-z0-9])")


def _strip_md_punct(text: str) -> str:
    """Remove markdown emphasis punctuation, keeping the words.

    Paragraphs are rendered with a single font by design (see
    `render_paragraph`) — mixing fonts mid-paragraph made fpdf2 overflow the
    right margin. So emphasis is stripped rather than styled.

    ⚠️ It must be stripped COMPLETELY. This used to remove `**` and backticks
    but not single `*`, so `**bold**` came out as clean unstyled text while
    `*italic*` came out as literal asterisks on the page — visible on any page
    that emphasised a single word. The guards either side of the marker are what
    keep it from eating an asterisk used as a footnote mark or a snake_case
    identifier: `_severity_` is emphasis, `some_field_name` is not.
    """
    text = text.replace("**", "").replace("`", "").replace("~~", "")
    return _EMPHASIS_RE.sub(r"\1", text)


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
        self.cell(0, 5, _ascii("GI Hub  ·  Multi-Site Inventory, Procurement "
                               "& Asset Management"), align="L")
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
        """Bullet list, drawn one line at a time at explicit coordinates.

        ⚠️ THE PAGE BREAK MUST HAPPEN BETWEEN LINES, NOT INSIDE ONE. The bullet
        used to be written with `cell()` and the body with `multi_cell()` after
        a `set_xy(..., y_start)` that re-pinned Y to the value captured before
        the bullet. When the bullet sat near the bottom, `cell()` tripped
        fpdf2's own auto page-break: the cursor moved to the top of a NEW page,
        and the `set_xy` then dragged the body back down to the old Y — on the
        new page — printing it across whatever was already there. Y is now
        advanced explicitly and every break is decided here, before anything is
        drawn, so no writer can move the cursor behind this method's back.
        """
        usable_w = PAGE_W_MM - 2 * MARGIN_MM
        bullet_w = 6
        body_w   = usable_w - bullet_w
        line_h   = 5.5
        prev_auto, prev_bmargin = self.auto_page_break, self.b_margin
        self.set_auto_page_break(False)
        try:
            for bullet, body in items:
                self.set_font("helvetica", "", 10.5)
                lines = self._wrap_cell(_ascii(_strip_md_punct(body)), body_w)
                first = True
                for ln_ in lines:
                    if self.get_y() + line_h > self._content_bottom():
                        self.add_page()
                    y = self.get_y()
                    if first:
                        self.set_font("helvetica", "B", 10.5)
                        self.set_text_color(*BRAND_NAVY)
                        self.set_xy(MARGIN_MM, y)
                        self.cell(bullet_w, line_h, _ascii(bullet), align="L")
                        first = False
                    self.set_font("helvetica", "", 10.5)
                    self.set_text_color(*TEXT_BODY)
                    self.set_xy(MARGIN_MM + bullet_w, y)
                    self.cell(body_w, line_h, ln_, align="L")
                    self.set_xy(MARGIN_MM, y + line_h)
                self.ln(0.5)
        finally:
            self.set_auto_page_break(prev_auto, margin=prev_bmargin)
        self.ln(2)

    def render_code(self, text: str):
        """Fenced code block: monospace, wrapped, and split across pages.

        Two failures are handled here that the previous version did not survive.
        A line wider than the box used to run straight through the right border
        and off the paper, because `cell()` does not wrap. And a block taller
        than the page was drawn as one over-long rectangle whose text carried on
        past the bottom margin, over the footer and into nothing. Both are now
        bounded: long lines wrap on the courier metrics, and the block is
        emitted in page-sized slices, each with its own box.
        """
        self.ln(1)
        line_h = 5
        pad    = 3
        x      = MARGIN_MM
        w      = PAGE_W_MM - 2 * MARGIN_MM
        text_w = w - 10          # 5 mm of padding either side of the text

        self.set_font("courier", "", 9)
        # Courier is monospace, so one measurement gives the character budget.
        # Wrapping by hand (rather than via _wrap_cell) keeps leading
        # indentation, which in a code sample carries meaning.
        char_w    = self.get_string_width("M") or 1.0
        max_chars = max(8, int(text_w / char_w))
        lines: list[str] = []
        for raw in (text.splitlines() or [""]):
            s = _ascii(raw)
            if not s.strip():
                lines.append("")
                continue
            hang = " " * min(len(s) - len(s.lstrip()), 4)
            while self.get_string_width(s) > text_w:
                lines.append(s[:max_chars])
                s = hang + s[max_chars:]
            lines.append(s)

        prev_auto, prev_bmargin = self.auto_page_break, self.b_margin
        self.set_auto_page_break(False)
        try:
            i = 0
            while i < len(lines):
                cap = int((self._content_bottom() - self.get_y() - 2 * pad) // line_h)
                if cap < 1:
                    self.add_page()
                    cap = int((self._content_bottom() - self.get_y() - 2 * pad) // line_h)
                    if cap < 1:
                        break          # page too short for a single line
                chunk = lines[i:i + cap]
                i += len(chunk)
                h = line_h * len(chunk) + pad * 2
                y = self.get_y()
                # Box background + border, then the gold left accent.
                self.set_fill_color(*CODE_BG)
                self.set_draw_color(*CODE_BORDER)
                self.set_line_width(0.2)
                self.rect(x, y, w, h, "DF")
                self.set_fill_color(*BRAND_GOLD)
                self.rect(x, y, 1.2, h, "F")
                # Text
                self.set_font("courier", "", 9)
                self.set_text_color(*TEXT_DARK)
                ty = y + pad
                for ln_ in chunk:
                    self.set_xy(x + 5, ty)
                    self.cell(text_w, line_h, ln_, align="L")
                    ty += line_h
                self.set_xy(MARGIN_MM, y + h)
        finally:
            self.set_auto_page_break(prev_auto, margin=prev_bmargin)
        self.ln(2)

    # ── Table rendering ───────────────────────────────────────────────────
    #
    # Every row is measured with fpdf2's OWN line splitter and then drawn line
    # by line at explicit coordinates. Nothing below relies on `multi_cell` to
    # advance the Y cursor, because that is exactly what used to go wrong: the
    # cursor ended up somewhere the measured row height had not accounted for,
    # and the following row printed on top of the previous one.
    TABLE_LINE_H = 5.5
    TABLE_PAD_Y  = 0.9
    TABLE_PAD_X  = 1.5

    def _content_bottom(self) -> float:
        """Lowest Y that content may occupy before the footer band."""
        return PAGE_H_MM - 25

    def _draw_row_segment(self, cols: list[list[str]], x0: float, y: float,
                          col_w: float, row_h: float, fill: bool) -> None:
        """Paint one row — or one page-slice of a tall row — boxes then text.

        Boxes for every column go down first so that no cell's background can
        later paint over a neighbour's text. Colours and font are the caller's
        responsibility; this method only places geometry.
        """
        inner_w = col_w - 2 * self.TABLE_PAD_X
        for i in range(len(cols)):
            self.rect(x0 + i * col_w, y, col_w, row_h, "DF" if fill else "D")
        for i, lines in enumerate(cols):
            ty = y + self.TABLE_PAD_Y
            for ln_ in lines:
                self.set_xy(x0 + i * col_w + self.TABLE_PAD_X, ty)
                self.cell(inner_w, self.TABLE_LINE_H, ln_, align="L")
                ty += self.TABLE_LINE_H

    def render_table(self, rows: list[list[str]]):
        if not rows:
            return
        header, body = rows[0], rows[1:]
        n_cols = len(header)
        if n_cols == 0:
            return
        usable  = PAGE_W_MM - 2 * MARGIN_MM
        col_w   = usable / n_cols
        inner_w = col_w - 2 * self.TABLE_PAD_X
        line_h  = self.TABLE_LINE_H
        pad_y   = self.TABLE_PAD_Y
        x0      = MARGIN_MM

        # Auto page-break is OFF for the duration of the table. Every break is
        # decided here, where the row height is known. Left on, fpdf2 could
        # break inside a cell whose enclosing box had already been drawn.
        prev_auto, prev_bmargin = self.auto_page_break, self.b_margin
        self.set_auto_page_break(False)
        try:
            def header_cols() -> list[list[str]]:
                self.set_font("helvetica", "B", 9.5)
                return [self._wrap_cell(_ascii(_strip_md_punct(c)), inner_w)
                        for c in header]

            def draw_header(y: float) -> float:
                """Draw the header band at `y`; return the Y below it.

                Header cells wrap like any other cell now. They used to be
                hard-truncated at 60 characters and drawn in a fixed 7 mm band,
                so a long column title lost its ending and a two-line title
                spilled over the first body row.
                """
                cols = header_cols()
                h = max(len(c) for c in cols) * line_h + 2 * pad_y
                self.set_fill_color(*TABLE_HEADER_BG)
                self.set_text_color(255, 255, 255)
                self.set_draw_color(*TABLE_BORDER)
                self.set_line_width(0.2)
                self.set_font("helvetica", "B", 9.5)
                self._draw_row_segment(cols, x0, y, col_w, h, True)
                return y + h

            # The header plus at least one body line must fit, or the table
            # starts on a fresh page rather than stranding its header.
            head_h = max(len(c) for c in header_cols()) * line_h + 2 * pad_y
            if self.get_y() + head_h + line_h + 2 * pad_y > self._content_bottom():
                self.add_page()
            y = draw_header(self.get_y())

            for ri, row in enumerate(body):
                cells = (list(row) + [""] * n_cols)[:n_cols]
                self.set_font("helvetica", "", 9)
                pending = [self._wrap_cell(_ascii(_strip_md_punct(c)), inner_w)
                           for c in cells]
                fill = (ri % 2 == 0)
                # A single row can be taller than a whole page — a cell holding
                # a paragraph of explanation will do it. Emit it in slices, each
                # of which fits the page it is drawn on, repeating the header
                # after every break so a continued row is still readable.
                while max((len(c) for c in pending), default=0) > 0:
                    cap = int((self._content_bottom() - y - 2 * pad_y) // line_h)
                    if cap < 1:
                        self.add_page()
                        y = draw_header(self.get_y())
                        cap = int((self._content_bottom() - y - 2 * pad_y) // line_h)
                        if cap < 1:
                            break      # page too short for even one line
                    take = min(max(len(c) for c in pending), cap)
                    seg     = [c[:take] for c in pending]
                    pending = [c[take:] for c in pending]
                    h = take * line_h + 2 * pad_y
                    self.set_fill_color(*TABLE_ROW_ALT_BG)
                    self.set_text_color(*TEXT_BODY)
                    self.set_draw_color(*TABLE_BORDER)
                    self.set_line_width(0.2)
                    self.set_font("helvetica", "", 9)
                    self._draw_row_segment(seg, x0, y, col_w, h, fill)
                    y += h
            self.set_xy(MARGIN_MM, y)
        finally:
            self.set_auto_page_break(prev_auto, margin=prev_bmargin)
        self.ln(2)

    def _wrap_cell(self, text: str, width_mm: float) -> list[str]:
        """The exact lines fpdf2 will draw for `text` in a cell `width_mm` wide.

        ⚠️ MEASURE WITH THE ENGINE THAT DRAWS. This used to wrap by hand with
        `get_string_width`, comparing each trial line against the FULL cell
        width — but a cell's usable text width is that width minus `c_margin`
        on each side, 2 mm at fpdf2's default. Lines that measured as fitting
        therefore re-wrapped when they were actually drawn: a row measured five
        lines tall rendered six, the sixth landed in the row below, and the two
        rows' words interleaved into unreadable mush. Asking
        `multi_cell(dry_run=True, output="LINES")` removes the possibility of
        the two ever disagreeing, because the measurement IS the split.

        WORD mode still breaks a token too long for the column — a URL, a long
        material code — at the character level, so nothing runs into the next
        column. The caller must have selected the font it intends to draw with:
        the answer depends on it.
        """
        if not text:
            return [""]
        lines = self.multi_cell(width_mm, self.TABLE_LINE_H, text,
                                dry_run=True, output="LINES", wrapmode="WORD")
        return list(lines) or [""]

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
    "⟳": "(refresh)", "〃": "(ditto)",
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


# Box-drawing and block characters. The manual no longer contains ASCII-art
# diagrams — they were replaced with tables in 2026-08, because fpdf2's core
# fonts are latin-1 and every one of these rendered as a literal "?", turning
# whole pages into rows of question marks. Mapped anyway rather than left to the
# latin-1 fallback: if a diagram is ever pasted back in, it should come out as
# recognisable ASCII art rather than as noise.
_REPLACE.update({c: sub for c, sub in (
    ("─", "-"), ("━", "-"), ("═", "="), ("│", "|"), ("┃", "|"), ("║", "|"),
    ("┌", "+"), ("┐", "+"), ("└", "+"), ("┘", "+"), ("├", "+"), ("┤", "+"),
    ("┬", "+"), ("┴", "+"), ("┼", "+"), ("╔", "+"), ("╗", "+"), ("╚", "+"),
    ("╝", "+"), ("╠", "+"), ("╣", "+"), ("╦", "+"), ("╩", "+"), ("╬", "+"),
    ("▶", ">"), ("►", ">"), ("◀", "<"), ("◄", "<"), ("▲", "^"), ("▼", "v"),
    ("█", "#"), ("▓", "#"), ("▒", ":"), ("░", "."), ("■", "*"), ("□", "-"),
    ("●", "*"), ("○", "-"), ("◦", "-"), ("▪", "*"), ("▫", "-"),
)})

# Mathematical and comparison symbols. These carry MEANING — a dropped minus
# sign changes a formula — so each one is spelled out rather than removed.
_REPLACE.update({
    "−": "-", "×": "x", "÷": "/", "±": "+/-", "≈": "~", "≠": "!=",
    "≤": "<=", "≥": ">=", "Σ": "Sum", "∑": "Sum", "√": "sqrt", "∞": "inf",
    "⌘": "Cmd", "⇧": "Shift", "⌥": "Alt", "⌃": "Ctrl", "⏎": "Enter",
    "½": "1/2", "¼": "1/4", "¾": "3/4", "º": "deg", "‰": "per mille",
})

# Characters seen leaving the sanitiser unmapped, reported once at the end of a
# build. Silence was the actual defect: a "?" in a PDF looks like a font problem
# to whoever finds it months later, and nothing in the build said a word. The
# first run with this warning turned up 56 of them, all of which are handled
# above or by `_drop_decorative` below.
_UNMAPPED: set[str] = set()


def _drop_decorative(text: str) -> str:
    """Remove emoji and other purely decorative symbols.

    The interface labels its tabs with an emoji prefix — "Incoming PRs",
    "DN Approvals", "Force-Close" — and the manual quotes those labels verbatim,
    so a page describing eight tabs used to carry eight question marks. There is
    no latin-1 equivalent to substitute and the emoji adds nothing to a printed
    page, so it goes.

    Category-based rather than a hand-written list: `_REPLACE` already tried the
    list approach and every new interface emoji silently defeated it. `So`
    (Symbol, other) covers emoji and pictographs, `Cf` (format) covers the
    variation selectors and zero-width joiners that ride along with them.
    """
    out = []
    for ch in text:
        if ch in _REPLACE or ord(ch) < 128:
            out.append(ch)
            continue
        if unicodedata.category(ch) in ("So", "Cf", "Sk"):
            continue        # decorative — drop it, and the space it leaves
        # Variation selectors and skin-tone modifiers are category Mn, which
        # otherwise holds combining accents that DO matter in a person's name.
        # Named by range rather than by category for exactly that reason.
        if 0xFE00 <= ord(ch) <= 0xFE0F or 0x1F3FB <= ord(ch) <= 0x1F3FF:
            continue
        out.append(ch)
    # Dropping a leading emoji leaves the space that followed it.
    return re.sub(r"  +", " ", "".join(out)).strip()


def _ascii(text: str) -> str:
    if not text:
        return ""
    for k, v in _REPLACE.items():
        if k in text:
            text = text.replace(k, v)
    try:
        return text.encode("latin-1").decode("latin-1")
    except UnicodeEncodeError:
        pass
    text = _drop_decorative(text)
    try:
        return text.encode("latin-1").decode("latin-1")
    except UnicodeEncodeError:
        # Genuinely unrepresentable and NOT decorative — a real gap. Name it in
        # the build output rather than shipping a silent "?".
        for ch in text:
            try:
                ch.encode("latin-1")
            except UnicodeEncodeError:
                _UNMAPPED.add(ch)
        return text.encode("latin-1", "replace").decode("latin-1")


def audit_pdf_overlaps(path: str | Path) -> Optional[int]:
    """Count pairs of words whose boxes intersect — i.e. text printed on text.

    Rendered text never legitimately overlaps, so any hit is a row-height or
    Y-cursor defect. This exists because the table renderer shipped such a
    defect for months: it was obvious on the page but nothing in the build said
    a word, so every regenerated PDF quietly carried it. A geometry check the
    build runs on its own output is the only kind that cannot be forgotten.

    Returns the pair count, or None if pdfplumber is unavailable (it is a
    backend OCR dependency, not a build one — its absence must not fail a build
    that is otherwise fine).
    """
    try:
        import pdfplumber
    except ImportError:
        return None

    eps = 0.6       # points — ignore kerning-level touching
    hits = 0
    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages:
            words = sorted(page.extract_words(use_text_flow=False),
                           key=lambda w: (w["top"], w["x0"]))
            for i, a in enumerate(words):
                for b in words[i + 1:]:
                    if b["top"] >= a["bottom"] - eps:
                        break        # sorted by top: nothing later can overlap
                    if (min(a["x1"], b["x1"]) - max(a["x0"], b["x0"]) > eps
                            and min(a["bottom"], b["bottom"])
                                - max(a["top"], b["top"]) > eps):
                        hits += 1
    return hits


def report_unmapped() -> None:
    """Print any character the PDF could not represent. Called after a build."""
    if not _UNMAPPED:
        return
    listing = ", ".join(f"{ch!r} (U+{ord(ch):04X})" for ch in sorted(_UNMAPPED))
    print(f"\n  WARNING  {len(_UNMAPPED)} character(s) could not be rendered "
          f"and became '?' in the PDF:\n           {listing}\n"
          f"           Add them to _REPLACE in build_manual_pdf.py, or remove "
          f"them from the markdown.", file=sys.stderr)


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
    parser.add_argument("--no-verify", dest="verify", action="store_false",
                        help="Skip the overlapping-text audit of the output.")
    args = parser.parse_args(argv)

    src = Path(args.src)
    if not src.exists():
        print(f"ERROR: source not found: {src}", file=sys.stderr)
        return 1

    print(f"Reading  {src} ({src.stat().st_size:,} bytes)")
    md = src.read_text(encoding="utf-8")
    print(f"Parsing  {len(md.splitlines()):,} lines …")

    today = datetime.date.today().isoformat()
    written: list[Path] = []

    if args.role == "all":
        # Master + every role booklet in one run.
        master_path = Path(args.dst)
        master_bytes = build_manual_pdf(md)
        master_path.write_bytes(master_bytes)
        written.append(master_path)
        print(f"Written  {master_path} ({len(master_bytes):,} bytes)")
        for rk in ROLE_MANUAL_RECIPES:
            short = {
                "store_keeper":   "SK",
                "supervisor":     "Supervisor",
                "hod":            "HOD",
                "logistics":      "Logistics",
                "warehouse_user": "Warehouse",
                "admin":          "Admin",
                "auditor":        "Auditor",
                "qc":             "QC",
            }.get(rk, rk)
            out = Path(f"GI_{short}_Manual_{today}.pdf")
            pdf = build_role_manual_pdf(rk, md)
            out.write_bytes(pdf)
            written.append(out)
            print(f"Written  {out} ({len(pdf):,} bytes)")
    else:
        if args.role:
            pdf_bytes = build_role_manual_pdf(args.role, md)
        else:
            pdf_bytes = build_manual_pdf(md)
        out = Path(args.dst)
        out.write_bytes(pdf_bytes)
        written.append(out)
        print(f"Written  {out} ({len(pdf_bytes):,} bytes)")

    if not args.verify:
        return 0

    print("\nVerifying rendered geometry (overlapping text) …")
    failed = 0
    for p in written:
        hits = audit_pdf_overlaps(p)
        if hits is None:
            print("  SKIPPED  pdfplumber not installed - geometry unverified")
            break
        print(f"  {'FAIL' if hits else 'OK  '}     {p.name:44s} "
              f"{hits} overlapping text pairs")
        failed += bool(hits)
    if failed:
        print(f"\n  ERROR  {failed} PDF(s) contain overlapping text. "
              f"See ManualPDF.render_table / _wrap_cell.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    code = main()
    report_unmapped()
    sys.exit(code)
