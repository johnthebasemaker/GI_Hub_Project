# Handwritten Form OCR — Specification

> **For Claude Code.** This directory contains a framework-agnostic specification for extracting structured data from handwritten site consumption forms and validating it against the inventory. Read the whole thing before writing code; the rules in later files depend on definitions in earlier ones.

---

## Context

This spec was distilled from a live workflow that processed 20+ handwritten "Daily Consumption — Safety & Production Consumables" forms for the MPC3P1-CNCEC project. The origin was Excel + VBA; the same logic now needs to power the **SK Portal → Issue page** in GI_Hub_Project.

Nothing here is Excel-specific. Where the source mentioned Excel columns, this spec calls them **logical fields** so PostgreSQL / FastAPI / React can implement them naturally.

---

## Reading Order

The files are numbered because later stages depend on decisions made in earlier ones. **Read in order the first time.** After that, use the index below.

| # | File | What it defines |
|---|---|---|
| 00 | [README.md](./README.md) | This file — overview, glossary, workflow map |
| 01 | [01-image-preprocessing.md](./01-image-preprocessing.md) | Accepting the image; orientation, cropping, quality checks |
| 02 | [02-header-extraction.md](./02-header-extraction.md) | Reading the **Date** in the top-right (three formats supported) |
| 03 | [03-row-parsing.md](./03-row-parsing.md) | Iterating rows; handling ditto marks, blanks, strike-throughs |
| 04 | [04-column-mapping.md](./04-column-mapping.md) | Mapping the 7 form columns to the 8 logical output fields |
| 05 | [05-fuzzy-matching.md](./05-fuzzy-matching.md) | Resolving a handwritten product name to an inventory SAP code |
| 06 | [06-stock-validation.md](./06-stock-validation.md) | The "current stock > 0" filter; substitution when the exact match is out |
| 07 | [07-stock-simulation.md](./07-stock-simulation.md) | Running-total deduction across a batch; negative-stock alerts |
| 08 | [08-flag-system.md](./08-flag-system.md) | The `[?]`, ⚠️, 🚨 markers; when each fires; how to surface them |
| 09 | [09-output-format.md](./09-output-format.md) | The canonical output shape (JSON primary, TSV legacy) |
| 10 | [10-batch-processing.md](./10-batch-processing.md) | Multiple forms in one submission; date grouping; ordering |
| 11 | [11-common-corrections.md](./11-common-corrections.md) | Observed OCR-error → correct-value mappings from real forms |
| ⌘ | [APPENDIX-claude-code-metadata.yaml](./APPENDIX-claude-code-metadata.yaml) | Machine-parseable version of all deterministic rules |

---

## End-to-End Workflow

```
                          ┌──────────────────────────────┐
    User uploads image ─► │  01 Preprocess               │
                          │     - orient, normalise       │
                          │     - reject if unreadable    │
                          └──────────────┬───────────────┘
                                         │
                          ┌──────────────▼───────────────┐
                          │  02 Extract header            │
                          │     - project name (validate) │
                          │     - date (three formats)    │
                          └──────────────┬───────────────┘
                                         │
                          ┌──────────────▼───────────────┐
                          │  03 Parse rows               │
                          │     - S.No → row index        │
                          │     - resolve ditto marks     │
                          │     - drop struck-through     │
                          │     - default blank qty → 1   │
                          └──────────────┬───────────────┘
                                         │
                          ┌──────────────▼───────────────┐
                          │  04 Column map                │
                          │     Name → received_by        │
                          │     Tank No → tank_no         │
                          │     Product Name → (raw)      │
                          │     UOM → discard (see spec)  │
                          │     Qty → qty                 │
                          │     Remarks → work_type       │
                          └──────────────┬───────────────┘
                                         │
                          ┌──────────────▼───────────────┐
                          │  05 Fuzzy-match product name  │
                          │     against inventory table   │
                          └──────────────┬───────────────┘
                                         │
                          ┌──────────────▼───────────────┐
                          │  06 Stock validation          │
                          │     - drop zero-stock hits    │
                          │     - substitute alternatives │
                          └──────────────┬───────────────┘
                                         │
                          ┌──────────────▼───────────────┐
                          │  07 Simulate deduction        │
                          │     across whole batch,       │
                          │     in chronological order    │
                          └──────────────┬───────────────┘
                                         │
                          ┌──────────────▼───────────────┐
                          │  08 Flag rows                 │
                          │     [?], ⚠️, 🚨               │
                          └──────────────┬───────────────┘
                                         │
                          ┌──────────────▼───────────────┐
                          │  09 Emit output               │
                          │     JSON payload + summary    │
                          └──────────────┬───────────────┘
                                         │
                                    to Issue page
```

