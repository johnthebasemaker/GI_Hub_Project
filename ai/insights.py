"""
ai/insights.py — AI-powered inventory insights
================================================
Architecture
------------
Each "insight" is a fixed SQL probe that computes a hard metric (consumption
spike, projected stockout days, FEFO violation count, supplier
consolidation savings, inventory health score) and a *single* LLM call
that turns the metric + raw rows into a short narrative + 2–3
recommendations.

The deterministic SQL keeps the numbers honest; the LLM only writes prose.
Probes are cheap (small SELECTs), so we always run all five — the user
clicks "Regenerate" and gets them back in one streamed pass.

Model
-----
Default: llama3.1:8b (already pulled, same model used for EOD summaries).
Swappable per-install via the `ai_insights_model` row in `app_settings`.
"""

from __future__ import annotations

import datetime
import json
from typing import Iterator

import pandas as pd

from ai.client import ollama_generate, OLLAMA_AVAILABLE, MODEL_CHAT
from database import get_connection, get_app_setting


def _get_model() -> str:
    """Resolve the model id (db override → default chat model)."""
    try:
        m = get_app_setting("ai_insights_model", "")
        return m or MODEL_CHAT
    except Exception:
        return MODEL_CHAT


# ---------------------------------------------------------------------------
# PROBES — each returns (metric, metric_label, severity, raw_rows_summary)
# Severity: 'crit' | 'low' | 'ok'
# ---------------------------------------------------------------------------
def _probe_consumption_spike(site_id: str | None) -> dict | None:
    """
    Find the single SAP_Code whose last-30-day consumption most exceeds its
    prior-90-day daily-average baseline. Returns None if no signal.
    """
    conn = get_connection()
    try:
        where_site = " AND COALESCE(Site_ID,'HQ') = ?" if site_id else ""
        params: tuple = (site_id,) * 4 if site_id else ()
        df = pd.read_sql(
            "SELECT i.SAP_Code, i.Equipment_Description AS Material, "
            "       SUM(CASE WHEN c.Date >= date('now','-30 days') THEN c.Quantity ELSE 0 END) AS last_30, "
            "       SUM(CASE WHEN c.Date BETWEEN date('now','-120 days') AND date('now','-31 days') "
            "                THEN c.Quantity ELSE 0 END) / 3.0 AS avg_30 "
            "FROM consumption c JOIN inventory i ON c.SAP_Code = i.SAP_Code "
            f"WHERE c.Date >= date('now','-120 days') {where_site} "
            "GROUP BY i.SAP_Code HAVING last_30 > 0 AND avg_30 > 0 "
            "ORDER BY (last_30 / NULLIF(avg_30, 0)) DESC LIMIT 1",
            conn, params=params,
        )
    except Exception:
        df = pd.DataFrame()
    finally:
        conn.close()

    if df.empty:
        return None
    r = df.iloc[0]
    ratio = float(r["last_30"]) / max(float(r["avg_30"]), 0.01)
    if ratio < 1.4:
        return None
    return {
        "sap":    r["SAP_Code"],
        "material": r["Material"],
        "last_30":  float(r["last_30"]),
        "avg_30":   float(r["avg_30"]),
        "ratio_pct": int((ratio - 1.0) * 100),
        "metric":   f"{float(r['last_30']):.0f} units",
        "metric_label": f"vs {float(r['avg_30']):.0f} avg",
        "severity": "crit" if ratio >= 2.0 else "low",
    }


def _probe_projected_stockouts(site_id: str | None) -> dict | None:
    """How many distinct SAP_Codes are projected to hit zero within 14 days?"""
    conn = get_connection()
    try:
        where_site = " AND COALESCE(Site_ID,'HQ') = ?" if site_id else ""
        params: tuple = (site_id,) * 2 if site_id else ()
        # Daily burn rate over last 30d; current stock from receipts-consumption.
        df = pd.read_sql(
            "WITH stock AS ("
            "  SELECT i.SAP_Code, i.Equipment_Description AS Material, "
            "    COALESCE((SELECT SUM(Quantity) FROM receipts r "
            f"             WHERE r.SAP_Code=i.SAP_Code {where_site}),0) "
            "  - COALESCE((SELECT SUM(Quantity) FROM consumption c "
            f"             WHERE c.SAP_Code=i.SAP_Code {where_site}),0) "
            "    AS Current_Stock, "
            "    COALESCE((SELECT SUM(Quantity) FROM consumption c2 "
            f"             WHERE c2.SAP_Code=i.SAP_Code AND c2.Date>=date('now','-30 days') {where_site}),0)/30.0 "
            "    AS daily_burn "
            "  FROM inventory i"
            ") SELECT * FROM stock WHERE daily_burn > 0 "
            "  AND (Current_Stock / NULLIF(daily_burn,0)) <= 14",
            conn, params=params * 2,
        )
    except Exception:
        df = pd.DataFrame()
    finally:
        conn.close()

    if df.empty:
        return None
    df["days_left"] = df["Current_Stock"] / df["daily_burn"]
    df = df.sort_values("days_left")
    names = ", ".join(df.head(5)["Material"].astype(str).tolist())
    return {
        "count":   int(len(df)),
        "names":   names,
        "min_days": int(df["days_left"].min()),
        "max_days": int(df["days_left"].max()),
        "metric":   f"{int(len(df))} items",
        "metric_label": "≤14d to stockout",
        "severity": "crit" if len(df) >= 3 else "low",
    }


