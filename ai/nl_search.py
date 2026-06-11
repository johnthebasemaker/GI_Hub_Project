"""
ai/nl_search.py — Natural-language inventory search (qwen2.5-coder → SQL)
=========================================================================
User types a question in plain English. We:

  1. Build a tight schema prompt (only safe tables, only useful columns).
  2. Ask qwen2.5-coder:7b to emit a single SQLite SELECT statement.
  3. Strip any markdown fences / commentary the model might have added.
  4. Validate against `ai.safety.is_safe_select` — bail if it fails.
  5. Scrub to inject a LIMIT clause if missing.
  6. Run on a **read-only** SQLite connection so even a model jailbreak
     can't mutate state.
  7. Return the result DataFrame + the SQL we actually ran (for transparency).

Why this is safe alongside cache_layer
--------------------------------------
NL queries open their own read-only connection and never touch the
cache_layer functions. The cache stays accurate for the existing pages;
NL search is a parallel read path.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Tuple

import pandas as pd

from ai.client import ollama_generate, MODEL_CODER, OLLAMA_AVAILABLE
from ai.safety import is_safe_select, scrub_sql
from database import DB_FILE


# ---------------------------------------------------------------------------
# SCHEMA HINT — small, hand-curated, intentionally excludes `users`.
# Keeping this curated (not auto-introspected) means the model sees only the
# columns we want it to query — no PII, no audit-log leakage.
# ---------------------------------------------------------------------------
SCHEMA_HINT = """\
You write SQLite SELECT queries for a warehouse inventory database.

=== STOCK QUESTIONS: ALWAYS use these pre-computed VIEWS ===
For ANY question about current stock, low stock, stock levels, what is
running out, available quantity, or stock at a site — query a VIEW below.
The views already contain the correct Current_Stock. NEVER compute stock
yourself by joining receipts/consumption — you will get it wrong.

v_live_stock(            -- one row per item, ALL sites combined (global)
  SAP_Code TEXT, Equipment_Description TEXT, Material_Code TEXT, UOM TEXT,
  Minimum_Qty REAL, Total_Received REAL, Total_Consumed REAL,
  Total_Returned REAL, Current_Stock REAL
)

v_site_stock(            -- one row per (item, site); use when a site is named
  SAP_Code TEXT, Site_ID TEXT, Equipment_Description TEXT, Material_Code TEXT,
  UOM TEXT, Minimum_Qty REAL, Total_Received REAL, Total_Consumed REAL,
  Total_Returned REAL, Current_Stock REAL
)

  - "items low on stock" / "below minimum":
        SELECT SAP_Code, Equipment_Description, Current_Stock, Minimum_Qty
        FROM v_live_stock WHERE Current_Stock < Minimum_Qty
        ORDER BY Current_Stock ASC
  - "low stock at HQ":  same but FROM v_site_stock WHERE Site_ID = 'HQ' AND ...
  - "out of stock":     WHERE Current_Stock <= 0

=== EXPIRY QUESTIONS: use this VIEW ===
For anything about expiry, expired, short-dated, shelf-life, or "expiring in N
days", query v_expiring_stock (one row per dated receipt batch):

v_expiring_stock(
  SAP_Code TEXT, Equipment_Description TEXT, UOM TEXT, Site_ID TEXT,
  Quantity REAL, Supplier TEXT, PR_Number TEXT, Expiry_Date TEXT,
  Days_Until_Expiry INTEGER,      -- negative = already expired
  Expiry_Status TEXT              -- 'Expired' | 'Short-Dated' | 'Good'
)

=== SUPPLIER QUESTIONS: use this VIEW ===
For supplier totals / rankings / "who supplies X", query v_supplier_activity:

v_supplier_activity(
  Supplier TEXT, Site_ID TEXT, Receipt_Count INTEGER, Distinct_Items INTEGER,
  Total_Received REAL, First_Receipt_Date TEXT, Last_Receipt_Date TEXT
)

=== DETAIL TABLES: for individual history rows / work types / PRs ===
receipts(Date, SAP_Code, Quantity, Supplier, Expiry_Date, PR_Number, Site_ID, Remarks)
consumption(Date, SAP_Code, Quantity, Work_Type, Issued_By, Issued_To, Tank_No, PR_Number, Site_ID, Remarks)
pr_master(PR_Number, SAP_Code, Material_Name, Requested_Qty, Site_ID, status, created_at)
inventory(SAP_Code, Equipment_Description, Material_Code, UOM, Minimum_Qty, Site_ID)

