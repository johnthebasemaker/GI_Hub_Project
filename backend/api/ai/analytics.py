"""
backend/api/ai/analytics.py — Phase AI-5: NL→SQL + insight probes + EOD summary.

NL→SQL security model (two independent walls):
  1. safety.is_safe_select() — the PG-hardened text gate (SELECT/WITH only,
     keyword blocklist incl. COPY/DO/CALL, users/auth_sessions/pg_catalog
     banned, LIMIT injected).
  2. A TRUE PostgreSQL read-only login (`gi_ai_ro`, see
     backend/scripts/create_ai_readonly_role.sql): default_transaction_read_only,
     statement_timeout=5s at the ROLE level, and REVOKEd SELECT on the
     sensitive tables — a gate bypass still physically cannot write or read
     users. The AI never touches the app's main engine for generated SQL.

Insight probes are OUR OWN deterministic SQL (ported from legacy
ai/insights.py, SQLite date fns → PG intervals; Date columns are ISO TEXT so
comparisons cast CURRENT_DATE to text). SQL owns the numbers; the LLM only
narrates — same division of labor as legacy.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from ..config import async_database_url
from . import client as aic
from .safety import is_safe_select, scrub_sql

# --- read-only engine (gi_ai_ro) -------------------------------------------------
_RO_ENGINE: Optional[AsyncEngine] = None


def _ro_url() -> str:
    override = os.environ.get("GI_AI_RO_URL")
    if override:
        return override
    # Derive from the app URL by swapping the username (local trust auth).
    url = async_database_url()
    return re.sub(r"//[^:@/]+(:[^@/]*)?@", "//gi_ai_ro@", url)


def ro_engine() -> AsyncEngine:
    global _RO_ENGINE
    if _RO_ENGINE is None:
        _RO_ENGINE = create_async_engine(_ro_url(), pool_size=2, max_overflow=2,
                                         pool_pre_ping=True)
    return _RO_ENGINE


# --- NL→SQL -----------------------------------------------------------------------
# Hand-curated schema hint (PG spelling: quoted mixed-case identifiers; the
# legacy hint pointed at SQLite views which don't exist in PG, so live-stock
# is shown as a worked pattern instead). `users`/auth tables deliberately
# absent — and unreadable by the role anyway.
SCHEMA_HINT = '''
You write ONE PostgreSQL SELECT statement for a warehouse ERP. Column names
are mixed-case and MUST be double-quoted exactly as shown.

Tables:
- inventory("SAP_Code" text PK, "Equipment_Description" text, "Material_Code" text,
    "Category" text, "UOM" text, "Minimum_Qty" float, "Opening_Stock" float,
    "Site_ID" text, "Expiry_Date" text, "Sl_No" text)
- receipts(id, "Date" text 'YYYY-MM-DD', "SAP_Code" text, "Quantity" float,
    "Supplier" text, "Site_ID" text, "PR_Number" text, "DN_No" text,
    "WBS" text, "Vehicle_No" text, "Driver_Name" text, "Mob_From" text)
- consumption(id, "Date" text 'YYYY-MM-DD', "SAP_Code" text, "Quantity" float,
    "Work_Type" text, "Issued_To" text, "Issued_By" text, "Tank_No" text,
    "WBS" text, "Site_ID" text)
- returns(id, "Date" text, "SAP_Code" text, "Quantity" float, "Reason" text,
    "Site_ID" text)
- pr_master(id, "PR_Number" text, "SAP_Code" text, "Material_Name" text,
    "Requested_Qty" float, "Site_ID" text, status text, workflow_state text)
- purchase_orders(id, "PO_Number" text, "PR_Number" text, "Vendor_Name" text,
    "PO_Date" text, "Site_ID" text, status text)
- sme_recipe(id, "Lining_System_Code" text, "Lining_System_Name" text,
    "Material_Code" text, "SAP_Code" text, "Material_Name" text,
    "Material_Description" text, "For_1_SQM" float, "UOM" text)
    — lining-system BOM lines; "SAP_Code" joins straight to
    inventory."SAP_Code" (incl. variant SAPs like '1041-1')
- sme_equipment(id, "Site_ID" text, "Equipment_Tag_No" text,
    "Lining_System_Code" text, "Surface_Area_SQM" float, "Location" text,
    "Name" text, "Substrate" text)
- sme_sqm_progress("Site_ID" text, "Equipment_Tag_No" text,
    "Lining_System_Code" text, "Original_SQM" float, "Done_SQM" float)

Facts:
- "Date"/"Expiry_Date" are ISO text — compare with e.g.
  "Date" >= (CURRENT_DATE - INTERVAL '30 days')::date::text
- Live stock per item = SUM(receipts) − SUM(consumption) − SUM(returns)
  ("Opening_Stock" exists but is 0 for every item — the ledger already
  carries the full history, never add it on top).
- Join consumption/receipts to inventory on "SAP_Code" for descriptions.
- "Category" values include 'Surface Shields' (rubber lining materials — the
  ones that require an MTC certificate on receipt), 'R/L Consumables',
  'R/L Tools', 'EQUIPMENTS/TOOLS', 'BR CC PU Tools', 'Safety', 'Office',
  'Electrical Items', 'VEHICLES', 'CONTRACTING SERVICES'. Users may say
  "surface shield" (singular) — match with "Category" ILIKE 'surface shield%'.
- "DN_No" is the delivery-note ("DN") number; "WBS" is the work-breakdown
  code; "Tank_No" identifies the tank an issue was consumed on.
- "Site_ID" values are short site codes like 'CNCEC' and 'HQ'.
- When the user names a MATERIAL FAMILY or brand ('remafix', 'furan',
  'chemoline', 'cumifuran'…) rather than a category, search DEEP with ILIKE
  on BOTH the ERP description and the SME material names via the SAP join:
    i."Equipment_Description" ILIKE '%furan%'
    OR EXISTS (SELECT 1 FROM sme_recipe sr
               WHERE TRIM(sr."SAP_Code") = TRIM(i."SAP_Code")
               AND (sr."Material_Name" ILIKE '%furan%'
                    OR sr."Material_Description" ILIKE '%furan%'))
  Never answer such questions from memory — always filter with ILIKE.

Examples:
Q: items below minimum stock
SQL: SELECT i."SAP_Code", i."Equipment_Description", i."Minimum_Qty",
  COALESCE((SELECT SUM(r."Quantity") FROM receipts r WHERE r."SAP_Code"=i."SAP_Code"),0)
  - COALESCE((SELECT SUM(c."Quantity") FROM consumption c WHERE c."SAP_Code"=i."SAP_Code"),0)
  AS current_stock FROM inventory i WHERE i."Minimum_Qty" IS NOT NULL
  AND COALESCE((SELECT SUM(r."Quantity") FROM receipts r WHERE r."SAP_Code"=i."SAP_Code"),0)
    - COALESCE((SELECT SUM(c."Quantity") FROM consumption c WHERE c."SAP_Code"=i."SAP_Code"),0)
    < i."Minimum_Qty" ORDER BY current_stock

Q: top suppliers by receipt quantity in the last 90 days
SQL: SELECT "Supplier", COUNT(*) AS orders, SUM("Quantity") AS total_qty
  FROM receipts WHERE "Supplier" IS NOT NULL AND "Supplier" <> ''
  AND "Date" >= (CURRENT_DATE - INTERVAL '90 days')::date::text
  GROUP BY "Supplier" ORDER BY total_qty DESC

Q: what was received on delivery note 15610
SQL: SELECT r."Date", r."SAP_Code", i."Equipment_Description", r."Quantity",
  r."Supplier" FROM receipts r JOIN inventory i ON i."SAP_Code" = r."SAP_Code"
  WHERE r."DN_No" = '15610' ORDER BY r."Date", r."SAP_Code"

Q: surface shields stock by item
SQL: SELECT i."SAP_Code", i."Equipment_Description",
  COALESCE((SELECT SUM(r."Quantity") FROM receipts r WHERE r."SAP_Code"=i."SAP_Code"),0)
  - COALESCE((SELECT SUM(c."Quantity") FROM consumption c WHERE c."SAP_Code"=i."SAP_Code"),0)
  - COALESCE((SELECT SUM(t."Quantity") FROM returns t WHERE t."SAP_Code"=i."SAP_Code"),0)
  AS current_stock FROM inventory i
  WHERE i."Category" ILIKE 'surface shield%' ORDER BY current_stock DESC

Q: current stock of furan materials
SQL: SELECT i."SAP_Code", i."Equipment_Description", i."Category",
  COALESCE((SELECT SUM(r."Quantity") FROM receipts r WHERE r."SAP_Code"=i."SAP_Code"),0)
  - COALESCE((SELECT SUM(c."Quantity") FROM consumption c WHERE c."SAP_Code"=i."SAP_Code"),0)
  - COALESCE((SELECT SUM(t."Quantity") FROM returns t WHERE t."SAP_Code"=i."SAP_Code"),0)
  AS current_stock FROM inventory i
  WHERE i."Equipment_Description" ILIKE '%furan%'
  OR EXISTS (SELECT 1 FROM sme_recipe sr
             WHERE TRIM(sr."SAP_Code") = TRIM(i."SAP_Code")
             AND (sr."Material_Name" ILIKE '%furan%'
                  OR sr."Material_Description" ILIKE '%furan%'))
  ORDER BY current_stock DESC

Rules: output ONLY the SQL (no fences, no prose). SELECT/WITH only. Always
double-quote mixed-case identifiers. Add a sensible ORDER BY.
'''

_SQL_FENCE = re.compile(r"```(?:sql)?\s*(.+?)\s*```", re.IGNORECASE | re.DOTALL)


def extract_sql(raw: str) -> str:
    m = _SQL_FENCE.search(raw or "")
    sql = (m.group(1) if m else (raw or "")).strip()
    # Drop any chatter before the first SELECT/WITH.
    m2 = re.search(r"\b(SELECT|WITH)\b", sql, re.IGNORECASE)
    return sql[m2.start():].strip() if m2 else sql


async def run_nl_query(question: str) -> dict:
    """English → SQL (qwen2.5-coder) → safety gate → RO engine. Returns
    {ok, message, sql, columns, rows} — never raises for model/SQL problems."""
    if not await aic.health():
        return {"ok": False, "sql": "", "columns": [], "rows": [],
                "message": "Local AI is offline — ask your admin to start Ollama."}
    try:
        async with aic.GEN_SEMAPHORE:
            raw = await aic.generate(
                aic.MODEL_CODER, f"Question: {question}\nSQL:",
                system=SCHEMA_HINT, temperature=0.1, num_predict=400)
    except RuntimeError as e:
        return {"ok": False, "sql": "", "columns": [], "rows": [], "message": str(e)}

    sql = extract_sql(raw)
    ok, reason = is_safe_select(sql)
    if not ok:
        return {"ok": False, "sql": sql, "columns": [], "rows": [],
                "message": f"Generated SQL was rejected by the safety gate: {reason}"}
    sql = scrub_sql(sql)
    try:
        async with ro_engine().connect() as conn:
            res = await conn.execute(text(sql))
            rows = res.mappings().all()
    except Exception as e:
        return {"ok": False, "sql": sql, "columns": [], "rows": [],
                "message": f"Query failed: {type(e).__name__}: {str(e)[:200]}"}
    columns = list(rows[0].keys()) if rows else []
    return {"ok": True, "sql": sql, "columns": columns,
            "rows": [[_plain(r[c]) for c in columns] for r in rows],
            "message": f"{len(rows)} row(s)"}


def _plain(v: Any):
    if isinstance(v, (_dt.date, _dt.datetime)):
        return v.isoformat()
    if hasattr(v, "quantize"):  # Decimal
        return float(v)
    return v


# --- Insight probes (PG ports of legacy ai/insights.py) ---------------------------
def _site_clause(site_id: Optional[str], col: str = '"Site_ID"') -> str:
    return f" AND COALESCE({col},'HQ') = :site" if site_id else ""


async def _probe_consumption_spike(session, site_id):
    sql = f'''
        SELECT i."SAP_Code" AS sap, i."Equipment_Description" AS material,
               SUM(CASE WHEN c."Date" >= (CURRENT_DATE - INTERVAL '30 days')::date::text
                        THEN c."Quantity" ELSE 0 END) AS last_30,
               SUM(CASE WHEN c."Date" BETWEEN (CURRENT_DATE - INTERVAL '120 days')::date::text
                        AND (CURRENT_DATE - INTERVAL '31 days')::date::text
                        THEN c."Quantity" ELSE 0 END) / 3.0 AS avg_30
        FROM consumption c JOIN inventory i ON c."SAP_Code" = i."SAP_Code"
        WHERE c."Date" >= (CURRENT_DATE - INTERVAL '120 days')::date::text
              {_site_clause(site_id, 'c."Site_ID"')}
        GROUP BY i."SAP_Code", i."Equipment_Description"
        HAVING SUM(CASE WHEN c."Date" >= (CURRENT_DATE - INTERVAL '30 days')::date::text
                        THEN c."Quantity" ELSE 0 END) > 0
           AND SUM(CASE WHEN c."Date" BETWEEN (CURRENT_DATE - INTERVAL '120 days')::date::text
                        AND (CURRENT_DATE - INTERVAL '31 days')::date::text
                        THEN c."Quantity" ELSE 0 END) > 0
        ORDER BY (SUM(CASE WHEN c."Date" >= (CURRENT_DATE - INTERVAL '30 days')::date::text
                           THEN c."Quantity" ELSE 0 END)
                  / NULLIF(SUM(CASE WHEN c."Date" BETWEEN (CURRENT_DATE - INTERVAL '120 days')::date::text
                               AND (CURRENT_DATE - INTERVAL '31 days')::date::text
                               THEN c."Quantity" ELSE 0 END) / 3.0, 0)) DESC
        LIMIT 1'''
    r = (await session.execute(text(sql), {"site": site_id} if site_id else {})
         ).mappings().first()
    if not r:
        return None
    ratio = float(r["last_30"]) / max(float(r["avg_30"]), 0.01)
    if ratio < 1.4:  # legacy signal threshold
        return None
    return {"sap": r["sap"], "material": r["material"],
            "last_30": float(r["last_30"]), "avg_30": float(r["avg_30"]),
            "ratio_pct": int((ratio - 1.0) * 100),
            "metric": f"{float(r['last_30']):.0f} units",
            "metric_label": f"vs {float(r['avg_30']):.0f} avg",
            "severity": "crit" if ratio >= 2.0 else "low"}


async def _probe_projected_stockouts(session, site_id):
    sc = _site_clause(site_id, 'r."Site_ID"')
    sc_c = _site_clause(site_id, 'c."Site_ID"')
    sc_c2 = _site_clause(site_id, 'c2."Site_ID"')
    sql = f'''
        WITH stock AS (
          SELECT i."SAP_Code", i."Equipment_Description" AS material,
            COALESCE((SELECT SUM(r."Quantity") FROM receipts r
                      WHERE r."SAP_Code" = i."SAP_Code"{sc}), 0)
          - COALESCE((SELECT SUM(c."Quantity") FROM consumption c
                      WHERE c."SAP_Code" = i."SAP_Code"{sc_c}), 0) AS current_stock,
            COALESCE((SELECT SUM(c2."Quantity") FROM consumption c2
                      WHERE c2."SAP_Code" = i."SAP_Code"
                        AND c2."Date" >= (CURRENT_DATE - INTERVAL '30 days')::date::text
                        {sc_c2}), 0) / 30.0 AS daily_burn
          FROM inventory i)
        SELECT material, current_stock / NULLIF(daily_burn, 0) AS days_left
        FROM stock WHERE daily_burn > 0
          AND current_stock / NULLIF(daily_burn, 0) <= 14
        ORDER BY days_left'''
    rows = (await session.execute(text(sql), {"site": site_id} if site_id else {})
            ).mappings().all()
    if not rows:
        return None
    return {"count": len(rows),
            "names": ", ".join(str(x["material"]) for x in rows[:5]),
            "min_days": int(min(float(x["days_left"]) for x in rows)),
            "max_days": int(max(float(x["days_left"]) for x in rows)),
            "metric": f"{len(rows)} items", "metric_label": "≤14d to stockout",
            "severity": "crit" if len(rows) >= 3 else "low"}


async def _probe_expired_lots(session, site_id):
    sql = f'''
        SELECT "SAP_Code" AS sap, "Equipment_Description" AS material
        FROM inventory WHERE "Expiry_Date" IS NOT NULL AND "Expiry_Date" <> ''
          AND "Expiry_Date" < CURRENT_DATE::text
          {_site_clause(site_id)} LIMIT 50'''
    rows = (await session.execute(text(sql), {"site": site_id} if site_id else {})
            ).mappings().all()
    if not rows:
        return None
    return {"count": len(rows),
            "names": ", ".join(str(x["material"]) for x in rows[:5]),
            "metric": f"{len(rows)} lots", "metric_label": "expired on shelf",
            "severity": "crit" if len(rows) >= 2 else "low"}


async def _probe_supplier_consolidation(session, site_id):
    sql = '''
        SELECT "Supplier" AS supplier, COUNT(*) AS n_orders,
               COALESCE(SUM("Quantity"), 0) AS total_qty
        FROM receipts WHERE "Supplier" IS NOT NULL AND "Supplier" <> ''
          AND "Date" >= (CURRENT_DATE - INTERVAL '90 days')::date::text
        GROUP BY "Supplier" HAVING COUNT(*) >= 3
        ORDER BY COUNT(*) DESC LIMIT 1'''
    r = (await session.execute(text(sql))).mappings().first()
    if not r:
        return None
    return {"supplier": r["supplier"], "n_orders": int(r["n_orders"]),
            "total_qty": float(r["total_qty"]),
            "metric": f"{int(r['n_orders'])} orders",
            "metric_label": f"{r['supplier']} · 90d", "severity": "ok"}


async def _probe_health_score(session, site_id):
    p = {"site": site_id} if site_id else {}
    sc_i = " WHERE COALESCE(\"Site_ID\",'HQ') = :site" if site_id else ""
    n_inv = (await session.execute(text(
        f'SELECT COUNT(*) FROM inventory{sc_i}'), p)).scalar_one() or 1
    sc_r = _site_clause(site_id, 'r."Site_ID"')
    sc_c = _site_clause(site_id, 'c."Site_ID"')
    sc_and = " AND COALESCE(i.\"Site_ID\",'HQ') = :site" if site_id else ""
    n_low = (await session.execute(text(f'''
        SELECT COUNT(*) FROM inventory i WHERE i."Minimum_Qty" IS NOT NULL
        AND COALESCE((SELECT SUM(r."Quantity") FROM receipts r
                      WHERE r."SAP_Code" = i."SAP_Code"{sc_r}), 0)
          - COALESCE((SELECT SUM(c."Quantity") FROM consumption c
                      WHERE c."SAP_Code" = i."SAP_Code"{sc_c}), 0)
          < i."Minimum_Qty"{sc_and}'''), p)).scalar_one() or 0
    n_exp = (await session.execute(text(f'''
        SELECT COUNT(*) FROM inventory
        WHERE "Expiry_Date" IS NOT NULL AND "Expiry_Date" <> ''
          AND "Expiry_Date" < CURRENT_DATE::text
          {_site_clause(site_id)}'''), p)).scalar_one() or 0
    score = max(0, 100 - min(40, int(n_low / max(n_inv, 1) * 100))
                - min(30, n_exp * 6))
    sev = "ok" if score >= 70 else ("low" if score >= 50 else "crit")
    return {"score": score, "n_low": int(n_low), "n_expired": int(n_exp),
            "n_total": int(n_inv), "metric": f"{score}/100",
            "metric_label": "inventory health", "severity": sev}


PROBES = [
    ("consumption_spike", "📉", _probe_consumption_spike, 94),
    ("projected_stockouts", "⚠️", _probe_projected_stockouts, 89),
    ("expired_lots_on_shelf", "🏷️", _probe_expired_lots, 97),
    ("supplier_consolidation", "💰", _probe_supplier_consolidation, 81),
    ("inventory_health_score", "✅", _probe_health_score, 99),
]

COMMENTARY_PROMPT = """You are an inventory operations analyst. Given a structured data
finding from a warehouse ERP, write:

