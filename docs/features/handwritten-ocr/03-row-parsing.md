# 03 · Row Parsing

> Walks the 30-row form table, extracts each populated row as raw strings, and applies the three form-specific conventions: **ditto marks**, **struck-through rows**, and **blank quantities**.

**Depends on:** `01-image-preprocessing.md`, `02-header-extraction.md`.
**Feeds:** `04-column-mapping.md`.

---

## Purpose

Between the header (top) and the signatures block (bottom) there is a 30-row grid with these printed columns:

```
| S.No. | Name | Tank No.# | Product Name | UOM | QTY | Remarks |
```

The operator writes freehand in each row. Row parsing produces a list of raw dictionaries — one per **populated** row — with the ditto marks resolved. It does not yet map to logical output fields (that's file 04) and does not yet fuzzy-match product names (that's file 05).

---

## Contract

```
input: {
  processed_image: bytes,
  header: HeaderExtract,           # from file 02
}

output: {
  rows: list<RawRow>,
  row_count: int,
  struck_through_count: int,
  blank_count: int,
  needs_review: bool,
  issues: list<{row_no: int, issue: string}>,
}

where RawRow = {
  s_no: int,                       # 1..30, as printed
  name: string | null,
  tank_no: string | null,
  product_name: string | null,
  uom: string | null,
  qty: number | null,
  remarks: string | null,
  ditto_flags: {
    name: bool,
    tank_no: bool,
    product_name: bool,
    uom: bool,
    remarks: bool,
  },
  qty_defaulted: bool,             # true if qty was blank and set to 1
  raw_extraction: object,          # what the OCR actually saw, for audit
}
```

Every row keeps `raw_extraction` untouched. That is the source of truth if anything downstream disputes what was written.

---

## Rules

### R1 — Which rows to keep

A row is kept only if **at least one** of these is populated:

- `product_name`
- `qty`

An empty row (just the printed `S.No.`) is discarded. Do not emit 30 rows padded with `null`s.

### R2 — Ditto marks

Any of these characters, alone in a cell, means "same as the row directly above":

- `"` (straight double quote)
- `〃` (Japanese unicode ditto — sometimes seen)
- `,,` (two commas — occasionally used)
- `''` (two single quotes)
- A backtick `` ` `` alone

When any of these five glyphs is the entire content of a cell, replace the cell's value with the value from the same column of the previous **non-blank** row, and set the corresponding `ditto_flags.<column>` to true.

**Ditto marks do NOT propagate:** if row 5 has `"` in the `Product Name` column and row 4 also had `"` in the same column, both resolve to the value from the last non-ditto row. This is important for long runs of `"` (10+ rows in a column is common on real forms).

**Ditto marks do NOT cross non-blank values.** If rows 2 and 3 are `"` and row 4 has a real value, the ditto resolution for rows 2 and 3 is still based on row 1, not row 4.

**S.No. never dittos.** The `S.No.` cell is always a printed number; if OCR reads a ditto there, treat it as OCR noise and use the printed row index.

### R3 — Struck-through rows

A struck-through row is one where a single horizontal line has been drawn across it (typically through the middle). It means the operator cancelled that entry.

Detection strategies (project-choice):

- Look for a horizontal line inside the row's bounding box that is longer than 60% of the row width.
- Alternatively (LLM vision): the model reports "row appears to be crossed out".

**Struck-through rows:**

- Are extracted anyway (so we can report them in the summary — file 08).
- Are **not** emitted in the `rows` list.
- Increment `struck_through_count`.

If only *some fields* in a row are struck through (rare), treat the whole row as struck through. Partial strikes are ambiguous and unsafe.

### R4 — Blank quantities

Real forms occasionally have a product name but no quantity, especially for a long run of PPE items where "1 each" is implicit.

**Default:** if `product_name` is present and `qty` is missing, set `qty = 1` and `qty_defaulted = true`. File 08 will flag this so the user can review before posting.

**Never default:** if a row has a quantity but no product name, do not invent one. Emit it with `product_name = null` and let file 04 handle the missing-field flag.