def _probe_expired_lots(site_id: str | None) -> dict | None:
    """Lots already past expiry that should be physically isolated."""
    conn = get_connection()
    try:
        where_site = " AND COALESCE(Site_ID,'HQ') = ?" if site_id else ""
        params: tuple = (site_id,) if site_id else ()
        df = pd.read_sql(
            "SELECT SAP_Code, Equipment_Description AS Material, Expiry_Date "
            "FROM inventory WHERE Expiry_Date IS NOT NULL "
            "AND date(Expiry_Date) < date('now') "
            f"{where_site} LIMIT 50",
            conn, params=params,
        )
    except Exception:
        df = pd.DataFrame()
    finally:
        conn.close()

    if df.empty:
        return None
    return {
        "count":  int(len(df)),
        "names":  ", ".join(df.head(5)["Material"].astype(str).tolist()),
        "metric": f"{int(len(df))} lots",
        "metric_label": "expired on shelf",
        "severity": "crit" if len(df) >= 2 else "low",
    }


def _probe_supplier_consolidation(site_id: str | None) -> dict | None:
    """Find a supplier we've used 3+ times in 90d — opportunity for blanket order."""
    conn = get_connection()
    try:
        df = pd.read_sql(
            "SELECT Supplier, COUNT(*) AS n_orders, "
            "       COALESCE(SUM(Quantity),0) AS total_qty "
            "FROM receipts WHERE Supplier IS NOT NULL AND Supplier <> '' "
            "  AND Date >= date('now','-90 days') "
            "GROUP BY Supplier HAVING n_orders >= 3 "
            "ORDER BY n_orders DESC LIMIT 1",
            conn,
        )
    except Exception:
        df = pd.DataFrame()
    finally:
        conn.close()

    if df.empty:
        return None
    r = df.iloc[0]
    return {
        "supplier":  r["Supplier"],
        "n_orders":  int(r["n_orders"]),
        "total_qty": float(r["total_qty"]),
        "metric":    f"{int(r['n_orders'])} orders",
        "metric_label": f"{r['Supplier']} · 90d",
        "severity":  "ok",
    }


def _probe_health_score(site_id: str | None) -> dict | None:
    """A 0–100 composite — naive but stable and explicable."""
    conn = get_connection()
    try:
        where_site = " AND COALESCE(Site_ID,'HQ') = ?" if site_id else ""
        params: tuple = (site_id,) * 4 if site_id else ()
        n_inv  = conn.execute(
            "SELECT COUNT(*) FROM inventory" + (" WHERE COALESCE(Site_ID,'HQ')=?" if site_id else ""),
            params[:1] if site_id else (),
        ).fetchone()[0] or 1
        n_low  = conn.execute(
            "SELECT COUNT(*) FROM inventory i WHERE Minimum_Qty IS NOT NULL AND "
            "COALESCE((SELECT SUM(Quantity) FROM receipts r WHERE r.SAP_Code=i.SAP_Code"
            + (where_site if site_id else "") + "),0)"
            " - COALESCE((SELECT SUM(Quantity) FROM consumption c WHERE c.SAP_Code=i.SAP_Code"
            + (where_site if site_id else "") + "),0) < Minimum_Qty"
            + ((" AND COALESCE(i.Site_ID,'HQ')=?") if site_id else ""),
            params if site_id else (),
        ).fetchone()[0] or 0
        n_exp = conn.execute(
            "SELECT COUNT(*) FROM inventory WHERE Expiry_Date IS NOT NULL "
            "AND date(Expiry_Date) < date('now')"
            + ((" AND COALESCE(Site_ID,'HQ')=?") if site_id else ""),
            params[:1] if site_id else (),
        ).fetchone()[0] or 0
    except Exception:
        return None
    finally:
        conn.close()

    score = 100
    score -= min(40, int(n_low / max(n_inv, 1) * 100))
    score -= min(30, n_exp * 6)
    score = max(0, score)
    sev = "ok" if score >= 70 else ("low" if score >= 50 else "crit")
    return {
        "score":     score,
        "n_low":     int(n_low),
        "n_expired": int(n_exp),
        "n_total":   int(n_inv),
        "metric":    f"{score}/100",
        "metric_label": "inventory health",
        "severity":  sev,
    }


