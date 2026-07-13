"""
ai/summarize.py — Streaming EOD executive summary (llama3.1:8b)
================================================================
Builds a compact, structured context from today's database state and asks
llama3.1:8b to narrate it as a 3–6 sentence executive summary suitable for
a daily ops email or a manager glance.

Why a stream
------------
On a MacBook Air, llama3.1:8b runs at roughly 15–40 tok/s. A 200-token
summary is ~5–13s — long enough that batch rendering feels broken.
`stream_eod_summary` yields token chunks so the UI can pipe them through
`st.write_stream` for a responsive feel.

Context is read on a fresh DB connection that closes BEFORE the LLM call
starts, so we don't hold the SQLite write lock across the slow Ollama
round-trip.
"""

from __future__ import annotations

import datetime
from typing import Iterator, Optional

import pandas as pd

from ai.client import ollama_stream, MODEL_CHAT, OLLAMA_AVAILABLE
from database import get_connection


# ---------------------------------------------------------------------------
# CONTEXT BUILDERS — read-only queries that mirror what the daily report shows
# ---------------------------------------------------------------------------
def _build_context(report_date: datetime.date) -> str:
    """
    Compose a short textual snapshot of today's activity. Kept small on
    purpose — feeding the LLM a 50 KB SQL dump produces noisy output and
    slow generation. Aim for ≤ 1.5 KB of pre-summary context.
    """
    conn = get_connection()
    try:
        date_str = report_date.isoformat()

        consumption_df = pd.read_sql(
            "SELECT SAP_Code, Quantity, Work_Type, Site_ID "
            "FROM consumption WHERE Date = ? LIMIT 200",
            conn, params=(date_str,),
        )
        receipts_df = pd.read_sql(
            "SELECT SAP_Code, Quantity, Supplier, Site_ID "
            "FROM receipts WHERE Date = ? LIMIT 200",
            conn, params=(date_str,),
        )
        # Low-stock snapshot (top 10 worst shortages globally)
        low_stock_df = pd.read_sql(
            """
            WITH stock AS (
              SELECT i.SAP_Code, i.Equipment_Description, i.Minimum_Qty,
                     COALESCE(SUM(r.Quantity),0)
                       - COALESCE((SELECT SUM(Quantity) FROM consumption c
                                    WHERE c.SAP_Code = i.SAP_Code),0)
                     AS Current_Stock
              FROM inventory i
              LEFT JOIN receipts r ON r.SAP_Code = i.SAP_Code
              GROUP BY i.SAP_Code
            )
            SELECT SAP_Code, Equipment_Description, Current_Stock, Minimum_Qty
            FROM stock
            WHERE Minimum_Qty IS NOT NULL AND Current_Stock < Minimum_Qty
            ORDER BY (Current_Stock - Minimum_Qty) ASC
            LIMIT 10
            """,
            conn,
        )
    finally:
        conn.close()

    # Aggregate to bullet-friendly facts
    consumed_n = len(consumption_df)
    consumed_qty = float(consumption_df["Quantity"].sum()) if consumed_n else 0.0
    received_n = len(receipts_df)
    received_qty = float(receipts_df["Quantity"].sum()) if received_n else 0.0
    by_site_cons = (
        consumption_df.groupby("Site_ID")["Quantity"].sum().to_dict()
        if consumed_n else {}
    )

    low_stock_lines = "\n".join(
        f"  - [{r.SAP_Code}] {r.Equipment_Description}: "
        f"stock {r.Current_Stock:g}, minimum {r.Minimum_Qty:g}"
        for r in low_stock_df.itertuples()
    ) or "  (none flagged)"

    site_lines = "\n".join(
        f"  - {site or 'HQ'}: {qty:g} units"
        for site, qty in by_site_cons.items()
    ) or "  (no site activity)"

    return (
        f"Date: {date_str}\n\n"
        f"Consumption rows today: {consumed_n}  (total qty: {consumed_qty:g})\n"
        f"Receipt rows today: {received_n}  (total qty: {received_qty:g})\n\n"
        f"Per-site consumption:\n{site_lines}\n\n"
        f"Top low-stock items (Current_Stock < Minimum_Qty):\n{low_stock_lines}"
    )


# ---------------------------------------------------------------------------
# PUBLIC API
# ---------------------------------------------------------------------------
def stream_eod_summary(
    report_date: Optional[datetime.date] = None,
) -> Iterator[str]:
    """
    Yields summary tokens. Compatible with `st.write_stream`.
    If Ollama is unreachable, yields a single explanatory chunk so the UI
    still shows something rather than failing silently.
    """
    if report_date is None:
        report_date = datetime.date.today()

    if not OLLAMA_AVAILABLE:
        yield (
            "🤖 Local AI is unavailable. Start `ollama serve` and confirm "
            "`llama3.1:8b` is pulled, then click Generate again."
        )
        return

    try:
        context = _build_context(report_date)
    except Exception as e:
        yield f"Could not build context for the summary: {e}"
        return

    system_prompt = (
        "You are a concise warehouse-operations analyst. You produce 3 to "
        "6 sentences summarizing the day for a department manager. Mention "
        "totals, notable site differences, and the most critical low-stock "
        "items by name. Do NOT invent numbers. Plain prose, no bullet lists, "
        "no markdown headings."
    )
    user_prompt = (
        f"Daily warehouse snapshot:\n\n{context}\n\n"
        f"Write the executive summary now."
    )

    try:
        for chunk in ollama_stream(
            MODEL_CHAT,
            prompt=user_prompt,
            system=system_prompt,
            temperature=0.3,
            num_predict=320,
        ):
            yield chunk
    except RuntimeError as e:
        yield f"\n\n⚠️ Summary stream interrupted: {e}"
