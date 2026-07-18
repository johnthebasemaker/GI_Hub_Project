#!/usr/bin/env python3
"""
tools/export_docs_pdf.py — render the ops-handoff PDFs from Markdown.

    .venv/bin/python tools/export_docs_pdf.py

Converts `docs/USER_MANUAL.md` (v2 role-based manual) and `SOP.md` into
clean PDFs under `docs/export/` using fpdf2 (already a project dependency —
same engine as backend/api/exec_pdf.py). Deliberately lightweight: headings,
bullets, numbered lists, tables, block quotes, code blocks, images
(screenshots), bold/code inline markers stripped. Core Helvetica fonts ⇒
non-latin-1 glyphs (emoji) are transliterated/dropped.

Re-run whenever either markdown changes; commit the refreshed PDFs.
"""
from __future__ import annotations

import os
import re
import sys

from fpdf import FPDF

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "docs", "export")

DOCS = [  # (source md, output pdf, cover title)
    ("docs/USER_MANUAL.md", "GI-Hub-User-Manual-v2.pdf",
     "GI Hub — User Manual (v2, React/FastAPI)"),
    ("SOP.md", "GI-Hub-SOP.pdf",
     "GI Hub — Standard Operating Procedure"),
]

_EMOJI_MAP = {"🐞": "[bug]", "✨": "[feature]", "💬": "[note]", "⚠️": "(!)",
              "🚨": "(!!)", "✅": "[ok]", "❌": "[x]", "🔁": "", "📋": "",
              "🧮": "", "🗄️": "", "📎": "", "🔔": "", "⌘": "Cmd"}
_INLINE = re.compile(r"\*\*(.+?)\*\*|\*(.+?)\*|`(.+?)`|\[(.+?)\]\((.+?)\)")


def _latin(s: str) -> str:
    for k, v in _EMOJI_MAP.items():
        s = s.replace(k, v)
    s = (s.replace("—", "-").replace("–", "-").replace("·", "-")
          .replace("→", "->").replace("⇄", "<->").replace("⇒", "=>")
          .replace("≤", "<=").replace("≥", ">=").replace("’", "'")
          .replace("‘", "'").replace("“", '"').replace("”", '"')
          .replace("…", "...").replace("×", "x").replace("Σ", "sum "))
    s = re.sub(r"(\S{44})(?=\S)", r"\1 ", s)  # soft-break unbreakable tokens
    return s.encode("latin-1", "ignore").decode("latin-1")


def _plain(s: str) -> str:
    """Strip inline markdown markers, keep link text."""
    def sub(m: re.Match) -> str:
        return next(g for g in m.groups() if g is not None)
    prev = None
    while prev != s:
        prev, s = s, _INLINE.sub(sub, s)
    return _latin(s)


class DocPDF(FPDF):
    def __init__(self, title: str):
        super().__init__(orientation="P", unit="mm", format="A4")
        self.doc_title = title
        self.set_auto_page_break(auto=True, margin=18)
        self.set_margins(16, 16, 16)

    def header(self):  # skip on the cover page
        if self.page_no() == 1:
            return
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(120)
        self.cell(0, 6, _latin(self.doc_title), align="L")
        self.ln(8)
        self.set_text_color(0)

    def footer(self):
        self.set_y(-14)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(120)
        self.cell(0, 6, f"Page {self.page_no()}", align="C")
        self.set_text_color(0)


def _render_table(pdf: DocPDF, rows: list[list[str]]) -> None:
    if not rows:
        return
    epw = pdf.epw
    ncols = max(len(r) for r in rows)
    widths = [epw / ncols] * ncols
    with pdf.table(col_widths=widths, text_align="LEFT",
                   line_height=4.6, first_row_as_headings=True) as table:
        for r in rows:
            row = table.row()
            for i in range(ncols):
                cell = _plain(r[i]) if i < len(r) else ""
                row.cell(cell)
    pdf.ln(2)


