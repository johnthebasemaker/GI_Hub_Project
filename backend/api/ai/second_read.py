"""
backend/api/ai/second_read.py — read again the rows the first read skipped
(Phase 23b, ruling Q23-3: automatic, in the background, 0 s added wait).

⚠️ THE SKIPPED LINES ARE NOT AT THE BOTTOM. The plan (and the Phase 22
summary before it) assumed the 7 lines the reader missed on the 8 photos were
"the last rows of a full page", and proposed re-reading the bottom third. The
S.No column says otherwise: all 7 are on ONE page (6 Oct, Day, 24 lines), the
reader DID reach printed row 28, and what it dropped is rows 2–4 and 9–12 —
two runs of rows that are almost nothing but ditto marks under "Leather
gloves" and "Dust mask". A bottom crop would have recovered none of them.

So the second read is aimed at the GAPS in the printed S.No column: a row the
paper numbers (1..30, printed) that the first read did not return is either
genuinely blank or skipped, and only a look can tell. The page's printed row
lines are found (OpenCV, a narrow vertical slice so a tilted photo still gives
straight lines), the gap rows are cut out as strips, stacked into ONE small
image, and read once with a prompt that names the rows.

⚠️ AND ONLY ROWS WITH HANDWRITING IN THEM. The first measurement sent every
gap row, blank ones included, and the model INVENTED four rows (printed rows
14, 17, 29, 30 of the 6 Oct paper — all blank — came back as ditto marks with
a quantity of 1). Each gap row's ink is now measured against the page's own
written rows, and a blank row is never shown to the model. That also makes
the read shorter: decode, the expensive part here, is paid only for real rows.

Never blocks: `jobs.run_job` writes the first read's rows as `done` FIRST, then
runs this; the page polls for `second_read` and slots the new rows in by S.No.
"""
from __future__ import annotations

import io
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

ROWS_PER_PAGE = 30          # the printed "Daily - Consumption" log
PAD_TOP = 0.12              # a strip reaches this far into the row above it —
                            # more, and the model reads the row above as the first row
PAD_BOTTOM = 0.35           # …and this far below (handwriting sags onto the line)
HEADER_ROWS = 1.4           # the printed column header sits this high above row 1
UPSCALE = 2                 # strips are small: twice the pixels per handwritten stroke


def _sno(r: dict) -> Optional[int]:
    try:
        v = int(str(r.get("sno")).strip())
    except (TypeError, ValueError):
        return None
    return v if 1 <= v <= ROWS_PER_PAGE else None


def missing_rows(rows: list[dict]) -> list[int]:
    """Printed rows the first read did not return (1..30). Most are blank —
    `written_gaps` keeps only the ones with handwriting in them.

    Needs a numbered read: when fewer than half the rows carry a usable S.No
    the page cannot be told apart from its gaps, and nothing is re-read."""
    if not rows:
        return []
    snos = [_sno(r) for r in rows]
    known = [s for s in snos if s is not None]
    if len(known) * 2 < len(rows):
        return []
    read = set(known)
    return [k for k in range(1, ROWS_PER_PAGE + 1) if k not in read]


def read_snos(rows: list[dict]) -> set[int]:
    return {s for s in (_sno(r) for r in rows) if s is not None}


def runs(nums: list[int]) -> list[tuple[int, int]]:
    """[2,3,4,9,10] → [(2,4), (9,10)]."""
    out: list[tuple[int, int]] = []
    for n in sorted(set(nums)):
        if out and n == out[-1][1] + 1:
            out[-1] = (out[-1][0], n)
        else:
            out.append((n, n))
    return out


# ── geometry ────────────────────────────────────────────────────────────────
def _lines_in_slice(bw, x0: int, x1: int) -> list[float]:
    import cv2
    import numpy as np
    sl = bw[:, x0:x1]
    w = sl.shape[1]
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (max(10, int(w * 0.6)), 1))
    horiz = cv2.morphologyEx(sl, cv2.MORPH_OPEN, k)
    prof = horiz.sum(axis=1) / 255 / max(w, 1)
    ys = np.where(prof > 0.5)[0]
    out, cur = [], []
    for y in ys:
        if cur and y - cur[-1] > 3:
            out.append(float(np.mean(cur)))
            cur = []
        cur.append(int(y))
    if cur:
        out.append(float(np.mean(cur)))
    return out


