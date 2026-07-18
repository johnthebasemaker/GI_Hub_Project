# 11 · Common OCR Corrections

> A living lookup table of observed OCR misreads and their corrections. Applied **before** fuzzy matching (file 05). Every entry has a real form as its source.

**Depends on:** nothing structural — this is data.
**Feeds:** `05-fuzzy-matching.md`.

---

## Purpose

Fuzzy matching gets `Leather gloves` from `Leather Gloves` easily. It cannot recover from `Leather Yloues` because `Yloues` shares no characters with `Gloves`. That's a *character-level* error the fuzzy layer isn't designed for.

This file is the pre-correction pass: known transformations from what OCR outputs to what the operator meant. It runs on the `product_name_raw` string alone, before file 05.

The list is empirical. Every entry came from a real handwritten form during the OCR-to-Excel workflow that preceded this spec. New entries are added conservatively — a one-off misread should not become a permanent correction.

---

## When to Apply

- **Only** to the `product_name_raw` field (not to `received_by`, `tank_no`, or `work_type` — those are stored verbatim by design).
- **Before** fuzzy matching. The output of this file feeds file 05's `query` input.
- **After** ditto resolution and whitespace normalisation.

---

## Correction Table

Grouped by the type of error to make review easier.

### Phonetic / handwriting typos

| Written | Should be | Notes |
|---|---|---|
| `Yloues`, `Ylones` | `Gloves` | `Y` misread from `G` handwriting |
| `Yvek`, `Tywek` | `Tyvek` | Brand name |
| `Yreen`, `Yrean` | `Green` | Same G/Y confusion |
| `Slasting` | `Blasting` | Cursive B → S |
| `Marky`, `Marker (Blach)` | `Marker (Black)` | Suffix noise |
| `Head Lamp` | `HANGLING LAMP` | Non-obvious alias (see below) |
| `Yellow holder` | `Blast nozzle Holder` | Colour-only reference |
| `Yland glass` | `Blasting glass` | |

### Missing detail (append canonical suffix)

These are cases where the operator wrote a category and, from context, only one canonical item exists:

| Written | Rewrite to | Because |
|---|---|---|
| `Mask` | `Dust Mask` | Only Dust Mask is stocked in that category unless remarks say otherwise |
| `Gloves` | *no rewrite* | Ambiguous — leave for fuzzy match / manual |
| `Tape` | *no rewrite* | Ambiguous |
| `Cable Tie` | *no rewrite* | Ambiguous, but the current single-SKU means fuzzy match resolves anyway |

### Size / spec noise

| Written | Rewrite to | Because |
|---|---|---|
| `10x10 Tarpaulin` | `Tarpaulin 10x10` | Word order |
| `Tarpaulin (10x10)` | `Tarpaulin 10x10` | Parens |
| `10m Measuring Tape` | `Measuring Tape 10m` | Word order |
| `2" Paint Brush` | `Paint Brush 2"` | Word order |
| `24V panel Board` | `24V Panel Board` | Case only (fuzzy handles this; listed for completeness) |

### Number/unit disambiguation

| Written | Rewrite to | Notes |
|---|---|---|
| `50m messuring tape` | `Measuring Tape 50m` | Common typo |
| `12" fan` | `Exhaust fan 12"` | Category prefix |
| `18" duct` | `18" DUCT` | Case (fuzzy handles) |
| `220V panel Board` | `Panel Board 220V` | Word order |

### Brand names

| Written | Rewrite to |
|---|---|
| `Vaultex earplug` | `Ear PLUG Vaultex` |
| `Stanley bag`, `Stanley Tool Bag` | `Tool Box STANLEY` |
| `Uvex goggle` | `UVEX MONO GOGGLE` |
| `KARAM helmet` | `KARAM SAFETY HELMET WHITE` |

### Do-NOT-correct (recorded for future maintainers)

Entries considered and rejected:

| Considered | Rejected because |
|---|---|
| `Bucket 10L` → `PLASTIC BUCKET 10 L` | Uncertain — sometimes means the 5L variant. Leave to fuzzy + manual. |
| `Growth Rod` → `Earthing Rod` | Only appeared once; not confident enough to promote. |
| `Duct 300 mm` → any specific duct | No matching 300mm SKU exists; would silently mis-post. |

---

## Structure

Corrections are stored as an ordered list of `{pattern, replacement, matching_mode}`:

