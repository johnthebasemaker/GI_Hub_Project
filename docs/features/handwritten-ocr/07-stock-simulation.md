# 07 · Stock Simulation

> Runs a chronological deduction across all resolved rows in a batch, catches items that would go negative *before* posting, and emits per-row stock warnings.

**Depends on:** `06-stock-validation.md`.
**Feeds:** `08-flag-system.md`.

---

## Purpose

A single form might consume 5 face shields when 8 are in stock — fine. But if three forms in the same batch each ask for 5, the second and third would push stock negative. This stage catches that scenario **before** any DB write.

It does not modify inventory. It only annotates each row with the projected stock impact.

---

## Contract

```
input: {
  rows: list<ResolvedRow>,          # from file 06, in chronological form order
  inventory_snapshot: dict<sap, {current_stock, ...}>,  # taken once, at batch start
}

output: {
  rows: list<AnnotatedRow>,         # same list, in same order, with stock fields
  low_stock_events: list<Event>,    # summary: which items crossed thresholds
  blocked_row_count: int,
}

where AnnotatedRow adds:
{
  stock_before: number,             # projected, at moment this row is applied
  stock_after: number,               # projected, if this row is applied
  would_go_negative: bool,
  crosses_low_stock: bool,           # crosses LOW_STOCK_THRESHOLD downward
  blocked: bool,                     # inherited from file 06 OR set to true here
  simulation_note: string | null,    # human-readable, e.g. "Would take stock 2 → -1"
}
```

The simulation is a **projection**, not a commit. If the user cancels the batch, no state changes.

---

## Algorithm

Iterate the batch in order (already sorted chronologically by file 10). Maintain a per-SAP running total, seeded from the snapshot.

```
running_stock[sap] = inventory_snapshot[sap].current_stock

for row in rows:
    if row.blocked:                              # zero-stock, no substitute
        continue
    sap = row.resolved_item.sap_code
    qty = row.qty
    before = running_stock.get(sap, 0)
    after  = before - qty
    row.stock_before = before
    row.stock_after  = after
    row.would_go_negative = after < 0
    row.crosses_low_stock = (before >= LOW_STOCK_THRESHOLD and after < LOW_STOCK_THRESHOLD)
    if row.would_go_negative:
        row.blocked = True
        row.simulation_note = f"Would take stock {before} → {after}"
    running_stock[sap] = after
```

- **Order matters.** File 10 sorts by `date_iso`, then by `source_form_id`, then by `source_row_no`. This gives deterministic, reproducible simulation results.
- Rows already blocked by file 06 (zero-stock, no substitute) are skipped in the deduction — they were never going to post anyway. But their SAP is still tracked so a *later* substitution to the same SAP starts from the correct running total.

---

## Thresholds

```
LOW_STOCK_THRESHOLD = 5
```

Rationale: 5 is the empirical inflection point across the CNCEC project where stock levels start prompting reorder attention. Anything below 5 is worth surfacing to the operator.

Changing this constant is a project decision; do not per-item override without adding a metadata field to inventory.

---

## Low-Stock Events

Alongside per-row annotations, the module emits a small, deduplicated list of "events" for the summary panel:

```jsonc
{
  "type": "would_go_negative",
  "sap_code": "1407",
  "item_name": "LIFTING BELT (3\" WIDTH) 3TON",
  "starting_stock": 2,
  "requested_across_batch": 5,
  "shortfall": 3,
  "affected_rows": [12, 18]
}
```

```jsonc
{
  "type": "crosses_low_stock",
  "sap_code": "1224",
  "item_name": "WARNING TAPE RED/WHITE",
  "starting_stock": 490,
  "final_stock_after_batch": 486
}
```

Only the "would_go_negative" event is 🚨-severity. `crosses_low_stock` is ⚠️.

---

## What This Stage Does Not Do

- **Does not modify the DB.** Even if all rows pass, the actual write happens after the user confirms the batch.
- **Does not re-fuzzy-match.** Any changes to `resolved_item` must happen in file 05/06.
- **Does not sort.** Ordering is file 10's responsibility.
- **Does not care about receipts.** If a receipt is posted mid-batch (rare), that's a separate consideration outside OCR scope.

