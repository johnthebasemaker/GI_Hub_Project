# 08 · Flag System

> The user-visible layer over all the ambiguity, uncertainty, and stock issues produced by files 01–07. Every non-clean row carries one or more flags; the UI decides how to render them.

**Depends on:** every earlier file (01–07).
**Feeds:** the Issue page UI (out of scope) and `09-output-format.md`.

---

## Purpose

Silent failure is the enemy. The operator must be shown, on a per-row basis:

- What was uncertain (OCR / matching)
- What was substituted (stock)
- What was defaulted (quantity)
- What was blocked (would-go-negative or zero stock)
- What was skipped (struck-through, empty, illegible)

Flags are annotations, not errors. A flagged batch can still be posted; the operator either accepts the flag or edits the row first.

---

## The Three Severities

| Marker | Severity | Semantics | Blocks posting? |
|---|---|---|---|
| `[?]` | Info | Something couldn't be read confidently; operator confirmation appreciated | No |
| ⚠️ | Warning | Something was auto-decided that the operator should review | No |
| 🚨 | Critical | The row cannot be posted as-is (would go negative, or no substitute) | Yes, for that row |

The batch can be posted with `[?]` and ⚠️ flags outstanding. Any 🚨 row is either fixed or removed before submission.

---

## Flag Catalogue

Every flag is a record `{code, severity, row_no?, message, context}`. `row_no` is null for batch-level flags.

### Info flags (`[?]`)

| Code | Origin file | Fires when |
|---|---|---|
| `INFO_NAME_ILLEGIBLE` | 03 | The `Name` field couldn't be read |
| `INFO_REMARKS_ILLEGIBLE` | 03 | The `Remarks` field couldn't be read |
| `INFO_TANK_NO_ILLEGIBLE` | 03 | The `Tank No.` field couldn't be read |
| `INFO_MATCH_UNCERTAIN` | 05 | Fuzzy match has `needs_review = true` (top candidates surfaced) |
| `INFO_DITTO_WITH_NO_SOURCE` | 03 | A ditto mark appeared but no previous row had a value in that column |

### Warning flags (⚠️)

| Code | Origin file | Fires when |
|---|---|---|
| `WARN_QTY_DEFAULTED` | 03 | Quantity was blank; defaulted to 1 |
| `WARN_ADDITIVE_QTY` | 03 | Quantity was written as `X+Y`; auto-summed |
| `WARN_SUBSTITUTED` | 06 | Zero-stock item was substituted per rule |
| `WARN_LOW_STOCK_CROSSED` | 07 | Deduction crossed `LOW_STOCK_THRESHOLD` |
| `WARN_MULTI_SUBSTITUTIONS_IN_BATCH` | 06 | ≥3 substitutions in one batch — worth reviewing |
| `WARN_QTY_APPROXIMATE` | 03 | Qty was written as `1 pack`, `1 set` etc.; extracted leading integer |

### Critical flags (🚨)

| Code | Origin file | Fires when |
|---|---|---|
| `CRIT_ZERO_STOCK_NO_SUBSTITUTE` | 06 | Match is zero and no substitution rule exists |
| `CRIT_WOULD_GO_NEGATIVE` | 07 | Simulated deduction would push stock below zero |
| `CRIT_DATE_UNPARSEABLE` | 02 | Header date couldn't be parsed (batch-level) |
| `CRIT_PRODUCT_NAME_MISSING` | 03/04 | No product name at all; row has nothing to post against |
| `CRIT_QTY_ZERO_OR_NEGATIVE` | 03 | Quantity value is ≤ 0 after parsing |

### Batch-level flags

Not tied to a single row:

| Code | Severity | Fires when |
|---|---|---|
| `BATCH_IMAGE_REJECTED` | 🚨 | File 01 rejected the image outright |
| `BATCH_HEADER_UNCERTAIN` | ⚠️ | Header parsed but with issues (e.g. banner not matched) |
| `BATCH_HIGH_STRUCK_THROUGH_COUNT` | ⚠️ | > 5 rows were struck through — possibly the wrong form |
| `BATCH_EMPTY` | 🚨 | Zero populated rows detected |
| `BATCH_MANY_ILLEGIBLE` | ⚠️ | > 30% of rows have any info-level flag |

---

## Flag Structure (on the wire)

```jsonc
{
  "code": "WARN_SUBSTITUTED",
  "severity": "warning",
  "row_no": 5,
  "message": "Zero-stock item substituted",
  "context": {
    "original_sap": "1141",
    "original_name": "Fire Extinguisher",
    "substituted_sap": "1072",
    "substituted_name": "Fire Extinguisher DCP",
    "reason": "Generic FE retired for DCP-specific"
  }
}
```

- `code` is stable and machine-readable.
- `message` is human-readable but generic; the UI can localise or elaborate.
- `context` carries whatever the UI needs to render the flag inline (name-before/after, stock numbers, candidate list, etc.).

---

## Per-Row Summary

Every row in the output payload carries a `flags` list. Rows with zero flags are clean.

```jsonc
{
  "row_no": 5,
  "date_iso": "2026-07-13",
  "received_by": "Prabhu",
  "product_name_raw": "Cable Tie",
  "resolved_sap": "1163",
  "qty": 1,
  "flags": [
    {"code": "WARN_SUBSTITUTED", "severity": "warning", ... }
  ]
}
```

---

## Batch Summary

At the top of the output payload:

```jsonc
{
  "summary": {
    "total_rows_seen": 30,
    "populated_rows": 22,
    "posted_ready_rows": 19,
    "struck_through_rows": 2,
    "empty_rows": 6,
    "flag_counts": {"info": 3, "warning": 4, "critical": 1},
    "blocking_row_count": 1,
    "batch_flags": [
      {"code": "BATCH_HIGH_STRUCK_THROUGH_COUNT", ... }
    ]
  }
}
```

The UI uses `blocking_row_count > 0` as the guard to disable the "Post" button.

---

## Rendering Guidance (advisory)

Not part of the spec — but this is how the operator has come to expect the flags visually:

- `[?]` — small grey pill next to the field, tooltip shows message
- ⚠️ — yellow left-border on the row, banner at top of row
- 🚨 — red left-border on the row, cannot be submitted until resolved
- Batch flags — a horizontal ribbon above the row list

---

## Pseudo-code

```python
INFO_FLAGS = {"NAME_ILLEGIBLE", "REMARKS_ILLEGIBLE", "TANK_NO_ILLEGIBLE",
              "MATCH_UNCERTAIN", "DITTO_WITH_NO_SOURCE"}
WARN_FLAGS = {"QTY_DEFAULTED", "ADDITIVE_QTY", "SUBSTITUTED",
              "LOW_STOCK_CROSSED", "MULTI_SUBSTITUTIONS_IN_BATCH", "QTY_APPROXIMATE"}
CRIT_FLAGS = {"ZERO_STOCK_NO_SUBSTITUTE", "WOULD_GO_NEGATIVE",
              "DATE_UNPARSEABLE", "PRODUCT_NAME_MISSING", "QTY_ZERO_OR_NEGATIVE"}

def make_flag(code_short: str, row_no: int | None, context: dict, message: str) -> dict:
    if code_short in INFO_FLAGS:
        code, sev = f"INFO_{code_short}", "info"
    elif code_short in WARN_FLAGS:
        code, sev = f"WARN_{code_short}", "warning"
    elif code_short in CRIT_FLAGS:
        code, sev = f"CRIT_{code_short}", "critical"
    else:
        code, sev = code_short, "info"   # unknown falls to info
    return {
        "code": code,
        "severity": sev,
        "row_no": row_no,
        "message": message,
        "context": context,
    }
```

Each earlier module can emit flags into the row's `flags` list. This module is really a specification of the *taxonomy*, not a runtime function; the actual emission happens where the condition is detected.

---

## Test Cases

| # | Scenario | Expected flags |
|---|---|---|
| 1 | Row parses cleanly | `flags = []` |
| 2 | Ditto in `Product Name`, no previous value | `INFO_DITTO_WITH_NO_SOURCE` |
| 3 | Match uncertain (fuzzy score < threshold) | `INFO_MATCH_UNCERTAIN` |
| 4 | Qty blank, product present | `WARN_QTY_DEFAULTED` |
| 5 | Qty `2+2` | `WARN_ADDITIVE_QTY` |
| 6 | Zero-stock item, has substitute | `WARN_SUBSTITUTED` |
| 7 | Zero-stock item, no substitute | `CRIT_ZERO_STOCK_NO_SUBSTITUTE` |
| 8 | Batch pushes stock negative | `CRIT_WOULD_GO_NEGATIVE` on offending row |
| 9 | Header date unreadable | Batch flag `CRIT_DATE_UNPARSEABLE`, no rows processed |
| 10 | 8 of 20 rows struck through | Batch flag `BATCH_HIGH_STRUCK_THROUGH_COUNT` |

---

## Claude Code Metadata

```yaml
module: flag-system
version: 1.0
depends_on: [image-preprocessing, header-extraction, row-parsing, fuzzy-matching, stock-validation, stock-simulation]
feeds: [output-format, ui]

severities:
  info:      { marker: '[?]', blocks_submission: false }
  warning:   { marker: '⚠️',  blocks_submission: false }
  critical:  { marker: '🚨',  blocks_submission: true  }

flag_codes:
  info:
    - INFO_NAME_ILLEGIBLE
    - INFO_REMARKS_ILLEGIBLE
    - INFO_TANK_NO_ILLEGIBLE
    - INFO_MATCH_UNCERTAIN
    - INFO_DITTO_WITH_NO_SOURCE
  warning:
    - WARN_QTY_DEFAULTED
    - WARN_ADDITIVE_QTY
    - WARN_SUBSTITUTED
    - WARN_LOW_STOCK_CROSSED
    - WARN_MULTI_SUBSTITUTIONS_IN_BATCH
    - WARN_QTY_APPROXIMATE
  critical:
    - CRIT_ZERO_STOCK_NO_SUBSTITUTE
    - CRIT_WOULD_GO_NEGATIVE
    - CRIT_DATE_UNPARSEABLE
    - CRIT_PRODUCT_NAME_MISSING
    - CRIT_QTY_ZERO_OR_NEGATIVE

batch_flags:
  - BATCH_IMAGE_REJECTED
  - BATCH_HEADER_UNCERTAIN
  - BATCH_HIGH_STRUCK_THROUGH_COUNT
  - BATCH_EMPTY
  - BATCH_MANY_ILLEGIBLE

flag_record_shape:
  code: string
  severity: "info | warning | critical"
  row_no: "int | null"
  message: string
  context: object

blocking_condition: "any row has severity=critical"
```