Every step is **idempotent**. Re-running produces identical output for identical input; there is no hidden state.

---

## Glossary

Terms used consistently throughout this spec:

| Term | Meaning |
|---|---|
| **Form** | One physical A4 sheet uploaded as an image |
| **Batch** | One or more forms submitted together (often same day, sometimes multiple dates) |
| **Row** | One S.No line on a form; corresponds to one consumption record |
| **Logical field** | An output attribute (e.g. `received_by`, `sap_code`); framework-neutral |
| **Inventory item** | A row in the master `inventory` table, keyed by `sap_code` |
| **Current stock** | Live stock, computed as `opening + receipt − consumption + return` |
| **Ditto mark** | `"`, `〃`, `,,` — instruction to copy the value from the row directly above |
| **Substitution** | Replacing an out-of-stock match with a functionally-equivalent in-stock alternative |
| **Flag** | A non-blocking annotation on a row (see file 08) |
| **Struck-through** | A row with a horizontal line through it — user explicitly cancelled the entry |

---

## Design Principles

1. **Never fabricate.** If a field cannot be read confidently, mark it `null` and flag it. Do not guess the received_by name.
2. **Never silently drop data.** Struck-through rows are excluded but reported in the summary. Same for unresolved rows.
3. **Stock is ground truth.** If a match would push stock negative, the row is flagged, not paused. The user decides whether to post a receipt first.
4. **Substitution requires an explicit rule.** File 06 lists every allowed substitution. New substitutions are added by editing that file, never by heuristic.
5. **Fuzzy match returns a candidate list, not a single answer.** Confidence < threshold → flag `[?]` and pass all candidates to the UI.
6. **The user always sees what changed.** Every flagged row shows its source text vs. its resolved value.

---

## Non-Goals

This spec deliberately does **not** cover:

- The upload endpoint itself (GI_Hub already has one).
- Choice of OCR engine (LLM vision, Tesseract, Textract — all viable; each file states what interface it needs, not how to fetch it).
- Persistence layer (whether the Issue page writes to a `consumption_log` table, an event stream, or an audit log — that is a project decision).
- Authentication / authorisation on the Issue page.
- The physical printing of consumption forms.

---

## How Claude Code Should Use This

1. **Read** all files 00 → 11 in order to build a mental model.
2. **Then parse** `APPENDIX-claude-code-metadata.yaml` for the deterministic rules (matching thresholds, substitution list, flag severities).
3. **Then locate** the existing image upload endpoint and the Issue page components in GI_Hub_Project.
4. **Adapt** — do not translate line-by-line. The spec is framework-neutral on purpose. Map logical fields to actual DB columns, use existing services (auth, logging, error handling) where present, prefer existing utilities over new ones.
5. **Do not change the algorithms** in file 05 (fuzzy matching), file 06 (substitution), or file 07 (stock simulation) without noting the change back to this spec.
6. **Ask before deleting flag types.** The flag taxonomy in file 08 is derived from real user needs.

---

## Provenance

This spec is derived from a live processing session covering these dates and outcomes:

- 07/06/26 – 13/07/26: 21 forms processed
- 3 distinct date formats observed (`DD/MM/YY`, `DD.MM.YY`, `DD/MM/YYYY`)
- 40+ distinct handwritten product names resolved to inventory
- 8 substitution rules established (see file 06)
- ~15 recurring OCR errors catalogued (see file 11)

If you extend the spec, add a note in the relevant file with the date and the form that motivated the change.
