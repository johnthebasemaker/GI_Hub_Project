# 05 · Fuzzy Matching

> Resolves a handwritten product name (post-OCR string) to an inventory row keyed by `sap_code`. This is the single most consequential step; a wrong match posts consumption against the wrong item and corrupts stock.

**Depends on:** `04-column-mapping.md` (must have the raw `product_name` string in hand).
**Feeds:** `06-stock-validation.md` (the returned candidate is then stock-checked).

---

## Purpose

The handwritten `Product Name` column contains freeform text. It rarely matches the canonical inventory description exactly. Reasons observed on real forms:

- **Typos / phonetic spelling** — `Leather Yloues` → `Leather gloves`
- **Abbreviations** — `Yvek coverall` → `Tyvek Coverall`
- **Case differences** — `DUST MASK` vs `Dust Mask` vs `dust mask`
- **Word order** — `Coated Green gloves` vs `Green Coated gloves`
- **Missing detail** — `Mask` (which mask?), `Gloves` (which gloves?)
- **Ambiguous shorthand** — `Belt` could be lifting belt, safety harness belt, etc.
- **Alternate names** — `Head Lamp 24V` vs `HANGLING LAMP 24V`

The fuzzy match must resolve as many of these as possible **without ever silently guessing** on the ambiguous cases.

---

## Contract

```
input:  {
  query: string,                    # raw OCR'd product name
  inventory: list<InventoryItem>,   # full inventory, in any order
  hints?: {                         # optional context
    remarks?: string,               # from the Remarks column
    prev_row_product?: string,      # for ditto-resolution ambiguity
  }
}

output: {
  match: InventoryItem | null,      # the resolved item, or null if none
  score: number,                    # 0..100
  candidates: list<{                # top-N alternates, always populated
    item: InventoryItem,
    score: number,
    reason: string,                 # e.g. "substring", "word-overlap-3"
  }>,
  needs_review: bool,               # true if score < AUTO_ACCEPT_THRESHOLD
}
```

`match` is populated **only** when confidence is above the auto-accept threshold. Below that, `match = null`, `needs_review = true`, and the caller (file 08) flags the row `[?]` and surfaces the top candidates in the UI.

---

## Algorithm

Two passes, in order. Stop as soon as a pass yields a decisive result.

### Pass 1 — Exact match (case-insensitive, whitespace-normalised)

Normalise both the query and every candidate description:

- Trim leading/trailing whitespace
- Collapse internal whitespace to single spaces
- Lowercase
- (Do **not** strip punctuation; `(RED-GREEN 60 PAIR / BOX)` is meaningful)

If exactly one inventory row matches after normalisation → return it with `score = 100`.
If more than one matches (rare, but possible when the inventory has near-duplicates) → treat as Pass 1 failure and go to Pass 2, but seed the candidate list with these hits.

### Pass 2 — Weighted scoring

For every inventory row, compute a score:

| Signal | Points | Notes |
|---|---|---|
| Query is a substring of candidate | +20 | e.g. query `Leather gloves` finds candidate `Leather gloves (120 ea) (240 PCS./BOX)` |
| Candidate is a substring of query | +15 | e.g. query `Big Blasting glass` finds candidate `Blasting glass` |
| Shared word (length > 2), each occurrence | +5 | Case-insensitive. Words are whitespace-split. Ignore words like `a`, `of`, `the`, and numbers with no unit |
| Query and candidate share ≥ 3 shared words | +5 bonus | Prevents single-word coincidences from winning |
| Candidate matches a hint from `Remarks` | +3 per hint word | e.g. Remarks says "Blasting" → any candidate containing "Blast" gets bumped |
| First word matches | +3 | The first word is often the most discriminating (`SAFETY`, `DUST`, `WELDING`) |

Then normalise the highest score to a 0–100 confidence via a soft cap:

```
confidence = min(100, raw_score * 2)
```

### Decision

- `confidence >= 40` **and** the top candidate leads the runner-up by **≥ 8 points** → auto-accept. Set `match`, `needs_review = false`.
- Otherwise → `match = null`, `needs_review = true`. Always return the top 5 candidates ranked by score.

**Rationale for the lead requirement:** two closely-scored candidates (e.g. `Blasting glass Big` and `Blasting glass Small` both score 30 for query `Blasting glass`) must never auto-accept — the user needs to disambiguate.

---

## Worked Examples

Each example uses the inventory items actually present in the CNCEC master.

### Example 1 — Trivial

```
query:      "Green Coated gloves"
best hit:   "Green Coated gloves"  (SAP 1145)
pass:       1 (exact after normalisation)
confidence: 100
decision:   auto-accept
```

### Example 2 — Typo