class Grid:
    """The 31 ruled lines bounding the 30 printed rows, as a straight line each
    across the page — so a TILTED photo is measured along its own rows.

    Found in narrow vertical slices (a full-width projection smears on a
    tilted photo); a slice counts when it holds 31 lines whose spacing is even
    (the data rows are equal height, the header and the signature box are not),
    and the slices that agree on where row 1 starts are fitted line by line.
    Measured on the operator's 19 photos: 18 found."""

    def __init__(self, xs, lines):
        import numpy as np
        self.xs = np.asarray(xs, dtype=float)
        self.L = np.asarray(lines, dtype=float)              # slices × 31
        if len(self.xs) >= 2:
            self.coef = [np.polyfit(self.xs, self.L[:, k], 1) for k in range(ROWS_PER_PAGE + 1)]
        else:
            self.coef = [(0.0, float(self.L[0, k])) for k in range(ROWS_PER_PAGE + 1)]
        self.row_h = float(np.median(np.diff(self.L, axis=1)))

    def y(self, k: int, x):
        import numpy as np
        return np.polyval(self.coef[k], x)

    def span(self, a: int, b: int, width: int) -> tuple[int, int]:
        """Top of printed row a to the bottom of row b, over the whole width."""
        import numpy as np
        xs = np.array([0.0, float(width)])
        top = float(min(self.y(a - 1, xs)))
        bottom = float(max(self.y(b, xs)))
        return int(top - PAD_TOP * self.row_h), int(bottom + PAD_BOTTOM * self.row_h)

    def header(self, width: int) -> tuple[int, int]:
        """The printed column header (S.No | Name | Tank No.# | Product …)."""
        import numpy as np
        xs = np.array([0.0, float(width)])
        top = float(min(self.y(0, xs)))
        return int(top - HEADER_ROWS * self.row_h), int(float(max(self.y(0, xs))) + 2)


def page_grid(gray) -> Optional[Grid]:
    try:
        import cv2
        import numpy as np
    except ImportError:          # no OpenCV: no grid, so no second read
        return None
    h, w = gray.shape
    bw = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                               cv2.THRESH_BINARY_INV, 31, 12)
    found = []
    for f0 in np.arange(0.10, 0.86, 0.06):
        ys = _lines_in_slice(bw, int(w * f0), int(w * (f0 + 0.08)))
        best = None
        for i in range(len(ys) - ROWS_PER_PAGE):
            seg = ys[i:i + ROWS_PER_PAGE + 1]
            d = np.diff(seg)
            med = float(np.median(d))
            if med < 8:
                continue
            spread = float((d.max() - d.min()) / med)
            if best is None or spread < best[0]:
                best = (spread, seg)
        if best and best[0] <= 0.35:
            found.append((w * (f0 + 0.04), best[1]))
    if not found:
        return None
    top = float(np.median([f[1][0] for f in found]))
    rh = float(np.median([np.median(np.diff(f[1])) for f in found]))
    found = [f for f in found if abs(f[1][0] - top) < 0.5 * rh]
    return Grid([f[0] for f in found], [f[1] for f in found]) if found else None


