#!/usr/bin/env python3
"""
tools/ocr_eval.py — measure the consumption-paper OCR against the workbook
(Phase 21d, brief Track 1, rulings Q21-1..6).

    .venv/bin/python tools/ocr_eval.py                      # run (vision cached)
    .venv/bin/python tools/ocr_eval.py --rescore            # no vision: re-score the cache
    .venv/bin/python tools/ocr_eval.py --semantic           # also try the embedding layer (Q21-4)
    .venv/bin/python tools/ocr_eval.py --propose-aliases    # write the aliases the papers teach

Every run scores the pages twice: RAW (the dates exactly as the model read
them) and DATE-CHECKED (a page whose date is implausible takes the first date
`paper_fields.check_paper_date` suggests — what a store keeper confirming the
first suggestion gets). It prints a per-page date table and writes the
committed scorecard `tests/ai_eval/ocr/scorecard.json` (counts only — no names).

The operator's 11 photos of the *Safety & Production Consumables* papers for
1–4 Oct 2026 are the baseline set (Q21-1). Their answers are already in
`CNCEC_Inventory.xlsx` → Consumption Log.

⚠️ THE WORKBOOK IS NOT LINE-FOR-LINE. It holds ONE row per (date, item, Work
Type, tank) with the quantities summed across every paper of that day: the
paper's *Remarks* column IS the workbook's *Work Type* (`R/L`, `B/L`, `PU`,
`Blast`; a handwritten `PV` is `PU`, Q21-2), and a night-shift paper dated
`01/10` is booked on 1 Oct. So the comparison is per DAY TOTAL, never per line.
Surface Shield items are not written on these papers (the operator enters them
directly), so they are left out of the truth.

⚠️ PRIVACY (Q21-6). The photos carry workers' names. They live in git-ignored
`data-archive/ocr_ground_truth/`, the cache in git-ignored `.cache/ocr_eval/`,
and NOTHING this tool prints or writes to the scorecard contains a name. Vision
runs LOCALLY only: the cloud fallback is forced off for this process.

⚠️ NOT A CI GATE (P10-7): the vision answer is stochastic and takes ≈ 3.5
minutes a page here. The deterministic half (aggregation, normalisation, the
matcher) is pinned by service-test suite 21D on frozen OCR JSON.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import collections
import datetime as _dt
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
os.environ.setdefault("GI_DOTENV", "0")                 # no cloud key, ever
os.environ["GI_AI_VISION_CLOUD_FALLBACK"] = "0"

DEFAULT_SET = _ROOT / "data-archive" / "ocr_ground_truth" / "2026-10-01_04"
CACHE = _ROOT / ".cache" / "ocr_eval"
SCORECARD = _ROOT / "tests" / "ai_eval" / "ocr" / "scorecard.json"

# ── normalisation shared with the app (backend/api/ai/paper_fields.py) ───────
from backend.api.ai.paper_fields import WORK_TYPES, check_paper_date  # noqa: E402,F401
from backend.api.ai.paper_fields import norm_work_type as _app_work_type  # noqa: E402


def norm_work_type(v) -> str:
    """The app's spelling, plus one equivalence only the COMPARISON needs: the
    workbook writes the same work both "Blast" and "Blasting"."""
    w = _app_work_type(v)
    return "Blast" if w == "Blasting" else w

PHOTO_DAY = _dt.date(2026, 10, 6)          # the day the 11 photos were uploaded


def tank_key(v) -> str:
    """The comparable part of a tank number: its trailing digits ("K-TNK-071",
    "522-8k80-TNK-071" → "071"; "J050" → "J050" keeps its letter, a J-series
    sump is not a TNK)."""
    s = str(v or "").strip().upper()
    m = re.search(r"\bJ\s*0*(\d+)\b", s)
    if m:
        return f"J{int(m.group(1)):03d}"
    m = re.search(r"(\d+)\s*$", s)
    return m.group(1).zfill(3) if m else s


def aggregate(rows, *, sap="sap", date="date", wt="work_type", tank="tank", qty="qty"):
    out: dict[tuple, float] = collections.defaultdict(float)
    for r in rows:
        if not r.get(sap) or r.get(qty) is None:
            continue
        out[(r[date], str(r[sap]), norm_work_type(r[wt]), tank_key(r[tank]))] += float(r[qty])
    return dict(out)


def score(pred: dict, truth: dict) -> dict:
    """Day-total precision/recall on (date, SAP, work type, tank) keys, and on
    the coarser (date, SAP) — plus the quantity error where both have a key."""
    def coarse(d):
        c = collections.defaultdict(float)
        for (dt, sap, _w, _t), q in d.items():
            c[(dt, sap)] += q
        return c
    out = {}
    for name, p, t in (("fine", pred, truth), ("date_sap", coarse(pred), coarse(truth))):
        hit = set(p) & set(t)
        out[name] = {"pred": len(p), "truth": len(t), "hit": len(hit),
                     "precision": round(len(hit) / len(p), 3) if p else 0.0,
                     "recall": round(len(hit) / len(t), 3) if t else 0.0,
                     "qty_exact": sum(1 for k in hit if abs(p[k] - t[k]) < 1e-6),
                     "qty_abs_err": round(sum(abs(p[k] - t[k]) for k in hit), 3)}
    return out


# ── the workbook: truth + the catalogue the matcher sees ─────────────────────
def load_workbook(path: Path, dates: set[str]):
    import warnings

    import openpyxl
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    inv_rows = list(wb["Inventory"].iter_rows(min_row=1, values_only=True))
    hi = next(i for i, r in enumerate(inv_rows[:6]) if r and any(
        isinstance(c, str) and c.strip().upper() == "SAP CODE" for c in r))
    h = [str(c).strip() if c else "" for c in inv_rows[hi]]
    ix = {k: i for i, k in enumerate(h)}
    inventory, stock, category = [], {}, {}
    for r in inv_rows[hi + 1:]:
        sap = r[ix["SAP CODE"]]
        if sap in (None, ""):
            continue
        sap = str(int(sap)) if isinstance(sap, float) else str(sap).strip()
        inventory.append({"SAP_Code": sap, "Equipment_Description": r[ix["Equipment Description"]],
                          "UOM": r[ix["UOM"]], "Material_Code": r[ix.get("Material Code", 0)]})
        try:
            stock[sap] = float(r[ix["Current Stock"]] or 0)
        except (TypeError, ValueError):
            stock[sap] = 0.0
        category[sap] = str(r[ix["Category"]] or "")
    rows = list(wb["Consumption Log"].iter_rows(min_row=1, values_only=True))
    ch = [str(c).strip() if c else "" for c in rows[1]]
    cx = {k: i for i, k in enumerate(ch)}
    truth_rows = []
    for r in rows[2:]:
        d = r[0]
        if not isinstance(d, _dt.datetime) or d.date().isoformat() not in dates:
            continue
        sap = r[cx["SAP CODE"]]
        sap = str(int(sap)) if isinstance(sap, (int, float)) else str(sap or "").strip()
        if "surface" in category.get(sap, "").lower() or str(r[cx["type"]] or "").lower() == "surface shield":
            continue                         # never written on these papers (Q21-1)
        truth_rows.append({"date": d.date().isoformat(), "sap": sap, "qty": r[cx["Qty."]],
                           "work_type": r[cx["Work Type"]], "tank": r[cx["Tank No."]],
                           "desc": r[cx["Equipment Description"]]})
    wb.close()
    return inventory, stock, truth_rows


# ── vision, cached per image + prompt ────────────────────────────────────────
async def read_page(raw: bytes) -> tuple[str, float]:
    from backend.api.ai import client as aic
    from backend.api.ai import ocr
    from backend.api.ai import route as R
    if aic.vision_provider() != "ollama":
        raise SystemExit("❌ vision must be LOCAL for these photos (they show workers' names)")
    jpeg = ocr.prep_image_for_vision(raw)
    t0 = time.perf_counter()
    out = await R.call_vision("ocr_consumption", ocr.USER_PROMPTS["ocr_consumption"],
                              system=ocr.SYSTEM_PROMPTS["ocr_consumption"],
                              image_b64=base64.b64encode(jpeg).decode(),
                              image_tokens=ocr.estimate_image_tokens(jpeg), temperature=0.1)
    if out.error_class:
        raise RuntimeError(f"vision failed: {out.error_class}")
    return out.text, time.perf_counter() - t0


def cache_key(raw: bytes) -> str:
    from backend.api.ai import ocr
    h = hashlib.sha256(raw)
    h.update(ocr.SYSTEM_PROMPTS["ocr_consumption"].encode())
    h.update(ocr.USER_PROMPTS["ocr_consumption"].encode())
    return h.hexdigest()[:24]


def to_form(parsed: dict, form_id: str) -> dict:
    """The vision JSON → `handwritten.process_batch`'s form shape."""
    return {"form_id": form_id, "date_text": parsed.get("date_text"),
            "rows": [{"source_row_no": r.get("sno"), "received_by": r.get("issued_to"),
                      "tank_no": r.get("tank_no"), "product_name_raw": r.get("material_text"),
                      "qty": r.get("qty_text") if r.get("qty_text") not in (None, "") else r.get("quantity"),
                      "work_type": r.get("work_type"), "struck_through": r.get("struck_through")}
                     for r in parsed.get("rows", [])]}


