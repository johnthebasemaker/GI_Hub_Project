"""
backend/api/ai/pdf_extract.py — PR/PO PDF extraction (Phase AI-2).

Framework-free ports of the two legacy pdfplumber parsers (root database.py:
process_pr_pdf ~4945, process_po_pdf ~8939). Pure functions: bytes in, dict
out — no DB access, no FastAPI. The endpoints in router.py run them through
asyncio.to_thread (pdfplumber is synchronous, CPU-bound, 0.1–1 s/page) and do
the inventory matching / preview enrichment afterwards.

Contract difference from legacy (deliberate — the preview-confirm workflow):
these parsers NEVER write to the database. Legacy inserted straight into
pr_master/purchase_orders with no audit row; the new flow returns a preview
the user reviews in React, and the confirm step goes through the existing
audited services (procurement.create_pr / create_po_from_pr).

Parsing heuristics are kept byte-for-byte compatible with legacy — the same
regexes, the same three PO line-item layouts, the same 6-word qty look-ahead —
so PDFs that parsed on the old stack parse identically here.
"""
from __future__ import annotations

import io
import re
from typing import Callable, Optional

# Header patterns calibrated against the GI sample POs (legacy Round 15).
_PO_HEADER_PATTERNS = {
    "PO_Number":      r"Purch\.?\s*Order\.?\s*No\.?\s*[:\-]?\s*([A-Z0-9X]+)",
    "PO_Date":        r"Purch\.?\s*Order\.?\s*Date\s*[:\-]?\s*([0-9XA-Za-z][\d\.\-/A-Za-z]+)",
    "PO_Type":        r"PO\s*Type\s*[:\-]?\s*(.+)",
    "Quotation_No":   r"Quotation\s*No\.?\s*[:\-]?\s*([^\n]*)",
    "Quotation_Date": r"Quotation\s*Date\s*[:\-]?\s*([^\n]*)",
    "PR_Number_PDF":  r"Purch\.?\s*Req\.?\s*No\.?\s*[:\-]?\s*([^\n]*)",
    "Vendor_Code":    r"Vendor\s*[:\-]?\s*0*([0-9]+)",
    "Inco_Terms":     r"Inco\s*Terms\s*[:\-]?\s*(.+)",
    "Payment_Terms":  r"Payment\s*Terms\s*[:\-]?\s*([^\n]*)",
    "Your_Reference": r"Your\s*Reference\s*[:\-]?\s*([^\n]*)",
    "Our_Reference":  r"Our\s*Reference\s*[:\-]?\s*([^\n]*)",
    "Contact_Person": r"Contact\s*[:\-]?\s*([^\n]+)",
    "Mobile":         r"Mobile\s*[:\-]?\s*([+\d\s]+)",
}

_PO_FOOTER_PATTERNS = {
    "Freight_Charges":  r"Freight\s*Charges\s*([\d,\.]+)",
    "Handling_Charges": r"Handling\s*Charges\s*([\d,\.]+)",
    "Discount_Amount":  r"Discount\s*Amount\s*([\d,\.]+)",
    "Total_Amount":     r"Total\s*Amount\s*([\d,\.]+)",
}

_NUMBER = r"[\d,]+\.?\d*"
_UOM_RE = r"[A-Za-z]{1,5}"

# Layout A — bare code line, then srno + desc + numbers on the next line.
_RE_CODE_ONLY = re.compile(r"^\s*(GI-\d{6,8})\s*$")
_RE_ROW_7COL = re.compile(   # srno desc qty uom unit_price vat total
    rf"^\s*(\d{{1,3}})\s+(.+?)\s+({_NUMBER})\s+({_UOM_RE})\s+"
    rf"({_NUMBER})\s+({_NUMBER})\s+({_NUMBER})\s*$")
_RE_ROW_6COL = re.compile(   # srno desc qty uom unit_price total (no VAT)
    rf"^\s*(\d{{1,3}})\s+(.+?)\s+({_NUMBER})\s+({_UOM_RE})\s+"
    rf"({_NUMBER})\s+({_NUMBER})\s*$")
# Layout B — single line with the code inline.
_RE_INLINE = re.compile(
    rf"^\s*(\d{{1,3}})\s+(GI-\d{{6,8}})\s+(.+?)\s+({_NUMBER})\s+({_UOM_RE})"
    rf"(?:\s+({_NUMBER}))?(?:\s+({_NUMBER}))?\s*$")
