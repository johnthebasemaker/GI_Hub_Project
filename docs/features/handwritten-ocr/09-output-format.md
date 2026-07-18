# 09 · Output Format

> The canonical shape of the payload that the OCR feature hands to the Issue page (and, downstream, to the consumption log). JSON is primary; TSV is retained as a legacy export.

**Depends on:** files 01–08.
**Feeds:** Issue page (React), consumption log (PostgreSQL), export endpoints.

---

## Purpose

Every earlier file emits partial data. This file specifies the merged shape that the frontend can render, that the backend can persist, and that (optionally) can be exported for legacy Excel workflows.

There is one payload per batch — one submission = one payload — regardless of how many forms were uploaded.

---

## Primary Format — JSON

```jsonc
{
  "batch_id": "batch_20260713_142309_abc",     // server-assigned
  "created_at": "2026-07-13T14:23:09Z",
  "submitted_by": "user@example.com",           // from session

  "summary": {                                  // from file 08
    "total_rows_seen": 30,
    "populated_rows": 22,
    "posted_ready_rows": 19,
    "struck_through_rows": 2,
    "empty_rows": 6,
    "flag_counts": { "info": 3, "warning": 4, "critical": 1 },
    "blocking_row_count": 1,
    "batch_flags": []
  },

  "sources": [                                  // one per uploaded form
    {
      "source_form_id": "form_abc123",
      "original_filename": "WhatsApp Image 2026-07-13 at 8.30.40 AM.jpeg",
      "processed_at": "2026-07-13T14:22:58Z",
      "header": {
        "date_iso": "2026-07-13",
        "date_raw": "13/07/26",
        "date_format_detected": "DD/MM/YY",
        "project_banner_ok": true,
        "form_title_ok": true
      },
      "row_count": 22
    }
  ],

  "rows": [                                     // the actual data
    {
      "id": "row_abc123_7",                     // {form_id}_{s_no}
      "date_iso": "2026-07-13",
      "source_form_id": "form_abc123",
      "source_row_no": 7,

      "received_by": "Akmal",
      "tank_no": "To site",
      "work_type": "Site Arrangement",

      "product_name_raw": "Dust Mask",
      "resolved_sap": "1131",
      "resolved_name": "DUST Mask",
      "resolved_material_code": "GI-7001234",
      "resolved_uom": "NOS",

      "qty": 1,
      "qty_defaulted": false,

      "substituted": false,
      "original_sap": null,
      "substitution_reason": null,

      "stock_before": 1920,
      "stock_after": 1919,
      "blocked": false,

      "ditto_flags": {
        "name": false,
        "tank_no": true,
        "product_name": false,
        "work_type": true
      },

      "flags": [],
      "raw_extraction": {                       // audit only
        "name_raw": "Akmal",
        "product_name_raw": "Dust Mask",
        "qty_raw": "1",
        "uom_raw": "NOS"
      }
    }
    // ... more rows
  ]
}
```

### Notes

- `id` is stable within a batch (form id + row no), so the UI can key-render without collisions if the same form is re-uploaded.
- `raw_extraction` is optional in the UI but must be present for the audit trail. Storage can move it to a side table.
- `resolved_uom` comes from the inventory row, not from the form (see file 04).
- `ditto_flags` is preserved so the UI can visually mark ditto-resolved cells (e.g. lighter font).

---

## Field-by-Field Reference

| Field | Type | Source | Notes |
|---|---|---|---|
| `id` | string | this file | `{source_form_id}_{source_row_no}` |
| `date_iso` | string (YYYY-MM-DD) | file 02 | Always present |
| `source_form_id` | string | file 10 | Server-assigned |
| `source_row_no` | int | file 03 | S.No. on the paper |
| `received_by` | string \| null | file 04 | Verbatim |
| `tank_no` | string \| null | file 04 | Verbatim |
| `work_type` | string \| null | file 04 | Verbatim |
| `product_name_raw` | string \| null | file 04 | What the operator wrote |
| `resolved_sap` | string \| null | file 05/06 | Post-substitution |
| `resolved_name` | string \| null | file 06 | From inventory |
| `resolved_material_code` | string \| null | file 06 | GI-* from inventory |
| `resolved_uom` | string \| null | file 06 | From inventory |
| `qty` | number | file 03 | Post-parsing |
| `qty_defaulted` | boolean | file 03 | |
| `substituted` | boolean | file 06 | |
| `original_sap` | string \| null | file 06 | Only if substituted |
| `substitution_reason` | string \| null | file 06 | |
| `stock_before` | number \| null | file 07 | Null if blocked before simulation |
| `stock_after` | number \| null | file 07 | |
| `blocked` | boolean | file 06/07 | |
| `ditto_flags` | object | file 03 | Per-column bools |
| `flags` | array | file 08 | Zero or more |
| `raw_extraction` | object | file 03 | Audit |

