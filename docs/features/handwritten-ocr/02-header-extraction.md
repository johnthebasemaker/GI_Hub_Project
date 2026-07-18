# 02 · Header Extraction

> Reads the metadata printed at the top of the form: the project banner, the "Daily — Consumption / Safety & Production Consumables" title, and — critically — the **Date**.

**Depends on:** `01-image-preprocessing.md` (right-side-up image).
**Feeds:** `03-row-parsing.md` and every later file that stamps rows with a date.

---

## Purpose

Every consumption record must carry the date the form was written. The date is the only piece of information the operator writes freehand at the top of every sheet. Three formats have been observed on real forms:

| Format | Example | Notes |
|---|---|---|
| `DD/MM/YY` | `13/07/26` | Most common |
| `DD.MM.YY` | `28.6.26` | Dots instead of slashes |
| `DD/MM/YYYY` | `04/07/2026` | Full year, occasional |

All three are DD-first (day-month-year). **Never interpret as MM/DD.**

---

## Contract

```
input: {
  processed_image: bytes,
}

output: {
  project_banner_ok: bool,          # "MPC3P1-CNCEC PROJECT" present
  form_title_ok: bool,              # "Daily - Consumption" and "Safety & Production Consumables" present
  date_iso: string | null,          # normalised to YYYY-MM-DD
  date_raw: string | null,          # exactly as written on the form
  date_format_detected: "DD/MM/YY" | "DD.MM.YY" | "DD/MM/YYYY" | null,
  needs_review: bool,
  issues: list<string>,
}
```

- `date_iso` is what downstream code should use.
- `date_raw` is preserved for the audit trail and for the flag summary.
- `needs_review = true` if the date could not be parsed unambiguously.

---

## Extraction

The exact extraction technique is project-choice (OCR line, LLM vision, template-region scan). The **rules** below apply regardless.

### Region

The date is written in the top-right of the form, immediately after the printed word `Date :`. Confine the search to the top-right quadrant of the image; do not scan the whole page.

### Text cleanup

Before parsing:

1. Remove the label prefix (`Date :`, `Date:`, `Date`) if present.
2. Strip whitespace.
3. Replace common OCR artifacts:
   - `l` (lowercase L) → `1` when between digits
   - `O` / `o` → `0` when between digits
   - Long dashes (`—`, `–`) → `-` (only if the form ever uses dashes; not currently observed)
4. Normalise separators: keep whatever separator is used (`/` or `.`), do not swap.

### Parsing order

Try in this order; stop at the first match:

1. `^(\d{1,2})/(\d{1,2})/(\d{4})$` → DD/MM/YYYY
2. `^(\d{1,2})/(\d{1,2})/(\d{2})$` → DD/MM/YY (assume 20YY)
3. `^(\d{1,2})\.(\d{1,2})\.(\d{2})$` → DD.MM.YY (assume 20YY)

Reject if none match.

### Validity

- Day must be 1–31 (loose; final validity comes from `date()` construction)
- Month must be 1–12
- Year, once expanded, must be within `[current_year - 2, current_year + 1]` (guards against `20XX` typos)

Fail validity → `needs_review = true`, `date_iso = null`.

---

## Worked Examples

```
raw:    "13/07/26"          → date_iso = "2026-07-13", format = "DD/MM/YY"
raw:    "28.6.26"           → date_iso = "2026-06-28", format = "DD.MM.YY"
raw:    "04/07/2026"        → date_iso = "2026-07-04", format = "DD/MM/YYYY"
raw:    "4/6/26"            → date_iso = "2026-06-04", format = "DD/MM/YY"   # single-digit tolerated
raw:    "13/07/2O26"        → OCR "O"→0 → "13/07/2026" → date_iso = "2026-07-13"
raw:    "13/07"             → needs_review (missing year)
raw:    "07/13/26"          → date_iso = null (month 13 invalid → needs_review)
raw:    "13/07/29"          → needs_review (year 2029 outside window)
raw:    (illegible)         → needs_review
```

**Note on `07/13/26` in the third-to-last row:** we don't silently swap DD/MM to MM/DD. The form is *always* DD-first; if the operator wrote it wrong, that's a data-entry error, not a parse ambiguity. Flag it, show the raw string, let the user correct in the UI.

---

## Multiple Papers, Same Date