# Layout C — `<srno> GI-NNNNNNN` line, desc + numbers on the next.
_RE_PAIR = re.compile(r"^\s*(\d{1,3})\s+(GI-\d{6,8})\s*$")

# Annexure: SHIPMENT 01  BRICK MATERIALS  05.02.2026
_RE_SCHEDULE = re.compile(
    r"(SHIPMENT\s*\d+)\s+([A-Z][A-Z ]+?)\s+(\d{2}\.\d{2}\.\d{4})", re.IGNORECASE)


class PdfExtractError(Exception):
    """Raised when the PDF cannot be opened/parsed at all (→ 422 upstream)."""


def _open_pdf(pdf_bytes: bytes):
    try:
        import pdfplumber
    except ImportError as e:  # pragma: no cover — dep is in requirements
        raise PdfExtractError(f"pdfplumber not installed: {e}")
    if not pdf_bytes:
        raise PdfExtractError("empty file")
    try:
        return pdfplumber.open(io.BytesIO(pdf_bytes))
    except Exception as e:
        raise PdfExtractError(f"could not open the PDF ({type(e).__name__})")


def _f(s: Optional[str]) -> float:
    try:
        return float((s or "0").replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


# --- PR PDF (legacy word-stream logic, GI-NNNNNNN + 6-word qty look-ahead) ----
def parse_pr_pdf(pdf_bytes: bytes) -> dict:
    """PR PDF → {"pr_number": str, "items": [{material_code, qty, context}]}.
    Items are deduped on (code, qty) preserving order, exactly like legacy.
    Inventory matching happens in the endpoint (needs the async session)."""
    with _open_pdf(pdf_bytes) as pdf:
        first_page_text = pdf.pages[0].extract_text() or ""
        m = (re.search(r"Purch\. Req\. No\.\s*:\s*\|?\s*(\d+)", first_page_text)
             or re.search(r"300\d{7}", first_page_text))
        pr_number = (m.group(1) if m and m.lastindex else
                     m.group(0) if m else "UNKNOWN_PR")

        items: list[dict] = []
        for page in pdf.pages:
            words = page.extract_words()
            for i, w in enumerate(words):
                if "GI-" not in w["text"].upper():
                    continue
                code_m = re.search(r"(GI-\d{7})", w["text"], re.IGNORECASE)
                if not code_m:
                    continue
                qty = 1.0  # legacy fallback
                for j in range(1, 7):  # 6-word look-ahead for the quantity
                    if i + j < len(words):
                        clean = words[i + j]["text"].replace(",", "")
                        if clean.replace(".", "", 1).isdigit():
                            qty = float(clean)
                            break
                ctx = " ".join(x["text"] for x in
                               words[max(0, i - 4):min(len(words), i + 3)])
                items.append({"material_code": code_m.group(1).upper(),
                              "qty": qty, "context": ctx.replace("\n", " ")})

    unique, seen = [], set()
    for it in items:
        key = (it["material_code"], it["qty"])
        if key not in seen:
            seen.add(key)
            unique.append(it)
    return {"pr_number": pr_number, "items": unique}


# --- PO PDF (legacy Round-15 scanner: 3 layouts + header + annexure) -----------
def parse_po_pdf(pdf_bytes: bytes,
                 classify: Optional[Callable[[str, str], Optional[str]]] = None,
                 ) -> dict:
    """PO PDF → {"ok", "message", "header": {...}, "items": [...],
    "shipment_schedule": [...]} — the exact legacy extracted shape."""
    classify = classify or (lambda code, desc: None)
    with _open_pdf(pdf_bytes) as pdf:
        full_text = ""
        for page in pdf.pages:
            full_text += "\n" + (page.extract_text() or "")

    header: dict = {"source": "pdf_upload"}
    for key, pat in _PO_HEADER_PATTERNS.items():
        m = re.search(pat, full_text, re.IGNORECASE | re.MULTILINE)
        if m:
            val = (m.group(1) or "").strip().strip(":").strip()
            if key == "PR_Number_PDF":
                if val and not header.get("PR_Number"):
                    header["PR_Number"] = val
            else:
                header[key] = val
    m = re.search(r"Vendor\s*[:\-]?\s*\d+\s*\n([^\n]+)", full_text, re.IGNORECASE)
    if m:
        header["Vendor_Name"] = m.group(1).strip()
    emails = re.findall(r"[\w\.\-]+@[\w\.\-]+", full_text)
    if emails:
        header["Contact_Email"] = emails[0]
        if len(emails) > 1:
            header["Our_Email"] = emails[-1]
    for k, pat in _PO_FOOTER_PATTERNS.items():
        m = re.search(pat, full_text, re.IGNORECASE)
        if m:
            try:
                header[k] = float(m.group(1).replace(",", ""))
            except ValueError:
                pass

    items: list[dict] = []
    seen_lines: set[int] = set()

    def _add(line_no: int, mat: str, desc: str, qty: float, uom: str,
             unit: float, total: float) -> None:
        seen_lines.add(line_no)
        items.append({"line_no": line_no, "Material_Code": mat,
                      "SAP_Code": mat,  # GI POs use GI-NNNNNNN as the SAP code
                      "Description": desc, "Qty": qty, "UOM": uom,
                      "Unit_Price": unit, "Total_Price": total,
                      "rl_bl_family": classify(mat, desc)})

    lines = full_text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]

        # Layout A: bare code line + 7/6-col row on the very next line.
        m_code = _RE_CODE_ONLY.match(line)
        if m_code and i + 1 < len(lines):
            nxt = lines[i + 1]
            m7 = _RE_ROW_7COL.match(nxt)
            m6 = _RE_ROW_6COL.match(nxt) if not m7 else None
            picked = m7 or m6
            if picked:
                try:
                    line_no = int(picked.group(1))
                except ValueError:
                    i += 1
                    continue
                if line_no not in seen_lines:
                    qty = _f(picked.group(3))
                    if qty > 0:
                        # 7-col: group 6 is VAT (captured, not stored) — total
                        # is the last group either way.
                        _add(line_no, m_code.group(1).strip(),
                             picked.group(2).strip(), qty,
                             picked.group(4).strip(), _f(picked.group(5)),
                             _f(picked.group(7 if m7 else 6)))
                        i += 2
                        continue

        # Layout B: single inline line.
        m_inl = _RE_INLINE.match(line)
        if m_inl:
            try:
                line_no = int(m_inl.group(1))
            except ValueError:
                i += 1
                continue
            if line_no not in seen_lines:
                qty = _f(m_inl.group(4))
                if qty > 0:
                    _add(line_no, m_inl.group(2).strip(), m_inl.group(3).strip(),
                         qty, m_inl.group(5).strip(), _f(m_inl.group(6)),
                         _f(m_inl.group(7)))

        # Layout C: `<srno> GI-…` pair line + desc/numbers on the next.
        m_pair = _RE_PAIR.match(line)
        if m_pair and i + 1 < len(lines):
            try:
                line_no = int(m_pair.group(1))
            except ValueError:
                i += 1
                continue
            if line_no not in seen_lines:
                m_rest = re.match(
                    rf"^\s*(.+?)\s+({_NUMBER})\s+({_UOM_RE})"
                    rf"(?:\s+({_NUMBER}))?(?:\s+({_NUMBER}))?\s*$", lines[i + 1])
                if m_rest:
                    qty = _f(m_rest.group(2))
                    if qty > 0:
                        _add(line_no, m_pair.group(2).strip(),
                             m_rest.group(1).strip(), qty,
                             m_rest.group(3).strip(), _f(m_rest.group(4)),
                             _f(m_rest.group(5)))
                        i += 2
                        continue
        i += 1

    items.sort(key=lambda r: r.get("line_no", 0))

    schedule = []
    for m in _RE_SCHEDULE.finditer(full_text):
        raw = m.group(3).strip()
        try:
            d, mo, y = raw.split(".")
            iso = f"{y}-{mo}-{d}"
        except ValueError:
            iso = raw
        schedule.append({"shipment_no": m.group(1).strip(),
                         "material_group": m.group(2).strip(),
                         "target_date": iso})

    ok = bool(items)
    return {"ok": ok,
            "message": (f"Extracted {len(items)} item(s) — review the preview"
                        if ok else
                        "No line items extracted from PDF — please use manual entry"),
            "header": header, "items": items, "shipment_schedule": schedule}