def render(md_path: str, out_pdf: str, title: str) -> str:
    src = os.path.join(ROOT, md_path)
    text = open(src, encoding="utf-8").read()
    pdf = DocPDF(title)
    pdf.add_page()

    # cover
    pdf.ln(60)
    pdf.set_font("Helvetica", "B", 22)
    pdf.multi_cell(0, 10, _latin(title), align="C")
    pdf.ln(6)
    pdf.set_font("Helvetica", "", 11)
    pdf.multi_cell(0, 7, "General Industries - GI Hub ERP\n"
                         "Generated from the repository markdown "
                         "(tools/export_docs_pdf.py)", align="C")
    pdf.add_page()

    lines = text.splitlines()
    i, in_code, table_buf = 0, False, []
    while i < len(lines):
        pdf.set_x(pdf.l_margin)  # every block starts at the full width
        line = lines[i]
        stripped = line.strip()

        if stripped.startswith("```"):
            in_code = not in_code
            if not in_code:
                pdf.ln(1.5)
            i += 1
            continue
        if in_code:
            pdf.set_font("Courier", "", 8)
            pdf.set_text_color(60)
            pdf.multi_cell(0, 4.2, _latin(line) or " ")
            pdf.set_text_color(0)
            i += 1
            continue

        if stripped.startswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if not all(re.fullmatch(r":?-{2,}:?", c or "-") for c in cells):
                table_buf.append(cells)
            i += 1
            continue
        if table_buf:
            _render_table(pdf, table_buf)
            table_buf = []

        m = re.match(r"^(#{1,4})\s+(.*)", stripped)
        if m:
            level = len(m.group(1))
            size = {1: 17, 2: 14, 3: 12, 4: 10.5}[level]
            pdf.ln(4 if level <= 2 else 2.5)
            pdf.set_font("Helvetica", "B", size)
            pdf.multi_cell(0, size * 0.5, _plain(m.group(2)))
            if level <= 2:
                y = pdf.get_y() + 0.8
                pdf.line(pdf.l_margin, y, pdf.l_margin + pdf.epw, y)
            pdf.ln(2)
        elif (m := re.match(r"^!\[.*?\]\((.+?)\)", stripped)):
            img = os.path.join(os.path.dirname(src), m.group(1))
            if os.path.exists(img):
                w = min(pdf.epw, 150)
                pdf.image(img, w=w)
                pdf.ln(3)
        elif stripped.startswith(">"):
            pdf.set_font("Helvetica", "I", 9.5)
            pdf.set_text_color(90)
            pdf.multi_cell(0, 5, _plain(stripped.lstrip("> ")) or " ")
            pdf.set_text_color(0)
        elif re.match(r"^[-*]\s+", stripped):
            pdf.set_font("Helvetica", "", 10)
            indent = (len(line) - len(line.lstrip())) // 2 * 4
            pdf.set_x(pdf.l_margin + indent)
            pdf.multi_cell(pdf.epw - indent, 5.2,
                           "-  " + _plain(re.sub(r"^[-*]\s+", "", stripped)))
        elif re.match(r"^\d+\.\s+", stripped):
            pdf.set_font("Helvetica", "", 10)
            pdf.multi_cell(0, 5.2, _plain(stripped))
        elif stripped in ("---", "***"):
            pdf.ln(2)
        elif stripped:
            pdf.set_font("Helvetica", "", 10)
            pdf.multi_cell(0, 5.2, _plain(stripped))
        else:
            pdf.ln(2)
        i += 1
    if table_buf:
        _render_table(pdf, table_buf)

    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, out_pdf)
    pdf.output(out)
    return out


def main() -> int:
    for md, out_name, title in DOCS:
        out = render(md, out_name, title)
        size = os.path.getsize(out)
        print(f"✅ {md} → {os.path.relpath(out, ROOT)} ({size/1024:.0f} KB)")
        if size < 5_000:
            print(f"❌ {out_name} suspiciously small")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