def date_checked(forms: list[dict], today: _dt.date = PHOTO_DAY) -> tuple[list[dict], list[dict]]:
    """Each form with its date settled the way the app settles it: a plausible
    date stays; an implausible one takes the FIRST suggestion. Returns (forms,
    the per-page date table)."""
    out, table = [], []
    for f in forms:
        chk = check_paper_date(f.get("date_text"), today)
        used = chk["date_iso"] if chk["plausible"] else (
            chk["candidates"][0]["date_iso"] if chk["candidates"] else None)
        table.append({"page": f["form_id"], "read": chk["read"], "read_iso": chk["date_iso"],
                      "plausible": chk["plausible"],
                      "suggested": [c["label"] for c in chk["candidates"]], "used": used})
        out.append(dict(f, date_iso=used) if used else dict(f))
    return out, table


def predictions(forms: list[dict], inventory, stock, *, aliases=None, semantic=None,
                today: _dt.date = PHOTO_DAY):
    """The deterministic half: ditto, corrections, quantities, the date — then
    the Phase 21d matcher. Returns (pred_rows, per-row match records)."""
    from backend.api.ai import consumption_match as CM
    from backend.api.ai import handwritten as HW
    res = HW.process_batch(forms, inventory, stock, today=today)
    pred, recs = [], []
    for r in res["rows"]:
        m = CM.match(r.get("product_name_raw") or "", inventory, aliases=aliases, stock=stock,
                     semantic=semantic)
        recs.append({"date": r["date_iso"], "written": r.get("product_name_raw"), "state": m["state"],
                     "source": m["source"], "sap": m["sap"], "tank": r.get("tank_no"),
                     "work_type": r.get("work_type"), "qty": r.get("qty")})
        pred.append({"date": r["date_iso"], "sap": m["sap"], "qty": r.get("qty"),
                     "work_type": r.get("work_type"), "tank": r.get("tank_no")})
    return pred, recs