```
query:      "Leather Yloues"
candidates: Leather gloves (SAP 1165)  → substring? no
                                        → words: {leather, gloves} vs {leather, yloues}
                                        → 1 shared word (leather), 1 word > 2 chars
                                        → score = 5 (shared) + 3 (first word) = 8
                                        → confidence = 16
decision:   needs_review, top candidate surfaced
```

**Note:** phonetic OCR errors like `Yloues → Gloves` are not caught by this algorithm. They must be corrected upstream (see file 11 — Common Corrections table) before fuzzy matching runs.

### Example 3 — Substring

```
query:      "Leather gloves"
candidate:  "Leather gloves (120 ea) (240 PCS./BOX)"  (SAP 1165)
signals:    query-in-candidate: +20
            shared words: leather (+5), gloves (+5) = 10
            first-word match: +3
            ≥3 shared words bonus: no
raw score:  33
confidence: 66
runner-up:  "WELDING APRON COVER LEATHER" (SAP 1247)
            shared: leather (+5), first-word: no  → 5 → conf 10
lead:       66 − 10 = 56 (well above 8)
decision:   auto-accept SAP 1165
```

### Example 4 — Ambiguous (must flag)

```
query:      "Blasting glass"
candidate A: "Blasting glass Big"    (SAP 1109)
candidate B: "Blasting glass Small"  (SAP 1187)
Both:       query-in-candidate: +20
            shared words: blasting (+5), glass (+5) = 10
            first-word: +3
            raw = 33, conf = 66
lead:       0
decision:   needs_review, both surfaced
Hint use:   if Remarks contains "(Big)" or "(Small)", pass 2 bonus of +3
            disambiguates automatically
```

### Example 5 — Alternate name (fails; correction needed)

```
query:      "Head Lamp 24V"
inventory:  "HANGLING LAMP 24V"  (SAP 1083)
signals:    shared: 24v (+5, but "24v" is only 3 chars — borderline)
            first-word: no (head ≠ hangling)
            no substring in either direction
raw score:  ~5
confidence: ~10
decision:   needs_review
resolution: add to file 11 corrections table
            "Head Lamp" → "HANGLING LAMP"
```

### Example 6 — Hint changes the winner

```
query:      "Mask"
Remarks:    "Blasting"
Candidates:
  DUST Mask               (SAP 1131)   shared: mask (+5), first-word: no
                                       score 5
  Blasting Helmet         (SAP 1149)   shared: none
                                       hint "Blasting" +3 → score 3
  CHEMICAL FILTER MASK 3M (SAP 1226)   shared: mask (+5)  → score 5

decision:   still needs_review — hint alone isn't enough here
```

The hint mechanism helps in genuinely ambiguous cases (e.g. `Red flag` alongside `Green flag`), not in cases where the query is under-specified.

---

## Pseudo-code

```python
from dataclasses import dataclass

AUTO_ACCEPT_MIN_CONFIDENCE = 40
AUTO_ACCEPT_MIN_LEAD       = 8
STOPWORDS = {"a", "an", "the", "of", "for", "and", "or", "to", "with"}

@dataclass
class MatchCandidate:
    item: dict          # inventory row
    score: int          # raw score (pre-normalisation)
    confidence: int     # 0..100
    reason: str

def _normalise(s: str) -> str:
    return " ".join(s.strip().lower().split())

def _tokens(s: str) -> set[str]:
    return {
        w for w in _normalise(s).split()
        if len(w) > 2 and w not in STOPWORDS
    }

def fuzzy_match(query: str,
                inventory: list[dict],
                hints: dict | None = None) -> dict:
    hints = hints or {}
    q = _normalise(query)
    q_tokens = _tokens(query)
    q_first  = q.split()[0] if q else ""

    hint_words = _tokens(hints.get("remarks", "")) if hints else set()

    # Pass 1 — exact
    exact = [i for i in inventory if _normalise(i["description"]) == q]
    if len(exact) == 1:
        return {
            "match": exact[0],
            "score": 100,
            "candidates": [
                MatchCandidate(exact[0], 100, 100, "exact")
            ],
            "needs_review": False,
        }

    # Pass 2 — weighted scoring
    scored: list[MatchCandidate] = []
    for item in inventory:
        cand = _normalise(item["description"])
        c_tokens = _tokens(item["description"])
        c_first  = cand.split()[0] if cand else ""
        score = 0
        reasons = []

        if q and q in cand:
            score += 20; reasons.append("substring-q-in-c")
        if cand and cand in q:
            score += 15; reasons.append("substring-c-in-q")

        shared = q_tokens & c_tokens
        if shared:
            score += 5 * len(shared)
            reasons.append(f"shared-words:{len(shared)}")
        if len(shared) >= 3:
            score += 5
            reasons.append("shared-bonus")

        if q_first and q_first == c_first:
            score += 3
            reasons.append("first-word")

        hint_shared = hint_words & c_tokens
        if hint_shared:
            score += 3 * len(hint_shared)
            reasons.append(f"hint:{len(hint_shared)}")

        if score > 0:
            scored.append(MatchCandidate(
                item=item,
                score=score,
                confidence=min(100, score * 2),
                reason=",".join(reasons),
            ))

    scored.sort(key=lambda c: c.score, reverse=True)
    top5 = scored[:5]

    if not top5:
        return {"match": None, "score": 0, "candidates": [], "needs_review": True}

    winner = top5[0]
    runner = top5[1] if len(top5) > 1 else None
    lead   = winner.score - (runner.score if runner else 0)

    auto = (
        winner.confidence >= AUTO_ACCEPT_MIN_CONFIDENCE
        and lead >= AUTO_ACCEPT_MIN_LEAD
    )

    return {
        "match": winner.item if auto else None,
        "score": winner.confidence,
        "candidates": top5,
        "needs_review": not auto,
    }
```