### R5 — Quantity parsing

Quantities are usually simple integers (1, 2, 4, 50). Two other forms occur:

- **Additive**: `2+2`, `6+10`, `2+5`. Sum them. `2+2 → 4`. Preserve the raw string in `raw_extraction`.
- **Approximate**: `1 set`, `1 pack`. Extract the leading integer; discard the unit word.

If a quantity is 0 or negative, treat as needs_review. Zero-quantity rows are almost always OCR errors, not real data.

### R6 — Free-text fields

`Name`, `Tank No.#`, and `Remarks` are stored verbatim (post-whitespace-cleanup). Do not:

- Correct spelling
- Expand abbreviations (`HK` stays as `HK`, not `House Keeping`)
- Normalise casing

These fields are display-only downstream; the DB stores them as the operator wrote them.

### R7 — Illegible cells

If OCR confidence is low for a cell:

- Non-critical fields (`name`, `remarks`) — store the best guess in `raw_extraction.<field>_raw`, set the parsed value to null. File 08 will flag it as `[?]`.
- Critical fields (`product_name`) — force `needs_review = true` on the whole row; do NOT invent.
- `qty` — treat like a blank; may default to 1 with `qty_defaulted = true` if `product_name` is present.

---

## Worked Examples

Excerpt from a real form (13/07/26, Paper B):

```
| S.No | Name        | Tank | Product         | UOM  | QTY | Remarks         |
|------|-------------|------|-----------------|------|-----|-----------------|
| 1    | Jeena       | To site | Leather Gloves | pair | 1   | Site Arrangement|
| 2    | Yaseen      | "    | "               | "    | 1   | "               |
| 3    | Mydeen      | "    | "               | "    | 1   | "               |
| ...  | ...         | ...  | ...             | ...  | ... | ...             |
| 7    | Akmal       | "    | Dust Mask       | NOS  | 1   | "               |
```

After parsing:

```jsonc
[
  { "s_no": 1, "name": "Jeena", "tank_no": "To site",
    "product_name": "Leather Gloves", "uom": "pair", "qty": 1,
    "remarks": "Site Arrangement",
    "ditto_flags": { "name":false, "tank_no":false, "product_name":false, "uom":false, "remarks":false },
    "qty_defaulted": false },
  { "s_no": 2, "name": "Yaseen", "tank_no": "To site",
    "product_name": "Leather Gloves", "uom": "pair", "qty": 1,
    "remarks": "Site Arrangement",
    "ditto_flags": { "name":false, "tank_no":true, "product_name":true, "uom":true, "remarks":true },
    "qty_defaulted": false },
  // ... rows 3..6 similarly ditto ...
  { "s_no": 7, "name": "Akmal", "tank_no": "To site",
    "product_name": "Dust Mask", "uom": "NOS", "qty": 1,
    "remarks": "Site Arrangement",
    "ditto_flags": { "name":false, "tank_no":true, "product_name":false, "uom":false, "remarks":true },
    "qty_defaulted": false }
]
```

Notes:

- Rows 2–6 all resolve to `Leather Gloves` because row 1 is the last non-ditto value in that column.
- Row 7 has its own product name; the ditto flag for `product_name` is false.
- The `Tank No.` `To site` propagates through all rows via ditto.

---

## Pseudo-code

```python
DITTO_GLYPHS = {'"', '〃', ",,", "''", "`"}