=== EXAMPLES (question → SQL) ===
Q: which items are low on stock
```sql
SELECT SAP_Code, Equipment_Description, Current_Stock, Minimum_Qty
FROM v_live_stock WHERE Current_Stock < Minimum_Qty
ORDER BY Current_Stock ASC LIMIT 100
```

Q: what is out of stock at HQ
```sql
SELECT SAP_Code, Equipment_Description, Current_Stock
FROM v_site_stock WHERE Site_ID = 'HQ' AND Current_Stock <= 0
ORDER BY Current_Stock ASC LIMIT 100
```

Q: items expiring in the next 60 days
```sql
SELECT SAP_Code, Equipment_Description, Expiry_Date, Days_Until_Expiry, Quantity
FROM v_expiring_stock
WHERE Days_Until_Expiry BETWEEN 0 AND 60
ORDER BY Days_Until_Expiry ASC LIMIT 100
```

Q: top 5 suppliers by quantity received
```sql
SELECT Supplier, SUM(Total_Received) AS Total_Received
FROM v_supplier_activity
GROUP BY Supplier ORDER BY Total_Received DESC LIMIT 5
```

Q: top 10 consumed materials this month
```sql
SELECT SAP_Code, SUM(Quantity) AS Consumed
FROM consumption
WHERE Date >= date('now','start of month')
GROUP BY SAP_Code ORDER BY Consumed DESC LIMIT 10
```

=== RULES (follow exactly) ===
- SQLite syntax only. Output ONE SELECT (or WITH … SELECT). No explanation.
- Wrap the final query in a ```sql … ``` fenced block.
- NEVER invent placeholder values like 'your_site_id'. If the user does not
  name a specific site, do NOT add any Site_ID filter — query v_live_stock
  (the global view) so every item is included.
- Only filter by Site_ID when the user explicitly names a site, and then use
  v_site_stock with the literal site name they gave.
- Dates are ISO strings 'YYYY-MM-DD'; use date('now','-30 days') for windows.
- Never reference any table or view not listed above. Never query `users`.
- Add ORDER BY when the question implies ranking. Add LIMIT 100 unless the
  user explicitly asks for more or for a single value.
- Do NOT add SQL comments to the query.
"""


_SQL_FENCE = re.compile(
    r"```(?:sql)?\s*(.+?)\s*```",
    re.IGNORECASE | re.DOTALL,
)


def _extract_sql(raw: str) -> str:
    """
    Models often wrap SQL in ```sql ... ``` fences or add a stray comment.
    Pull out the first fenced block; otherwise return the raw text trimmed.
    """
    m = _SQL_FENCE.search(raw)
    if m:
        return m.group(1).strip()
    return raw.strip()


def run_nl_query(question: str) -> Tuple[bool, str, pd.DataFrame, str]:
    """
    Translate `question` to SQL and execute it on a read-only connection.

    Returns (ok, message, df, sql_actually_run).
      ok=True  → df has the rows, message is empty
      ok=False → df is empty, message explains why (model error, unsafe SQL,
                 execution failure, or Ollama unreachable)
    """
    if not OLLAMA_AVAILABLE:
        return False, (
            "Local AI is unavailable. Make sure `ollama serve` is running "
            "and `qwen2.5-coder:7b` is pulled."
        ), pd.DataFrame(), ""

    if not question or not question.strip():
        return False, "Type a question first.", pd.DataFrame(), ""

    system_prompt = (
        "You translate plain-English warehouse questions into a single "
        "SQLite SELECT statement. You never explain, you only emit SQL."
    )
    user_prompt = f"{SCHEMA_HINT}\n\nQuestion: {question.strip()}\n\nSQL:"

    try:
        raw = ollama_generate(
            MODEL_CODER,
            prompt=user_prompt,
            system=system_prompt,
            temperature=0.1,
            num_predict=400,
        )
    except RuntimeError as e:
        return False, f"AI request failed: {e}", pd.DataFrame(), ""

    candidate = _extract_sql(raw)

    ok, why = is_safe_select(candidate)
    if not ok:
        return False, f"AI generated an unsafe query — {why}", pd.DataFrame(), candidate

    safe_sql = scrub_sql(candidate)

    # Read-only connection. `?mode=ro` is honoured by SQLite URI syntax and
    # rejects writes at the engine level — defence in depth on top of the
    # safety validator above.
    ro_uri = f"file:{DB_FILE}?mode=ro"
    try:
        conn = sqlite3.connect(ro_uri, uri=True)
        try:
            df = pd.read_sql(safe_sql, conn)
        finally:
            conn.close()
    except sqlite3.Error as e:
        return False, f"SQL execution failed: {e}", pd.DataFrame(), safe_sql

    return True, "", df, safe_sql