1. A 2-3 sentence narrative explanation (plain English, no jargon).
2. Exactly 3 recommendations (one short sentence each).

Return STRICT JSON only — no markdown, no preamble. Schema:
{
  "title": "Short headline (<= 60 chars)",
  "body":  "2-3 sentence narrative",
  "recs":  ["rec 1", "rec 2", "rec 3"]
}

Finding:
"""


def fallback_commentary(kind: str) -> dict:
    return {"title": kind.replace("_", " ").title(),
            "body": "AI commentary unavailable. Inspect the metric directly.",
            "recs": ["Review the underlying metric.",
                     "Take site-level corrective action.",
                     "Re-run insights after the next EOD commit."]}


async def llm_commentary(kind: str, data: dict) -> dict:
    """Single generation per probe; deterministic fallback on ANY failure —
    the insights stream never dies because of the narration layer."""
    try:
        if not await aic.health():
            return fallback_commentary(kind)
        finding = json.dumps({"kind": kind, "data": data}, default=str)
        async with aic.GEN_SEMAPHORE:
            raw = await aic.generate(aic.MODEL_CHAT, COMMENTARY_PROMPT + finding,
                                     temperature=0.3, num_predict=400)
        start, end = raw.find("{"), raw.rfind("}")
        if start == -1 or end <= start:
            return fallback_commentary(kind)
        parsed = json.loads(raw[start:end + 1])
        if not all(k in parsed for k in ("title", "body", "recs")) \
                or not isinstance(parsed["recs"], list):
            return fallback_commentary(kind)
        return {"title": str(parsed["title"])[:120],
                "body": str(parsed["body"])[:800],
                "recs": [str(x)[:200] for x in parsed["recs"][:3]]}
    except Exception:
        return fallback_commentary(kind)


# --- EOD summary context (PG port of legacy ai/summarize.py) -----------------------
async def build_eod_context(session, report_date: str,
                            site_id: Optional[str]) -> str:
    """≤1.5 KB text snapshot of the day — totals, per-site consumption, top-10
    low stock — mirroring the legacy builder (site filter added: the new
    stack scopes hods to their site)."""
    p: dict = {"d": report_date}
    sc = ""
    if site_id:
        p["site"] = site_id
        sc = " AND COALESCE(\"Site_ID\",'HQ') = :site"
    cons = (await session.execute(text(
        f'SELECT COALESCE("Site_ID",\'HQ\') AS site, COUNT(*) AS n, '
        f'COALESCE(SUM("Quantity"),0) AS qty FROM consumption '
        f'WHERE "Date" = :d{sc} GROUP BY 1'), p)).mappings().all()
    recv = (await session.execute(text(
        f'SELECT COUNT(*) AS n, COALESCE(SUM("Quantity"),0) AS qty '
        f'FROM receipts WHERE "Date" = :d{sc}'), p)).mappings().first()
    sc_r = _site_clause(site_id, 'r."Site_ID"')
    sc_c = _site_clause(site_id, 'c."Site_ID"')
    low = (await session.execute(text(f'''
        SELECT i."SAP_Code" AS sap, i."Equipment_Description" AS descr,
               i."Minimum_Qty" AS min_qty,
          COALESCE((SELECT SUM(r."Quantity") FROM receipts r
                    WHERE r."SAP_Code" = i."SAP_Code"{sc_r}), 0)
        - COALESCE((SELECT SUM(c."Quantity") FROM consumption c
                    WHERE c."SAP_Code" = i."SAP_Code"{sc_c}), 0) AS stock
        FROM inventory i WHERE i."Minimum_Qty" IS NOT NULL
        AND COALESCE((SELECT SUM(r."Quantity") FROM receipts r
                      WHERE r."SAP_Code" = i."SAP_Code"{sc_r}), 0)
          - COALESCE((SELECT SUM(c."Quantity") FROM consumption c
                      WHERE c."SAP_Code" = i."SAP_Code"{sc_c}), 0)
          < i."Minimum_Qty"
        ORDER BY (COALESCE((SELECT SUM(r."Quantity") FROM receipts r
                            WHERE r."SAP_Code" = i."SAP_Code"{sc_r}), 0)
                - COALESCE((SELECT SUM(c."Quantity") FROM consumption c
                            WHERE c."SAP_Code" = i."SAP_Code"{sc_c}), 0)
                - i."Minimum_Qty") ASC
        LIMIT 10'''), {"site": site_id} if site_id else {})).mappings().all()

    total_n = sum(int(r["n"]) for r in cons)
    total_q = sum(float(r["qty"]) for r in cons)
    site_lines = "\n".join(f"  - {r['site']}: {float(r['qty']):g} units"
                           for r in cons) or "  (no site activity)"
    low_lines = "\n".join(
        f"  - [{r['sap']}] {r['descr']}: stock {float(r['stock']):g}, "
        f"minimum {float(r['min_qty']):g}" for r in low) or "  (none flagged)"
    return (f"Date: {report_date}\n\n"
            f"Consumption rows today: {total_n}  (total qty: {total_q:g})\n"
            f"Receipt rows today: {int(recv['n'])}  (total qty: {float(recv['qty']):g})\n\n"
            f"Per-site consumption:\n{site_lines}\n\n"
            f"Top low-stock items (Current_Stock < Minimum_Qty):\n{low_lines}")


EOD_SYSTEM_PROMPT = (
    "You are a concise warehouse-operations analyst. You produce 3 to "
    "6 sentences summarizing the day for a department manager. Mention "
    "totals, notable site differences, and the most critical low-stock "
    "items by name. Do NOT invent numbers. Plain prose, no bullet lists, "
    "no markdown headings.")