Two forms with the same date is common (large consumption days need a second sheet). This is handled in file 10 (batch processing) — the header extractor doesn't need to know.

If a *single* form has two dates written (rare — happened once when the operator corrected themselves and both dates are visible), extract both, mark `needs_review`, let the UI pick.

---

## Pseudo-code

```python
import re
from datetime import date, datetime

DATE_PATTERNS = [
    ("DD/MM/YYYY", re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")),
    ("DD/MM/YY",   re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{2})$")),
    ("DD.MM.YY",   re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{2})$")),
]

def _cleanup(raw: str) -> str:
    s = raw.replace("Date :", "").replace("Date:", "").replace("Date", "").strip()
    # OCR artifact fixes, character-by-character between digits
    out = []
    for i, ch in enumerate(s):
        prev_dig = i > 0 and s[i-1].isdigit()
        next_dig = i+1 < len(s) and s[i+1].isdigit()
        if ch in "lI" and prev_dig and next_dig:
            out.append("1"); continue
        if ch in "Oo" and prev_dig and next_dig:
            out.append("0"); continue
        out.append(ch)
    return "".join(out)

def extract_header(raw_date_string: str, today: date | None = None) -> dict:
    today = today or date.today()
    cleaned = _cleanup(raw_date_string)

    parsed = None
    fmt_name = None
    for name, pattern in DATE_PATTERNS:
        m = pattern.match(cleaned)
        if not m:
            continue
        dd, mm, yy = m.groups()
        year = int(yy) if len(yy) == 4 else 2000 + int(yy)
        try:
            parsed = date(year=year, month=int(mm), day=int(dd))
            fmt_name = name
            break
        except ValueError:
            parsed = None

    if not parsed:
        return {
            "date_iso": None,
            "date_raw": raw_date_string,
            "date_format_detected": None,
            "needs_review": True,
            "issues": ["date_unparseable"],
        }

    # Year sanity check
    if not (today.year - 2 <= parsed.year <= today.year + 1):
        return {
            "date_iso": None,
            "date_raw": raw_date_string,
            "date_format_detected": fmt_name,
            "needs_review": True,
            "issues": ["date_year_out_of_window"],
        }

    return {
        "date_iso": parsed.isoformat(),
        "date_raw": raw_date_string,
        "date_format_detected": fmt_name,
        "needs_review": False,
        "issues": [],
    }
```

---

## Test Cases

| # | Raw input | Expected `date_iso` | Format | needs_review |
|---|---|---|---|---|
| 1 | `13/07/26` | `2026-07-13` | DD/MM/YY | false |
| 2 | `28.6.26` | `2026-06-28` | DD.MM.YY | false |
| 3 | `04/07/2026` | `2026-07-04` | DD/MM/YYYY | false |
| 4 | `4/6/26` | `2026-06-04` | DD/MM/YY | false |
| 5 | `13/07/2O26` | `2026-07-13` | DD/MM/YYYY | false |
| 6 | `13/07` | null | null | true |
| 7 | `13-07-26` | null | null | true |
| 8 | `07/13/26` | null | DD/MM/YY | true |
| 9 | `13/07/29` | null | DD/MM/YY | true |
| 10 | `` (empty) | null | null | true |

---

## Claude Code Metadata

```yaml
module: header-extraction
version: 1.0
depends_on: [image-preprocessing]
feeds: [row-parsing, batch-processing]

date_formats_in_priority_order:
  - name: DD/MM/YYYY
    regex: '^(\d{1,2})/(\d{1,2})/(\d{4})$'
  - name: DD/MM/YY
    regex: '^(\d{1,2})/(\d{1,2})/(\d{2})$'
    century_assumption: 2000
  - name: DD.MM.YY
    regex: '^(\d{1,2})\.(\d{1,2})\.(\d{2})$'
    century_assumption: 2000

ocr_character_fixes_between_digits:
  l: "1"
  I: "1"
  O: "0"
  o: "0"

validity_gates:
  year_window_relative_to_today: [-2, 1]

banner_expected: "MPC3P1-CNCEC PROJECT"
title_expected_lines:
  - "Daily - Consumption"
  - "Safety & Production Consumables"

never:
  - "swap DD/MM to MM/DD"
  - "assume a missing year"
  - "auto-correct a date typed as 07/13/26 to 13/07/26"
```
