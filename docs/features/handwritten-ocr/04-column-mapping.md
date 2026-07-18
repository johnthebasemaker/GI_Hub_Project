# 04 · Column Mapping

> Translates the 7 handwritten form columns into the 8 logical output fields used by the Issue page and the consumption log. This is a pure renaming/routing step — no lookups yet.

**Depends on:** `03-row-parsing.md`.
**Feeds:** `05-fuzzy-matching.md`.

---

## Purpose

The form and the storage model use different vocabulary. This file establishes the vocabulary translation, once, in one place, so no downstream file has to guess.

---

## The Mapping

| # | Form column | → | Logical output field | Notes |
|---|---|---|---|---|
| 1 | `S.No.` | → | *(discarded)* | Only used for row ordering during parsing |
| 2 | `Name` | → | `received_by` | Verbatim, no lookup |
| 3 | `Tank No.#` | → | `tank_no` | Verbatim; `HK`, `Yard`, `Others` etc. preserved literally |
| 4 | `Product Name` | → | `product_name_raw` | Handed to file 05 for fuzzy resolution |
| 5 | `UOM` | → | *(discarded)* | See "UOM is discarded" below |
| 6 | `QTY` | → | `qty` | Post file-03 numeric value |
| 7 | `Remarks` | → | `work_type` | Verbatim |

Additional fields added at this stage (not on the form):

| Added field | Source |
|---|---|
| `date_iso` | From the header (file 02) |
| `source_form_id` | Set by the batch layer (file 10) |
| `source_row_no` | The `S.No.` from the form, kept for traceability |

**No new inferences.** This stage does not fuzzy-match, does not check stock, does not deduplicate. It only routes and renames.

---

## UOM is discarded

The handwritten UOM (`Pair`, `NOS`, `Pcs`, `Roll`, `Pack`) is not stored. Reason:

- The inventory record is the authority on UOM.
- Handwriting for UOM is very ambiguous (`h`, `n`, `II`, `\\`, `Nos`, `NOS` all appear for "each").
- Downstream systems (SAP, stock reports) always want the canonical UOM from the inventory row.

The UOM is **used** upstream — file 05 may consult it as a weak hint when disambiguating (e.g. `Roll` suggests tape or wire, not gloves) — but it is not carried into the output payload.

If a UI mockup shows a UOM column on the Issue page, populate it from the resolved inventory row, not from the form.

---

## `work_type` — from Remarks

The Remarks column varies wildly across forms. Observed contents:

- Job phase: `Blasting`, `Painting`, `Scaffolding`, `Site Arrangement`
- Location: `To site`, `In yard`, `Shed`, `Messhall`
- Notes: `Extra`, `(1 Bd)`, `(Big)`, `(Small)`, `(Blue)`, `(2m)`

**Storage:** keep the raw string. Do not try to classify.

**Downstream use:** the Issue page shows this as a free-text field. Reporting/analytics can regex over it later; storing it as free text preserves options.

**Do not** truncate remarks (e.g. `(Big)` next to `Blasting glass` may be the only signal file 05 needs to disambiguate).

---

## `received_by`, `tank_no`

Kept verbatim. The Issue page shows these; there is no attempt to normalise `Rajendra` vs `Rajendhra` vs `Rajendran`, nor to look up an employee ID. If future work adds an employee directory, the mapping happens there, not here.

---

## `product_name_raw`

This is the string that file 05 will attempt to resolve. It is the *cleaned* form-column value (ditto already resolved by file 03), not the raw OCR output.

If `product_name_raw` is `null` (illegible or ditto-with-no-source), the row is emitted with `product_name_raw = null` and `needs_review = true`; file 05 short-circuits and does no matching.

---

## Output Shape (one row)

```jsonc
{
  "date_iso": "2026-07-13",
  "source_form_id": "form_abc123",
  "source_row_no": 7,
  "received_by": "Akmal",
  "tank_no": "To site",
  "product_name_raw": "Dust Mask",
  "qty": 1,
  "qty_defaulted": false,
  "work_type": "Site Arrangement",
  "ditto_flags": {
    "name": false,
    "tank_no": true,
    "product_name": false,
    "work_type": true
  }
}
```

Everything file 05 needs (`product_name_raw`, `work_type` as a hint) is present. Everything the Issue page needs downstream (`received_by`, `tank_no`, `date_iso`, provenance) is present. Nothing else.

---

## Pseudo-code

```python
def map_columns(parsed_row: dict, header: dict, source_form_id: str) -> dict:
    return {
        "date_iso":         header["date_iso"],
        "source_form_id":   source_form_id,
        "source_row_no":    parsed_row["s_no"],
        "received_by":      parsed_row["name"],
        "tank_no":          parsed_row["tank_no"],
        "product_name_raw": parsed_row["product_name"],
        "qty":              parsed_row["qty"],
        "qty_defaulted":    parsed_row["qty_defaulted"],
        "work_type":        parsed_row["remarks"],
        "ditto_flags": {
            "name":         parsed_row["ditto_flags"]["name"],
            "tank_no":      parsed_row["ditto_flags"]["tank_no"],
            "product_name": parsed_row["ditto_flags"]["product_name"],
            "work_type":    parsed_row["ditto_flags"]["remarks"],
        },
    }
```

That is the entire mapping module. Any additional field-manipulation belongs in earlier (file 03) or later (files 05+) stages.

---

## Test Cases

| # | Input row (from file 03) | Expected key changes |
|---|---|---|
| 1 | `remarks = "Blasting"` | `work_type = "Blasting"` |
| 2 | `remarks = None`, `product_name = "Fire Extinguisher"` | `work_type = null`; row still valid |
| 3 | `uom = "NOS"` | Not present in output |
| 4 | `name = "Rajendhra"` | `received_by = "Rajendhra"` (verbatim) |
| 5 | `tank_no = "HK"` | `tank_no = "HK"` (not expanded) |
| 6 | `qty = 4`, `qty_defaulted = false` | Copied through |
| 7 | Ditto flag on `remarks = true` | Ditto flag surfaced as `work_type: true` |

---

## Claude Code Metadata

```yaml
module: column-mapping
version: 1.0
depends_on: [row-parsing, header-extraction]
feeds: [fuzzy-matching]

form_to_logical:
  S.No.:        source_row_no
  Name:         received_by
  "Tank No.#":  tank_no
  "Product Name": product_name_raw
  UOM:          "*discarded*"
  QTY:          qty
  Remarks:      work_type

added_fields:
  - date_iso            # from file 02
  - source_form_id      # from file 10
  - qty_defaulted       # from file 03
  - ditto_flags         # from file 03

verbatim_fields:
  - received_by
  - tank_no
  - work_type
  - product_name_raw

no_transformations_at_this_stage:
  - fuzzy_matching
  - stock_lookup
  - deduplication
  - name_normalisation
  - uom_persistence
```