def row_ink(gray, grid: Grid) -> list[float]:
    """Handwriting in each printed row: dark pixels left once the ruled lines
    are removed, inside the row (its top and bottom fifth skipped), across the
    table, followed along the row's own tilt."""
    import cv2
    import numpy as np
    h, w = gray.shape
    bw = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                               cv2.THRESH_BINARY_INV, 31, 12)
    hk = cv2.morphologyEx(bw, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (w // 14, 1)))
    vk = cv2.morphologyEx(bw, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, h // 45)))
    ink = cv2.subtract(bw, cv2.bitwise_or(hk, vk))
    cols = np.arange(int(w * 0.13), int(w * 0.92), 3)
    out = []
    for k in range(ROWS_PER_PAGE):
        top, bot = grid.y(k, cols), grid.y(k + 1, cols)
        pad = (bot - top) * 0.2
        total = n = 0
        for x, a, b in zip(cols, (top + pad).astype(int), (bot - pad).astype(int)):
            if b > a:
                total += int(ink[max(a, 0):min(b, h), x].sum()) // 255
                n += b - a
        out.append(total / max(n, 1))
    return out


INK_SHARE = 0.35     # of a typical written row's ink — measured: skipped rows 0.45–0.66,
                     # blank rows ≤ 0.15, a lone scribble 0.32 (operator photos, 2026-10-09)


def written_gaps(gray, grid: Grid, gaps: list[int], read: set[int]) -> tuple[list[int], dict]:
    """The gap rows with handwriting in them. ⚠️ THE MODEL INVENTS ROWS: asked
    about printed rows 14, 17, 29 and 30 of the 6 Oct paper — all four blank —
    it returned four rows of ditto marks and a quantity of 1. So a blank row is
    never shown to it; only rows whose ink is at least INK_SHARE of the page's
    typical written row are."""
    import numpy as np
    ink = row_ink(gray, grid)
    ref_rows = [ink[k - 1] for k in read if 1 <= k <= ROWS_PER_PAGE]
    ref = float(np.median(ref_rows)) if ref_rows else 0.0
    if ref <= 0:
        return [], {"ink_ref": 0}
    keep = [k for k in gaps if ink[k - 1] >= INK_SHARE * ref]
    return keep, {"ink_ref": round(ref, 4),
                  "blank": [k for k in gaps if k not in keep]}


def strip_image(jpeg: bytes, gaps: list[int], read: Optional[set[int]] = None
                ) -> Optional[tuple[bytes, dict]]:
    """One JPEG of the WRITTEN gap rows stacked top to bottom, and how it was
    cut — or None (no gaps, no grid, or every gap row is blank)."""
    if not gaps:
        return None
    import numpy as np
    from PIL import Image, ImageOps
    im = ImageOps.exif_transpose(Image.open(io.BytesIO(jpeg))).convert("RGB")
    gray = np.asarray(im.convert("L"))
    grid = page_grid(gray)
    if grid is None:
        return None
    rows = gaps
    meta: dict = {"geometry": "ruled lines", "slices": int(len(grid.xs))}
    if read is not None:
        rows, info = written_gaps(gray, grid, gaps, read)
        meta.update(info)
    if not rows:
        return None
    # the column header first, so the strips below read as the same table
    h0, h1 = grid.header(im.width)
    parts = [im.crop((0, max(0, h0), im.width, max(1, h1)))] if h1 - max(0, h0) > 4 else []
    for a, b in runs(rows):
        y0, y1 = grid.span(a, b, im.width)
        y0, y1 = max(0, y0), min(im.height, y1)
        if y1 - y0 > 4:
            parts.append(im.crop((0, y0, im.width, y1)))
    if not parts:
        return None
    gap_px = 12
    out = Image.new("RGB", (im.width, sum(p.height for p in parts) + gap_px * (len(parts) - 1)), "white")
    y = 0
    for p in parts:
        out.paste(p, (0, y))
        y += p.height + gap_px
    if UPSCALE > 1:
        out = out.resize((out.width * UPSCALE, out.height * UPSCALE), Image.LANCZOS)
    buf = io.BytesIO()
    out.save(buf, format="JPEG", quality=88)
    meta.update({"strips": len(parts) - 1, "rows": rows, "size": [out.width, out.height]})
    return buf.getvalue(), meta


# ── the prompt ──────────────────────────────────────────────────────────────
def _rows_text(gaps: list[int]) -> str:
    return ", ".join(f"{a}" if a == b else f"{a}–{b}" for a, b in runs(gaps))


def user_prompt(gaps: list[int]) -> str:
    return (
        f"This image is strips cut from the SAME form. The top strip is the printed "
        f"column header (S.No | Name | Tank No.# | Product Name | UOM | QTY | "
        f"Remarks); below it are the printed rows S.No {_rows_text(gaps)} (a strip "
        f"may show a sliver of the row above or below it — ignore it). Read the "
        f"S.No printed in the left column of each row. Transcribe EVERY printed row in these strips that has "
        f"anything written in it — one JSON row per printed row, with its "
        f"printed S.No, EVEN WHEN every cell is a ditto mark (output <DITTO> for "
        f"each such cell). A row with nothing written at all is left out. Do not "
        f"output a row whose S.No is not in that list. Same JSON shape as "
        f"always: {{\"date_text\": \"\", \"rows\": [...]}}."
    )


# ── reading the answer ──────────────────────────────────────────────────────
def parse_rows(text: str) -> list[dict]:
    """The second read's rows. ⚠️ An EMPTY answer is a valid answer here — the
    gap rows are often genuinely blank — whereas `ocr.parse_vision_reply`
    rightly refuses a first read with no rows (a photo nobody could read must
    not look like a blank form). Only that refusal is turned into []."""
    from . import ocr
    obj = ocr.extract_json_object(text) or ocr.salvage_truncated_json(text)
    if not obj or not isinstance(obj.get("rows"), list):
        return []
    # ⚠️ LENIENT ON PURPOSE. The first read's parser drops a row with no product
    # and no quantity — a ditto row read as empty cells has only a NAME, and
    # that is exactly the row this read exists to recover. Blank rows were never
    # shown to the model (`written_gaps`), so a name alone is kept.
    rows = [ocr.clean_consumption_row(r) for r in obj["rows"] if isinstance(r, dict)]
    return [r for r in rows if any(str(r.get(f) or "").strip() for f in
                                   ("issued_to", "material_text", "qty_text", "tank_no", "work_type"))
            or r.get("quantity") is not None]


# ── merging ─────────────────────────────────────────────────────────────────
def accept(second_rows: list[dict], gaps: list[int], first_rows: list[dict]) -> list[dict]:
    """The second read's rows worth adding: a gap S.No, not already read, one
    per S.No, and not empty."""
    have = {_sno(r) for r in first_rows}
    wanted, seen, out = set(gaps), set(), []
    for r in second_rows:
        s = _sno(r)
        if s is None or s not in wanted or s in have or s in seen:
            continue
        if not any(str(r.get(f) or "").strip() for f in
                   ("issued_to", "material_text", "qty_text", "tank_no", "work_type")) \
                and r.get("quantity") is None:
            continue
        seen.add(s)
        out.append({**r, "second_read": True})
    return out


def merge(first_rows: list[dict], added: list[dict]) -> list[dict]:
    """The page in printed order (rows without an S.No keep their place)."""
    if not added:
        return list(first_rows)
    merged = list(first_rows) + list(added)
    keyed = [(_sno(r) if _sno(r) is not None else 10_000 + i, i, r) for i, r in enumerate(merged)]
    return [r for _k, _i, r in sorted(keyed, key=lambda t: (t[0], t[1]))]


def initial_state(gaps: list[int], meta: dict, expected_s: int, started_at: str) -> dict[str, Any]:
    return {"status": "running", "rows": gaps, "geometry": meta.get("geometry"),
            "started_at": started_at, "expected_s": expected_s}