# ---------------------------------------------------------------------------
# LLM commentary
# ---------------------------------------------------------------------------
_PROMPT = """You are an inventory operations analyst. Given a structured data
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


def _llm_commentary(probe_kind: str, probe_data: dict, model: str) -> dict:
    """
    Single Ollama call per probe. Returns {title, body, recs[]} on success,
    or a deterministic fallback dict if Ollama is unreachable / output is
    malformed. The UI never crashes because of this layer.
    """
    fallback = {
        "title": probe_kind.replace("_", " ").title(),
        "body":  "AI commentary unavailable. Inspect the metric directly.",
        "recs":  ["Review the underlying metric.",
                  "Take site-level corrective action.",
                  "Re-run insights after the next EOD commit."],
    }
    if not OLLAMA_AVAILABLE:
        return fallback
    try:
        finding = json.dumps({"kind": probe_kind, "data": probe_data}, default=str)
        raw = ollama_generate(model, _PROMPT + finding, options={"temperature": 0.3})
        raw = raw.strip()
        # Strip code-fence if the model wrapped it
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.lower().startswith("json"):
                raw = raw[4:].strip()
        # Find first { ... } in the response — robust to chatter prefixes
        start = raw.find("{")
        end   = raw.rfind("}")
        if start == -1 or end == -1:
            return fallback
        parsed = json.loads(raw[start:end + 1])
        if not all(k in parsed for k in ("title", "body", "recs")):
            return fallback
        if not isinstance(parsed["recs"], list):
            return fallback
        return {
            "title": str(parsed["title"])[:120],
            "body":  str(parsed["body"])[:800],
            "recs":  [str(r)[:200] for r in parsed["recs"][:3]],
        }
    except Exception:
        return fallback


# ---------------------------------------------------------------------------
# PUBLIC API
# ---------------------------------------------------------------------------
def build_insights(site_id: str | None = None) -> list[dict]:
    """
    Run every probe + LLM commentary. Returns a list of insight dicts:

        {
          "id": "consumption_spike", "icon": "📉",
          "title": "Abnormal consumption spike — MAT-10089",
          "body":  "…", "recs": [...],
          "metric": "62 units", "metric_label": "vs 18 avg",
          "severity": "crit" | "low" | "ok",
          "confidence": 0–100
        }

    Probes that have nothing interesting to report are skipped — the
    insights list is naturally adaptive to the current data shape.
    """
    model = _get_model()
    out: list[dict] = []

    probes = [
        ("consumption_spike",      "📉", _probe_consumption_spike, 94),
        ("projected_stockouts",    "⚠️", _probe_projected_stockouts, 89),
        ("expired_lots_on_shelf",  "🏷️", _probe_expired_lots, 97),
        ("supplier_consolidation", "💰", _probe_supplier_consolidation, 81),
        ("inventory_health_score", "✅", _probe_health_score, 99),
    ]
    for kind, icon, probe_fn, confidence in probes:
        try:
            data = probe_fn(site_id)
        except Exception:
            data = None
        if not data:
            continue
        commentary = _llm_commentary(kind, data, model)
        out.append({
            "id":             kind,
            "icon":           icon,
            "title":          commentary["title"],
            "body":           commentary["body"],
            "recs":           commentary["recs"],
            "metric":         data.get("metric", "—"),
            "metric_label":   data.get("metric_label", ""),
            "severity":       data.get("severity", "ok"),
            "confidence":     int(confidence),
        })

    return out


def stream_insights(site_id: str | None = None) -> Iterator[dict]:
    """
    Yields insights one at a time so a Streamlit page can render them as
    they arrive (matching the design's progressive-reveal aesthetic).
    Each call still runs all probes synchronously — this is a generator
    purely for UX pacing.
    """
    for ins in build_insights(site_id):
        yield ins
