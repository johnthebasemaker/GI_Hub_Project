# 10 · Batch Processing

> Coordinates multiple forms in a single submission: assigns IDs, groups by date, orders rows for the stock simulator, and handles mixed-quality uploads.

**Depends on:** files 01–09 (calls into them).
**Feeds:** the Issue page (as a single coordinated response).

---

## Purpose

An operator commonly uploads 2–5 forms at once — sometimes for the same date (two sheets needed for a busy day), sometimes for a few days that hadn't been logged. This file specifies:

- How each form gets its identifier
- How mixed-date uploads are ordered
- What happens when only *some* forms in a batch are unreadable
- How the batch is presented as a single logical unit

---

## Contract

```
input: {
  uploads: list<{ image: bytes, filename: string, mime_type: string }>,
  submitted_by: string,
}

output: BatchPayload           # from file 09
```

The batch endpoint is the outer wrapper around all the file 01–09 stages.

---

## Steps

### 1. Assign source_form_ids

For each upload, generate a stable id **before** any processing:

```
source_form_id = f"form_{timestamp}_{short_hash}"
```

Where `short_hash` is a hash of the image bytes truncated to 8 chars. This ensures re-uploading the same image gets a different id (timestamp differs) but audit trails can detect duplicate content.

### 2. Preprocess each image (file 01)

Run in parallel where possible; independent. Each yields either a processed image or a rejection.

### 3. Extract header for each (file 02)

Per-form. Rejected forms are skipped from here on but retained in `sources` list with their rejection reason.

### 4. Sort forms

Group and sort by date:

- Primary sort: `date_iso` ascending (nulls last — but a null date is a rejection anyway)
- Secondary sort: upload order (client-side timestamp of the file, or upload index)

Two forms with the same date maintain their upload order. This makes the outcome reproducible.

### 5. Parse and map (files 03, 04)

Per-form, produces per-row output.

### 6. Fuzzy-match and stock-validate (files 05, 06)

Per-row. **Uses the same inventory snapshot** across the whole batch — taken once at step 1 timestamp. This is essential: if a receipt is posted mid-batch by another user, we must not partially apply it.

### 7. Chronologically ordered simulation (file 07)

Combine all rows from all forms into a single ordered stream:

```
sort_key = (date_iso, source_form_id, source_row_no)
```

Run the simulator once over this combined stream. This makes cross-form deductions work correctly (e.g. Form A on 13/07 uses 4 face shields; Form B also on 13/07 uses 5 more — the simulator sees them in sequence).

### 8. Flag everything (file 08)

Merge per-row and batch-level flags.

### 9. Emit payload (file 09)

One JSON, ready for the Issue page to render.

---

## Handling Mixed-Quality Uploads

If the operator uploads 5 images and 1 is rejected:

- The 4 good forms are fully processed.
- The rejected form appears in `sources` with its `rejected` flag and reason.
- The batch is not aborted.

The Issue page shows a banner: "1 of 5 images could not be read — [Retry upload]". The operator can re-add the missing image without losing the work on the other 4.

If **all** images are rejected:

- `sources` populated with rejection details
- `rows` empty
- Batch-level flag `BATCH_ALL_IMAGES_REJECTED`

---

## Two Forms, Same Date

Handled by the sort key. Both forms' rows are interleaved by `(date_iso, source_form_id, source_row_no)`, but form_id is what the operator sees as "Sheet 1" / "Sheet 2" in the UI.

**Important:** cross-form ditto does *not* work. A ditto mark on row 1 of Sheet 2 refers to the last non-ditto value on Sheet 2, not the last on Sheet 1. Each form is parsed independently in file 03.

---

## Two Forms, Different Dates

Common case. Example:

- Upload 1: form dated 13/07/26
- Upload 2: form dated 14/07/26

Both sit in the same batch, but the simulator sees 13/07 rows first, then 14/07 rows. That's exactly how stock deductions ripple correctly.

---

## Inventory Snapshot: When, Once

Take the snapshot at batch start, not per row and not per form. Store it in memory for the duration of the batch's processing. Rationale:

- **Consistency**: two concurrent batches don't step on each other's mid-flight state.
- **Auditability**: the `raw_extraction` + snapshot + resulting rows are enough to reproduce a batch offline.
- **Performance**: one DB read for inventory (indexed by SAP), then all in-memory.

If a receipt is posted by another user while a batch is being reviewed on the UI, the operator will see stale stock numbers. When they finally click "Post", the server re-checks stock and any new negatives are surfaced as `CRIT_WOULD_GO_NEGATIVE` errors on the post attempt (out of OCR scope; standard optimistic-concurrency concern).

---

## Idempotency and Retries

- **OCR endpoint**: safe to call multiple times with the same images — each call is a fresh batch with a fresh `batch_id`. The endpoint does not write to the DB.
- **Post endpoint** (Issue page → DB): idempotent by `batch_id`. If the client retries with the same `batch_id`, the server refuses to double-write.

---

## Concurrency Considerations