def propose_aliases(recs: list[dict], truth_rows: list[dict], inventory) -> list[dict]:
    """What the papers teach: for each written name, the truth SAP in the SAME
    (date, work type, tank) group that it most resembles — kept only when it is
    unambiguous across every occurrence."""
    from backend.api.ai import consumption_match as CM
    from backend.api.ai import fuzzy as FZ
    desc = {str(r["SAP_Code"]): str(r["Equipment_Description"] or "") for r in inventory}
    groups = collections.defaultdict(set)
    for t in truth_rows:
        groups[(t["date"], norm_work_type(t["work_type"]), tank_key(t["tank"]))].add(t["sap"])
    votes: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for r in recs:
        if not r["written"]:
            continue
        g = groups.get((r["date"], norm_work_type(r["work_type"]), tank_key(r["tank"])), set())
        if not g:
            continue
        best = max(g, key=lambda s: FZ._hybrid_score(r["written"], desc.get(s, "")))
        if FZ._hybrid_score(r["written"], desc.get(best, "")) >= 0.35:
            votes[CM.written_key(r["written"])][best] += 1
    out = []
    for key, c in sorted(votes.items()):
        (sap, n), *rest = c.most_common()
        if not rest or n >= 2 * rest[0][1]:
            out.append({"written_key": key, "SAP_Code": sap, "description": desc.get(sap, ""),
                        "seen": n})
    return out


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--images", default=str(DEFAULT_SET))
    ap.add_argument("--workbook", default=str(_ROOT / "CNCEC_Inventory.xlsx"))
    ap.add_argument("--rescore", action="store_true", help="never call vision; use the cache")
    ap.add_argument("--semantic", action="store_true", help="also measure the embedding layer")
    ap.add_argument("--aliases", help="JSON of learned aliases to apply (e.g. the proposed ones)")
    ap.add_argument("--propose-aliases", action="store_true")
    a = ap.parse_args()
    imgs = sorted(p for p in Path(a.images).glob("*") if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".heic"))
    if not imgs:
        print(f"❌ no photos in {a.images}")
        return 2
    CACHE.mkdir(parents=True, exist_ok=True)
    forms, timings = [], []
    for i, p in enumerate(imgs, 1):
        raw = p.read_bytes()
        cp = CACHE / f"{cache_key(raw)}.txt"
        if cp.exists():
            text = cp.read_text()
        elif a.rescore:
            print(f"  page {i}: not cached — skipped (--rescore)")
            continue
        else:
            print(f"  page {i}/{len(imgs)}: reading (≈ 3–4 min)…", flush=True)
            text, secs = await read_page(raw)
            cp.write_text(text)
            timings.append(round(secs, 1))
        from backend.api.ai import ocr
        parsed = ocr.parse_vision_reply("ocr_consumption", text)
        forms.append(to_form(parsed, f"page{i:02d}"))

    from backend.api.ai import handwritten as HW
    checked_forms, date_table = date_checked(forms)
    dates = {t["used"] for t in date_table if t["used"]}
    for f in forms:
        d, _flag = HW.parse_form_date(f.get("date_text"), PHOTO_DAY)
        if d:
            dates.add(d)
    inventory, stock, truth_rows = load_workbook(Path(a.workbook), dates)
    aliases = None
    if a.aliases:
        aliases = {x["written_key"]: {"SAP_Code": x["SAP_Code"], "confirmations": x.get("seen", 1)}
                   for x in json.loads(Path(a.aliases).read_text())}
    semantic = None
    if a.semantic:
        from backend.api.ai import consumption_semantic as SEM
        semantic = await SEM.build_local(inventory)
    pred, recs = predictions(forms, inventory, stock, aliases=aliases, semantic=semantic)
    truth = aggregate(truth_rows)
    sc = score(aggregate(pred), truth)
    pred_c, recs_c = predictions(checked_forms, inventory, stock, aliases=aliases, semantic=semantic)
    sc_c = score(aggregate(pred_c), truth)
    states = collections.Counter(r["state"] for r in recs)
    sources = collections.Counter(r["source"] for r in recs if r["source"])
    rows_total = len(recs)
    pages_dated = sum(1 for f in forms if HW.parse_form_date(f.get("date_text"), PHOTO_DAY)[0])
    card = {"at": _dt.datetime.now().isoformat(timespec="seconds"), "pages": len(forms),
            "pages_dated": pages_dated, "dates_used": sorted({t["used"] for t in date_table if t["used"]}),
            "rows": rows_total, "states": dict(states), "sources": dict(sources),
            "aliases_applied": bool(aliases), "semantic": bool(semantic),
            "day_totals_raw": sc, "day_totals_date_checked": sc_c,
            "pages_date_implausible": sum(1 for t in date_table if not t["plausible"]),
            "date_table": date_table, "vision_seconds": timings}
    # the confusion list carries WRITTEN PRODUCT NAMES only — never a person
    confusion = collections.Counter((r["written"], r["state"], r["sap"]) for r in recs)
    print(json.dumps({k: v for k, v in card.items() if k != "date_table"}, indent=1))
    print("\npage    read                plausible  suggested                        used")
    for t in date_table:
        print(f"  {t['page']:<6} {t['read']!s:<19} {('yes' if t['plausible'] else 'NO'):<10} "
              f"{', '.join(t['suggested']) or '—':<32} {t['used'] or '—'}")
    print("\nwritten name → state → SAP (count):")
    for (w, st, sap), n in sorted(confusion.items(), key=lambda x: -x[1])[:60]:
        print(f"  {n:>3} × {w!r:<28} {st:<9} {sap or '—'}")
    SCORECARD.parent.mkdir(parents=True, exist_ok=True)
    SCORECARD.write_text(json.dumps(card, indent=1))
    if a.propose_aliases:
        prop = propose_aliases(recs, truth_rows, inventory)
        out = CACHE / "proposed_aliases.json"
        out.write_text(json.dumps(prop, indent=1))
        print(f"\n▶ {len(prop)} alias(es) proposed → {out.relative_to(_ROOT)}")
        for x in prop:
            print(f"    {x['written_key']!r:<28} → {x['SAP_Code']} {x['description']} (seen {x['seen']}×)")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
