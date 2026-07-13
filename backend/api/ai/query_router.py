"""
Phase C — "Chat with your data": the routing layer behind POST /ai/query.

Two lanes, tried in order:

  1. TEMPLATE lane (this module, no model needed): a deterministic intent
     router that matches the question against known entities (returns,
     receipts, issues, stock, low stock, expiring, PRs, POs, top suppliers),
     a time window ("last week", "last 30 days", "today", …) and an optional
     site mention, then runs a parameterized SQL template. Every value is a
     bound parameter and — crucially — a site-scoped user's Site_ID is
     ENFORCED server-side from the JWT, which is why this lane is safe for
     HODs (the locked AI-5 ruling excluded scoped roles from raw NL→SQL
     because generated SQL can't be scoped reliably; templates can).

  2. NL lane (fallback, unscoped roles only): the existing
     analytics.run_nl_query() — Ollama coder model → SQL safety gate →
     read-only gi_ai_ro engine. Scoped users never reach this lane; when the
     template lane can't route THEIR question they get a friendly "try one
     of these" message instead.

Result shape (both lanes):
    {ok, mode: 'template'|'nl', intent?, sql, columns, rows,
     metric?: {label, value}, message}
The frontend renders `metric` as a stat card when present, else a table.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# --------------------------------------------------------------------------- #
# question parsing
# --------------------------------------------------------------------------- #
_INTENTS: list[tuple[str, re.Pattern]] = [
    # order matters — first match wins; more specific phrasings first
    ("low_stock", re.compile(r"\b(low stock|below minimum|under minimum|reorder)\b", re.I)),
    ("expiring", re.compile(r"\b(expir\w+|shelf life)\b", re.I)),
    ("top_suppliers", re.compile(r"\b(top|biggest|largest)\b.*\bsuppliers?\b|\bsuppliers?\b.*\b(top|ranking)\b", re.I)),
    ("returns", re.compile(r"\breturn(s|ed)?\b", re.I)),
    ("receipts", re.compile(r"\b(receipts?|received|deliveries|delivered|grn)\b", re.I)),
    ("issues", re.compile(r"\b(issue[sd]?|consum\w+|used|usage|withdrawals?)\b", re.I)),
    ("prs", re.compile(r"\b(purchase requests?|prs?\b|pr status)\b", re.I)),
    ("pos", re.compile(r"\b(purchase orders?|pos?\b|po status)\b", re.I)),
    ("stock", re.compile(r"\b(stock|inventory|on hand|balance)\b", re.I)),
]

_COUNT_RX = re.compile(r"\b(how many|how much|count|total|sum)\b", re.I)
_DAYS_RX = re.compile(r"\blast\s+(\d{1,3})\s+days?\b", re.I)


def _window(q: str, today: date) -> tuple[Optional[str], Optional[str], str]:
    """(date_from, date_to, label) as ISO strings; ledger dates are ISO text."""
    ql = q.lower()
    m = _DAYS_RX.search(ql)
    if m:
        n = max(1, min(365, int(m.group(1))))
        return (today - timedelta(days=n)).isoformat(), None, f"last {n} days"
    if "today" in ql:
        return today.isoformat(), None, "today"
    if "yesterday" in ql:
        y = today - timedelta(days=1)
        return y.isoformat(), today.isoformat(), "yesterday"
    if "last week" in ql or "past week" in ql or "this week" in ql:
        return (today - timedelta(days=7)).isoformat(), None, "last 7 days"
    if "last month" in ql or "past month" in ql:
        return (today - timedelta(days=30)).isoformat(), None, "last 30 days"
    if "this month" in ql:
        return today.replace(day=1).isoformat(), None, "this month"
    if "this year" in ql:
        return today.replace(month=1, day=1).isoformat(), None, "this year"
    if "all time" in ql or "ever" in ql:
        return None, None, "all time"
    return None, None, "all time"


def _mentioned_site(q: str, sites: list[str]) -> Optional[str]:
    ql = q.lower()
    for s in sites:
        if s and re.search(rf"\b{re.escape(s.lower())}\b", ql):
            return s
    return None


# --------------------------------------------------------------------------- #
# SQL templates — every dynamic value is a BOUND PARAMETER
# --------------------------------------------------------------------------- #
_LEDGER = {
    "returns": ('returns', 'r."Reason" AS reason'),
    "receipts": ('receipts', 'r."Supplier" AS supplier'),
    "issues": ('consumption', 'r."Issued_To" AS issued_to, r."Work_Type" AS work_type'),
}


def _ledger_sql(intent: str, count: bool, site: Optional[str],
                dfrom: Optional[str], dto: Optional[str]) -> tuple[str, dict]:
    table, extra = _LEDGER[intent]
    p: dict[str, Any] = {}
    where = ["1=1"]
    if site:
        where.append(f'COALESCE(r."Site_ID", \'HQ\') = :site')
        p["site"] = site
    if dfrom:
        where.append('r."Date" >= :dfrom')
        p["dfrom"] = dfrom
    if dto:
        where.append('r."Date" < :dto')
        p["dto"] = dto
    w = " AND ".join(where)
    if count:
        sql = (f'SELECT COUNT(*) AS entries, COALESCE(SUM(r."Quantity"),0) AS total_qty '
               f'FROM {table} r WHERE {w}')
    else:
        sql = (f'SELECT r."Date" AS date, r."SAP_Code" AS sap_code, '
               f'i."Equipment_Description" AS description, r."Quantity" AS qty, '
               f'{extra}, COALESCE(r."Site_ID", \'HQ\') AS site '
               f'FROM {table} r LEFT JOIN inventory i ON TRIM(i."SAP_Code") = TRIM(r."SAP_Code") '
               f'WHERE {w} ORDER BY r."Date" DESC, r.id DESC LIMIT 100')
    return sql, p


_STOCK_EXPR = ('COALESCE((SELECT SUM(x."Quantity") FROM receipts x WHERE TRIM(x."SAP_Code")=TRIM(i."SAP_Code")),0)'
               ' - COALESCE((SELECT SUM(x."Quantity") FROM consumption x WHERE TRIM(x."SAP_Code")=TRIM(i."SAP_Code")),0)'
               ' - COALESCE((SELECT SUM(x."Quantity") FROM returns x WHERE TRIM(x."SAP_Code")=TRIM(i."SAP_Code")),0)')


def _build(intent: str, count: bool, site: Optional[str],
           dfrom: Optional[str], dto: Optional[str]) -> tuple[str, dict]:
    if intent in _LEDGER:
        return _ledger_sql(intent, count, site, dfrom, dto)
    p: dict[str, Any] = {}
    site_w = 'AND COALESCE(i."Site_ID", \'HQ\') = :site' if site else ''
    if site:
        p["site"] = site
    if intent == "stock":
        return (f'SELECT i."SAP_Code" AS sap_code, i."Equipment_Description" AS description, '
                f'i."Category" AS category, i."UOM" AS uom, {_STOCK_EXPR} AS current_stock '
                f'FROM inventory i WHERE 1=1 {site_w} ORDER BY current_stock DESC LIMIT 100'), p
    if intent == "low_stock":
        return (f'SELECT i."SAP_Code" AS sap_code, i."Equipment_Description" AS description, '
                f'i."Minimum_Qty" AS minimum_qty, {_STOCK_EXPR} AS current_stock '
                f'FROM inventory i WHERE i."Minimum_Qty" IS NOT NULL AND i."Minimum_Qty" > 0 '
                f'AND {_STOCK_EXPR} < i."Minimum_Qty" {site_w} ORDER BY current_stock LIMIT 100'), p
    if intent == "expiring":
        p["cutoff"] = (date.today() + timedelta(days=90)).isoformat()
        return (f'SELECT i."SAP_Code" AS sap_code, i."Equipment_Description" AS description, '
                f'i."Expiry_Date" AS expiry_date, {_STOCK_EXPR} AS current_stock '
                f'FROM inventory i WHERE i."Expiry_Date" IS NOT NULL AND TRIM(i."Expiry_Date") <> \'\' '
                f'AND i."Expiry_Date" <= :cutoff {site_w} ORDER BY i."Expiry_Date" LIMIT 100'), p
    if intent == "top_suppliers":
        w = ['r."Supplier" IS NOT NULL', 'TRIM(r."Supplier") <> \'\'']
        if site:
            w.append('COALESCE(r."Site_ID", \'HQ\') = :site')
        if dfrom:
            w.append('r."Date" >= :dfrom')
            p["dfrom"] = dfrom
        return (f'SELECT r."Supplier" AS supplier, COUNT(*) AS orders, '
                f'SUM(r."Quantity") AS total_qty FROM receipts r WHERE {" AND ".join(w)} '
                f'GROUP BY r."Supplier" ORDER BY total_qty DESC LIMIT 25'), p
    if intent == "prs":
        return (f'SELECT p."PR_Number" AS pr_number, p."SAP_Code" AS sap_code, '
                f'p."Material_Name" AS material, p."Requested_Qty" AS requested_qty, '
                f'p.status AS status, p.workflow_state AS workflow_state, '
                f'COALESCE(p."Site_ID", \'HQ\') AS site FROM pr_master p '
                f'WHERE 1=1 {site_w.replace("i.", "p.")} ORDER BY p.id DESC LIMIT 100'), p
    if intent == "pos":
        return (f'SELECT o."PO_Number" AS po_number, o."PR_Number" AS pr_number, '
                f'o."Vendor_Name" AS vendor, o."PO_Date" AS po_date, o.status AS status, '
                f'COALESCE(o."Site_ID", \'HQ\') AS site FROM purchase_orders o '
                f'WHERE 1=1 {site_w.replace("i.", "o.")} ORDER BY o.id DESC LIMIT 100'), p
    raise KeyError(intent)


_METRIC_LABEL = {"returns": "return entries", "receipts": "receipt entries", "issues": "issue entries"}

EXAMPLES = [
    "Show me all CNCEC material returns from last week",
    "How many issues in the last 30 days?",
    "Items below minimum stock",
    "Top suppliers by received quantity last 90 days",
    "Purchase requests status",
    "Expiring stock",
]


def _plain(v: Any):
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if hasattr(v, "quantize"):  # Decimal
        return float(v)
    return v


async def run_query(session: AsyncSession, question: str, *,
                    site_scope: Optional[str], known_sites: list[str]) -> Optional[dict]:
    """Template lane. Returns a result dict, or None when no intent matched
    (caller decides whether the NL lane may take over)."""
    q = question.strip()
    intent = next((name for name, rx in _INTENTS if rx.search(q)), None)
    if intent is None:
        return None

    count = bool(_COUNT_RX.search(q)) and intent in _LEDGER
    dfrom, dto, wlabel = _window(q, date.today())
    # default ledger windows to 30 days so "show me returns" isn't ALL history
    if intent in _LEDGER and dfrom is None and dto is None and "all time" not in q.lower():
        dfrom, wlabel = (date.today() - timedelta(days=30)).isoformat(), "last 30 days"

    if site_scope is not None:
        site: Optional[str] = site_scope or "__none__"  # '' scope = matches nothing
    else:
        site = _mentioned_site(q, known_sites)

    sql, params = _build(intent, count, site, dfrom, dto)
    res = await session.execute(text(sql), params)
    rows = res.mappings().all()
    columns = list(rows[0].keys()) if rows else list(res.keys())
    out: dict[str, Any] = {
        "ok": True, "mode": "template", "intent": intent, "sql": sql,
        "columns": columns, "rows": [[_plain(r[c]) for c in columns] for r in rows],
        "message": f"{intent.replace('_', ' ')} · {wlabel}"
                   + (f" · site {site}" if site and site != "__none__" else " · all sites"),
    }
    if count and rows:
        out["metric"] = {"label": f"{_METRIC_LABEL.get(intent, intent)} ({wlabel})",
                         "value": _plain(rows[0]["total_qty"]),
                         "entries": _plain(rows[0]["entries"])}
    return out