---

## Interaction with the UI

The Issue page shows the annotated rows. Rows with `blocked = true` are displayed with the 🚨 marker and the option to:

1. Reduce the qty
2. Split across dates
3. Post a receipt for the item first (opens Receipt page)
4. Remove the row

The user cannot post the batch while any `blocked` row remains active.

---

## Pseudo-code

```python
from collections import defaultdict

LOW_STOCK_THRESHOLD = 5

def simulate_stock(rows: list[dict],
                   inventory_snapshot: dict[str, dict]) -> dict:
    running = {sap: float(item.get("current_stock") or 0)
               for sap, item in inventory_snapshot.items()}
    events_by_sap: dict[str, dict] = {}
    blocked = 0

    for row in rows:
        if row.get("blocked"):
            blocked += 1
            continue

        item = row["resolved_item"]
        sap  = str(item["sap_code"])
        qty  = float(row["qty"])
        before = running.get(sap, 0)
        after  = before - qty

        row["stock_before"] = before
        row["stock_after"]  = after
        row["would_go_negative"] = after < 0
        row["crosses_low_stock"] = (
            before >= LOW_STOCK_THRESHOLD and after < LOW_STOCK_THRESHOLD
        )

        if row["would_go_negative"]:
            row["blocked"] = True
            row["simulation_note"] = (
                f"Would take stock {before:g} → {after:g}"
            )
            blocked += 1
            ev = events_by_sap.setdefault(sap, {
                "type": "would_go_negative",
                "sap_code": sap,
                "item_name": item["description"],
                "starting_stock": inventory_snapshot[sap]["current_stock"],
                "requested_across_batch": 0,
                "shortfall": 0,
                "affected_rows": [],
            })
            ev["requested_across_batch"] += qty
            ev["shortfall"] = ev["requested_across_batch"] - ev["starting_stock"]
            ev["affected_rows"].append(row["source_row_no"])
        elif row["crosses_low_stock"] and sap not in events_by_sap:
            events_by_sap[sap] = {
                "type": "crosses_low_stock",
                "sap_code": sap,
                "item_name": item["description"],
                "starting_stock": inventory_snapshot[sap]["current_stock"],
                "final_stock_after_batch": after,
            }

        running[sap] = after

    return {
        "rows": rows,
        "low_stock_events": list(events_by_sap.values()),
        "blocked_row_count": blocked,
    }
```

---

## Test Cases

Assume inventory snapshot:

```
1141 (Fire Extinguisher): 4
1131 (Dust Mask):        1920
1407 (Lifting Belt 3T):  2
1165 (Leather gloves):   5000
```

| # | Batch (in order) | Expected result |
|---|---|---|
| 1 | 1× SAP 1131 (Dust Mask, qty 1) | 1131 → 1919, no events |
| 2 | 3× SAP 1141 (FE, qty 2 each) | Row 1 → 2, Row 2 → 0, Row 3 blocked (would go −2) |
| 3 | 5× SAP 1407 (Belt, qty 1 each) | Rows 1–2 → 1, 0. Rows 3–5 blocked; crosses_low_stock triggers on row 1 |
| 4 | 10× SAP 1165 (gloves, qty 1) | All pass, no events |
| 5 | SAP 1141 qty 4 | Passes (2 → 0). No going negative. Crosses low_stock on this row. |

---

## Claude Code Metadata

```yaml
module: stock-simulation
version: 1.0
depends_on: [stock-validation, batch-processing]
feeds: [flag-system]

constants:
  LOW_STOCK_THRESHOLD: 5

ordering_expectation: "rows arrive sorted by (date_iso, source_form_id, source_row_no)"

per_row_output_fields:
  - stock_before
  - stock_after
  - would_go_negative
  - crosses_low_stock
  - blocked
  - simulation_note

event_types:
  would_go_negative:
    severity: critical
    aggregation: per_sap
    fields_summed: [requested_across_batch]

  crosses_low_stock:
    severity: warning
    aggregation: per_sap
    emit_once_per_sap: true

side_effects: none    # simulation only, no DB writes
```