- **Multiple operators, same site**: each has their own batch. Snapshots are taken independently. Actual DB writes race in the normal way (transactional; the loser sees stock has changed and must review).
- **Same operator, two browser tabs**: same treatment — each tab has its own batch_id.

---

## Size Limits

Practical guidance, not part of the spec proper:

- Maximum images per batch: **10** (soft — form-submission UX becomes unwieldy above this)
- Maximum rows per form: **30** (physical form limit)
- Maximum rows per batch: **300** (10 × 30)

If future forms grow (e.g. a 60-row variant), file 03 needs adjustment but this file does not.

---

## Pseudo-code

```python
import hashlib, time

def process_batch(uploads: list[dict], submitted_by: str,
                  inventory_snapshot: dict) -> dict:
    batch_id = f"batch_{int(time.time())}_{_short_hash(uploads)}"

    sources = []
    all_rows: list[dict] = []

    # Steps 1-3: preprocess + header extraction per form
    for idx, up in enumerate(uploads):
        form_id = f"form_{int(time.time()*1000)}_{_content_hash(up['image'])[:8]}"
        pre = preprocess(up["image"], up["mime_type"])
        src = {
            "source_form_id": form_id,
            "original_filename": up["filename"],
            "upload_index": idx,
            "processed_at": _now_iso(),
        }
        if pre["rejected"]:
            src["rejected"] = True
            src["rejection_reason"] = pre["rejection_reason"]
            sources.append(src)
            continue

        header = extract_header(_read_header_text(pre["processed_image"]))
        src["header"] = header
        if header["date_iso"] is None:
            src["rejected"] = True
            src["rejection_reason"] = "date_unparseable"
            sources.append(src)
            continue

        # Steps 5-6: parse + map + fuzzy + stock validate
        raw_rows = _extract_rows(pre["processed_image"])
        parsed = parse_rows(raw_rows, header["date_iso"])
        for r in parsed["rows"]:
            mapped = map_columns(r, header, form_id)
            match  = fuzzy_match(
                mapped["product_name_raw"] or "",
                list(inventory_snapshot.values()),
                hints={"remarks": mapped.get("work_type")}
            )
            if match["match"]:
                validated = validate_stock(match["match"], mapped["qty"], inventory_snapshot)
                mapped.update(validated)
            else:
                mapped["blocked"] = True
                mapped["candidates"] = match["candidates"]
            all_rows.append(mapped)

        src["row_count"] = len(parsed["rows"])
        sources.append(src)

    # Step 7: chronological simulation across the whole batch
    all_rows.sort(key=lambda r: (r["date_iso"], r["source_form_id"], r["source_row_no"]))
    sim = simulate_stock(all_rows, inventory_snapshot)

    # Step 8: flag rollup
    summary = _build_summary(sim["rows"], sim["low_stock_events"], sources)

    # Step 9: emit
    return {
        "batch_id": batch_id,
        "created_at": _now_iso(),
        "submitted_by": submitted_by,
        "summary": summary,
        "sources": sources,
        "rows": sim["rows"],
    }
```

---

## Test Cases

| # | Scenario | Expected |
|---|---|---|
| 1 | 1 form, clean | Batch has 1 source, N rows, no batch flags |
| 2 | 3 forms, all same date | 3 sources, rows interleaved by form then row_no |
| 3 | 2 forms, dates 13/07 and 14/07 | 13/07 rows come first in `rows` |
| 4 | 5 forms, 2 rejected | 5 sources (2 with `rejected: true`), rows only from the 3 good ones |
| 5 | 5 forms, all rejected | Empty `rows`, batch flag `BATCH_ALL_IMAGES_REJECTED` |
| 6 | Same form uploaded twice | 2 sources (different form_ids), rows duplicated with different `id` |
| 7 | Same batch_id posted twice | Post endpoint rejects second call (out of OCR scope but noted) |
| 8 | 2 forms, both consume 3 face shields, snapshot has 4 | 1st form's 3 rows pass, 2nd form's 3 rows blocked with `WOULD_GO_NEGATIVE` |

---

## Claude Code Metadata

```yaml
module: batch-processing
version: 1.0
depends_on: [image-preprocessing, header-extraction, row-parsing, column-mapping,
             fuzzy-matching, stock-validation, stock-simulation, flag-system, output-format]

id_generation:
  batch_id: "batch_{unix_ts}_{short_hash}"
  form_id:  "form_{unix_ms}_{content_hash_8}"

sort_key_for_simulation: [date_iso, source_form_id, source_row_no]

inventory_snapshot:
  taken_when: "start of batch"
  scope:      "entire batch (never per-row)"
  refreshed:  "on Post attempt, in the DB layer"

mixed_quality_upload_behaviour: continue_with_valid_forms

cross_form_ditto: false
cross_form_stock: true

size_limits:
  images_per_batch: 10
  rows_per_form: 30
  rows_per_batch: 300

concurrency_model: optimistic
idempotency:
  ocr_endpoint:  by_default_no
  post_endpoint: by_batch_id
```