```yaml
corrections:
  - pattern: "Yloues"
    replacement: "Gloves"
    mode: substring_case_insensitive

  - pattern: "Head Lamp"
    replacement: "HANGLING LAMP"
    mode: substring_case_insensitive

  - pattern: "^Mask$"           # anchored — only exact "Mask", not "Dust Mask"
    replacement: "Dust Mask"
    mode: regex_case_insensitive

  - pattern: "10x10 Tarpaulin"
    replacement: "Tarpaulin 10x10"
    mode: substring_case_insensitive
```

Modes:

- `substring_case_insensitive` — `str.replace()` style, everywhere the pattern appears
- `regex_case_insensitive` — for anchored corrections (`^` / `$`)

Corrections are applied **in order**. Later corrections may operate on the output of earlier ones. Keep the list small enough that this is auditable.

---

## Governance

Adding a correction:

1. The misread must have appeared on at least **two** independent forms, OR the resulting item is critical enough (safety-related) to justify one-off inclusion.
2. The replacement must be unambiguous — if you're picking between two candidate items, this file is not the right place.
3. Record the source form in a code comment beside the entry if possible.

Removing a correction:

1. If fuzzy matching (file 05) now handles it thanks to inventory-description changes, the correction becomes redundant. Remove after confirming with a test.
2. If a correction ever produces a wrong match on a real form, remove immediately.

---

## Pseudo-code

```python
import re

CORRECTIONS = [
    # (pattern, replacement, mode)
    ("Yloues", "Gloves", "substring_ci"),
    ("Ylones", "Gloves", "substring_ci"),
    ("Yvek",   "Tyvek",  "substring_ci"),
    ("Tywek",  "Tyvek",  "substring_ci"),
    ("Yreen",  "Green",  "substring_ci"),
    ("Slasting", "Blasting", "substring_ci"),
    ("Head Lamp", "HANGLING LAMP", "substring_ci"),
    ("Yellow holder", "Blast nozzle Holder", "substring_ci"),
    ("Yland glass", "Blasting glass", "substring_ci"),
    (r"^Mask$", "Dust Mask", "regex_ci"),
    ("10x10 Tarpaulin", "Tarpaulin 10x10", "substring_ci"),
    ("Tarpaulin (10x10)", "Tarpaulin 10x10", "substring_ci"),
    ("50m messuring tape", "Measuring Tape 50m", "substring_ci"),
    ("Vaultex earplug", "Ear PLUG Vaultex", "substring_ci"),
    ("Stanley bag", "Tool Box STANLEY", "substring_ci"),
    ("Stanley Tool Bag", "Tool Box STANLEY", "substring_ci"),
    ("Uvex goggle", "UVEX MONO GOGGLE", "substring_ci"),
    ("KARAM helmet", "KARAM SAFETY HELMET WHITE", "substring_ci"),
]

def apply_corrections(name: str) -> str:
    if not name:
        return name
    out = name
    for pat, rep, mode in CORRECTIONS:
        if mode == "substring_ci":
            out = re.sub(re.escape(pat), rep, out, flags=re.IGNORECASE)
        elif mode == "regex_ci":
            out = re.sub(pat, rep, out, flags=re.IGNORECASE)
    return " ".join(out.split())
```

---

## Test Cases

| Input | Expected output |
|---|---|
| `Leather Yloues` | `Leather Gloves` |
| `Yvek coverall` | `Tyvek coverall` |
| `Head Lamp 24V` | `HANGLING LAMP 24V` |
| `Mask` | `Dust Mask` |
| `Dust Mask` | `Dust Mask` (unchanged; `^Mask$` only anchors on exact match) |
| `Chemical Mask` | `Chemical Mask` (unchanged) |
| `Tarpaulin (10x10)` | `Tarpaulin 10x10` |
| `50m messuring tape` | `Measuring Tape 50m` |
| `Yreen coated gloves` | `Green coated gloves` |
| `Stanley Tool Bag` | `Tool Box STANLEY` |

---

## Claude Code Metadata

```yaml
module: common-corrections
version: 1.0
depends_on: []
feeds: [fuzzy-matching]

applies_to_fields:
  - product_name_raw

does_not_apply_to:
  - received_by
  - tank_no
  - work_type

application_stage: "after ditto resolution, before fuzzy matching"

correction_modes:
  substring_ci: "case-insensitive substring replacement"
  regex_ci:     "case-insensitive regex replacement, may include ^ / $ anchors"

application_order: sequential
                # later corrections operate on the output of earlier ones

governance:
  add_criteria:
    - "observed on ≥2 forms OR safety-critical"
    - "replacement is unambiguous"
    - "no chained substitution needed"
  remove_criteria:
    - "fuzzy match now handles the case without help"
    - "correction produced a wrong match on a real form"
```