---

## TSV Legacy Export

Excel workflows still exist; some staff paste into the legacy `Consumption Log` sheet. A TSV export mirrors the columns of that sheet:

```
Date  SAP Code  Material Code  Description  UOM  Qty  ...  Remarks  Tank No  ...  Received by
```

Columns match the Excel Consumption Log positional layout. Blank columns exist because the log has spacer columns; do not remove them.

**Rule:** only rows with `blocked = false` and no `severity = "critical"` flags are exported to TSV. Blocked rows must be resolved in the UI first.

**Example TSV line:**

```
2026-07-13			DUST Mask		1			Site Arrangement	To site					Akmal	
```

Only fields with reliable values from the OCR pipeline are populated in TSV. The columns `SAP Code`, `Material Code`, and `UOM` are left blank; the Excel VBA fills them via the inventory lookup on paste. This is the historically-used convention and is preserved for compatibility.

---

## What Goes to the DB

For the primary write path (PostgreSQL), the target table is (schema is project-choice, this is illustrative):

```sql
CREATE TABLE consumption_log (
  id                       uuid PRIMARY KEY,
  batch_id                 text NOT NULL,
  source_form_id           text NOT NULL,
  source_row_no            int  NOT NULL,
  date_of_consumption      date NOT NULL,
  sap_code                 text NOT NULL REFERENCES inventory(sap_code),
  qty                      numeric NOT NULL,
  received_by              text,
  tank_no                  text,
  work_type                text,
  substituted_from_sap     text,
  substitution_reason      text,
  qty_defaulted            boolean DEFAULT false,
  posted_at                timestamptz DEFAULT now(),
  posted_by                text NOT NULL,
  raw_extraction           jsonb
);
```

Only rows the operator has accepted (no critical flags outstanding) are written. The `flags` array is stored **separately** (e.g. an audit table) — the operator's acceptance implicitly closes the flags for that row.

---

## Immutability

Once posted, a row cannot be edited — it can only be reversed by a compensating entry. This matches Excel VBA behaviour (append-only log) and is critical for stock arithmetic.

If the user needs to undo, the Issue page offers a "Reverse" action that creates a new row with negative qty and a `reversed_from` reference. This is not part of OCR scope but the schema anticipates it.

---

## Streaming vs Batch Return

The full payload is returned as a single JSON in the success response of the OCR endpoint. It is not streamed row-by-row. Batches are typically small (< 200 rows across multiple forms); a single response is simplest and matches the "review then post" UX.

---

## Test Cases

| # | Scenario | Expected payload characteristic |
|---|---|---|
| 1 | 1 form, 5 clean rows | 5 rows, `summary.posted_ready_rows = 5`, no flags |
| 2 | 1 form, 2 rows have substitutions | 2 rows have `substituted = true` + `WARN_SUBSTITUTED` flag |
| 3 | Multi-form batch, 3 forms, dates 13/07, 14/07, 14/07 | 3 entries in `sources`, rows sorted chronologically |
| 4 | Row would go negative | Row has `blocked = true`, `CRIT_WOULD_GO_NEGATIVE`, `summary.blocking_row_count > 0` |
| 5 | Image rejected | `sources` empty for that upload, `summary.batch_flags` includes `BATCH_IMAGE_REJECTED` |
| 6 | TSV export requested, blocking rows present | Blocked rows omitted from TSV, exported count < row count |

---

## Claude Code Metadata

```yaml
module: output-format
version: 1.0
depends_on: [image-preprocessing, header-extraction, row-parsing, column-mapping,
             fuzzy-matching, stock-validation, stock-simulation, flag-system]
feeds: [ui, persistence, export]

payload_shape:
  root_keys: [batch_id, created_at, submitted_by, summary, sources, rows]
  row_id_format: "{source_form_id}_{source_row_no}"

formats_supported:
  primary: json
  legacy_export: tsv

tsv_export_rules:
  include_only_when: "row.blocked == false AND no critical flags"
  columns_in_order:
    - date_iso
    - "" # SAP code — VBA fills
    - "" # Material Code — VBA fills
    - product_name_or_resolved
    - "" # UOM — VBA fills
    - qty
    - "" # spacer
    - "" # spacer
    - "" # spacer
    - work_type
    - tank_no
    - "" # spacer x5
    - "" 
    - ""
    - ""
    - ""
    - received_by
    - "" # spacer

db_write_rules:
  target_table: consumption_log
  append_only: true
  flags_stored_separately: true
  raw_extraction_stored_as_jsonb: true

reversal:
  supported: true
  mechanism: "compensating row with negative qty and reversed_from reference"
  out_of_scope_for_ocr: true
```