def parse_rows(raw_rows: list[dict], date_iso: str) -> dict:
    """
    raw_rows is what the OCR stage produced — one entry per S.No line,
    with raw string values (or None) for each column.
    """
    parsed: list[dict] = []
    struck = 0
    issues: list[dict] = []
    last_nonditto = {
        "name": None, "tank_no": None, "product_name": None,
        "uom": None, "remarks": None,
    }

    for raw in raw_rows:
        s_no = raw["s_no"]

        # R3 — Skip struck-through
        if raw.get("_struck_through"):
            struck += 1
            continue

        row = {"s_no": s_no, "ditto_flags": {}, "qty_defaulted": False,
               "raw_extraction": raw.copy()}

        # R2 — Resolve ditto per column
        for col in ("name", "tank_no", "product_name", "uom", "remarks"):
            val = _clean(raw.get(col))
            if _is_ditto(val):
                row[col] = last_nonditto[col]
                row["ditto_flags"][col] = True
            else:
                row[col] = val
                row["ditto_flags"][col] = False
                if val is not None:
                    last_nonditto[col] = val

        # R5 — Quantity
        row["qty"] = _parse_qty(raw.get("qty"))

        # R1 — Skip empty rows
        if row["product_name"] is None and row["qty"] is None:
            continue

        # R4 — Default blank qty
        if row["qty"] is None and row["product_name"] is not None:
            row["qty"] = 1
            row["qty_defaulted"] = True

        # R7 — Illegible product name
        if row.get("_product_name_illegible"):
            issues.append({"row_no": s_no, "issue": "product_name_illegible"})
            row["product_name"] = None

        parsed.append(row)

    return {
        "rows": parsed,
        "row_count": len(parsed),
        "struck_through_count": struck,
        "blank_count": 30 - len(parsed) - struck,
        "needs_review": bool(issues),
        "issues": issues,
    }

def _clean(s):
    if s is None: return None
    s = " ".join(str(s).split())
    return s or None

def _is_ditto(s):
    return s is not None and s in DITTO_GLYPHS

def _parse_qty(raw):
    if raw is None or raw == "": return None
    s = str(raw).strip()
    if "+" in s:                               # additive
        parts = [p.strip() for p in s.split("+")]
        try:
            return sum(int(p) for p in parts if p.isdigit())
        except ValueError:
            return None
    # extract leading integer
    import re
    m = re.match(r"^(\d+)", s)
    if not m: return None
    n = int(m.group(1))
    return n if n > 0 else None
```

---

## Test Cases

| # | Scenario | Expected behaviour |
|---|---|---|
| 1 | 30 rows, only 5 populated | `row_count = 5` |
| 2 | 4 rows populated, 3 use `"` for Product Name | All 4 resolve to the first row's product |
| 3 | Row 5 is struck through | Excluded from `rows`; `struck_through_count = 1` |
| 4 | Row 3 has `Product Name` but no `QTY` | Emitted with `qty = 1`, `qty_defaulted = true` |
| 5 | Row 3 has `QTY = 2+5` | Emitted with `qty = 7`, raw preserved |
| 6 | Row 3 has `QTY = 1 set` | Emitted with `qty = 1` |
| 7 | Row 3 has `QTY = 0` | Emitted with `qty = null`, needs_review |
| 8 | Row 3 has `Product Name = ????` (illegible) | `product_name = null`, needs_review |
| 9 | Row 3 has ditto in `Product Name`, but no row above had a product | `product_name = null`, needs_review |
| 10 | Rows 1–5 all have `"` in `Tank No.` | All resolve to null (no previous value); needs_review on those rows |

---

## Claude Code Metadata

```yaml
module: row-parsing
version: 1.0
depends_on: [image-preprocessing, header-extraction]
feeds: [column-mapping]

form_grid:
  total_rows: 30
  columns: [S.No., Name, Tank No.#, Product Name, UOM, QTY, Remarks]

ditto_glyphs:
  - '"'
  - '〃'
  - ',,'
  - "''"
  - '`'

qty_parsing:
  additive_form_regex: '^\d+(\+\d+)+$'          # e.g. 2+2, 6+10+3
  leading_integer_regex: '^(\d+)'
  zero_or_negative: reject

blank_qty_default:
  when: "product_name is not null"
  value: 1
  flag: qty_defaulted

row_keep_condition: "product_name != null OR qty != null"

struck_through_detection:
  strategies: [horizontal_line_detection, vision_model_report]
  applies_to: entire_row_only
  emit_in_output: false
  count_in_summary: true

illegible_handling:
  critical_fields: [product_name]
  non_critical_fields: [name, remarks, tank_no, uom]
  action_on_critical: needs_review
  action_on_non_critical: null_value_plus_flag
```