---

## Test Cases

Every test case is a real form entry that occurred during the derivation of this spec. Keep them; regression matters.

| # | Query | Hints (remarks) | Expected outcome |
|---|---|---|---|
| 1 | `Green Coated gloves` | — | Auto-accept SAP 1145, conf 100 |
| 2 | `Leather gloves` | — | Auto-accept SAP 1165 |
| 3 | `Leather Yloues` | — | `needs_review`, SAP 1165 surfaces in top 5 (via corrections table) |
| 4 | `Blasting glass` | — | `needs_review`, SAP 1109 and 1187 tied at top |
| 5 | `Blasting glass` | `(Big)` | Auto-accept SAP 1109 (hint tips balance) |
| 6 | `Blasting glass` | `(Small)` | Auto-accept SAP 1187 |
| 7 | `Head Lamp 24V` | — | `needs_review` (see file 11 for correction) |
| 8 | `Duct 300 mm` | — | `needs_review` (no 300mm duct exists; user must clarify) |
| 9 | `Mask` | `Blasting` | `needs_review`, DUST Mask + CHEMICAL FILTER MASK 3M surfaced |
| 10 | `Fire Extinguisher` | — | Auto-accept `Fire Extinguisher` if in stock; **file 06** then substitutes when zero |
| 11 | `Yvek coverall` | — | `needs_review` (typo of Tyvek) |
| 12 | `Warning Tape` | — | Auto-accept `WARNING TAPE RED/WHITE` (SAP 1224) — only one candidate wins strongly |
| 13 | `` (empty) | — | Return `{match: null, candidates: [], needs_review: false}` — nothing to match, caller must skip |

---

## Interactions with Other Files

- **File 04** decides what the `query` and `hints.remarks` are.
- **File 06** consumes the `match`. If `needs_review` is true, file 06 is skipped (no substitution on an uncertain hit).
- **File 08** turns `needs_review = true` into the `[?]` flag.
- **File 11** owns the pre-processing corrections that this file assumes have already happened.

---

## Claude Code Metadata

```yaml
module: fuzzy-matching
version: 1.0
depends_on: [column-mapping]
feeds: [stock-validation, flag-system]

constants:
  AUTO_ACCEPT_MIN_CONFIDENCE: 40
  AUTO_ACCEPT_MIN_LEAD: 8
  TOP_N_CANDIDATES: 5
  MIN_WORD_LENGTH: 3
  STOPWORDS: [a, an, the, of, for, and, or, to, with]

scoring:
  substring_query_in_candidate: 20
  substring_candidate_in_query: 15
  shared_word:                  5   # per word, len > 2
  shared_word_bonus_threshold:  3   # words
  shared_word_bonus_points:     5
  first_word_match:             3
  hint_word_match:              3   # per word

passes:
  - name: exact
    normalise: [trim, collapse-ws, lowercase]
    tie_break: fall_through_to_pass_2

  - name: weighted
    scoring: see above
    normalise_score: "min(100, raw_score * 2)"

decision:
  auto_accept_when:
    - confidence >= AUTO_ACCEPT_MIN_CONFIDENCE
    - (winner.score - runner.score) >= AUTO_ACCEPT_MIN_LEAD
  else: needs_review

interfaces:
  input:
    query: string
    inventory: "list of {sap_code, description, current_stock, ...}"
    hints: "{remarks?: string, prev_row_product?: string}"
  output:
    match: "InventoryItem | null"
    score: "int (0..100)"
    candidates: "list of {item, score, confidence, reason}"
    needs_review: bool

do_not_change_without_updating:
  - "test cases table in this file"
  - "file 08 flag thresholds"
  - "file 11 corrections table"
```
