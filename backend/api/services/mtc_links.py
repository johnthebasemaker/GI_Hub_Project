"""
backend/api/services/mtc_links.py — Material Test Certificates from Drive →
the lots they certify, by batch (Phase 22c, rulings Q22-10/11, plan §1.2 22c).

The *MTC* folder holds the suppliers' certificates. Each one is read twice:

  1. its NAME — `… (BNO-3633,3542,3504).pdf`, `… Batch - 2802, 2826 ….pdf`,
     `CUMI FN 2802.jpeg`, `AB250001410_4924-A1-3.1-BC 3004.pdf`;
  2. its TEXT LAYER (pdfplumber, under a second; no vision model) — one block
     per page: `Product Name`, `Batch No`, `D.O.M`, `D.O.E` (CUMI) or
     `Artikel / Product`, `Chargen Nr. / A1 – 4924`, `Herstellungsdatum` (TIP TOP).

A candidate (batch + product) links to a lot ONLY when both agree:

  · the BATCH is the lot's number exactly — or, for a supplier batch such as
    `A1 – 4924`, the last six characters of the workbook's long batch
    (`5254143A14924`). A bare 4-digit tail is NEVER enough: on the real folder
    `0426` "matched" `A20426` (plan §0.3);
  · the PRODUCT names the lot's material (`Cement BC 3004` ↔ `BC 3004(9KG)`,
    `Härter E40` ↔ `HARDNER E40 30GR`), and a thickness in it (3 MM) is the
    item's own — batch 3504 exists for the 3 MM AND the 5 MM PU.

⚠️ AN EXACT LINK CLEARS THE ISSUE GATE. `quality.assert_mtc_for_issue` looks
for a certificate per material and site, so a linked file is filed as an
`mtc_documents` row (`drive_file_id`, `disk_path` = the cached copy) exactly as
if the site had uploaded it. That is why anything less than exact — a batch
with no product to check it against, or a file someone assigns by hand (AR
bricks by container, CHEMOLINE rolls by order — Q22-10) — is only PROPOSED
(`mtc_assignments`) and becomes a certificate when QC confirms it.

EXPIRY (Q22-11). A certificate's expiry (D.O.E) is the real one: it fills the
lot's `Expiry_Date` with `Expiry_Source = 'mtc'`, above the Lot Register file
and a derived date. An expiry typed in the app (`'app'` — a retest) still
wins; a difference is listed, never overwritten. The manufacture date fills an
empty `MFD_Date`.
"""
from __future__ import annotations

import datetime as _dt
import re
import unicodedata
from pathlib import Path
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

SYSTEM_USER = "drive-sync"
MIN_PRODUCT_OVERLAP = 6


# ── normalising ──────────────────────────────────────────────────────────────
_ALIASES = ((r"\bH(?:Ä|AE|A)RTER\b", "HARDNER"), (r"\bHARDENER\b", "HARDNER"),
            (r"\bCH\.?(?=\s*4\s*E)", "CHEMOLINE"), (r"\bCUMI\s+FN\b", "CUMIFURAN FN"),
            (r"\bBRICK\b", "BRICKS"))


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s or "")).upper()
    s = s.replace("Ä", "AE")
    return "".join(c for c in s if not unicodedata.combining(c))


def norm(s) -> str:
    """Upper-case letters and digits only."""
    return re.sub(r"[^A-Z0-9]", "", _fold(s))


def product_norm(s) -> str:
    t = _fold(s)
    for pat, rep in _ALIASES:
        t = re.sub(pat, rep, t)
    return re.sub(r"[^A-Z0-9]", "", t)


_MM = re.compile(r"(?<![\dX.])(\d+)\s*\.?\s*MM\b")


def thicknesses(s) -> set[str]:
    return set(_MM.findall(_fold(s)))


def _lcs(a: str, b: str) -> int:
    """Longest common substring length (short strings — fine as O(n·m))."""
    best = 0
    prev = [0] * (len(b) + 1)
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        for j in range(1, len(b) + 1):
            if a[i - 1] == b[j - 1]:
                cur[j] = prev[j - 1] + 1
                best = max(best, cur[j])
        prev = cur
    return best


def family_ok(product: Optional[str], description: Optional[str]) -> bool:
    """Does the certificate's product name the lot's material?"""
    if not product or not description:
        return False
    p, d = product_norm(product), product_norm(description)
    if not p or not d:
        return False
    if _lcs(p, d) < min(MIN_PRODUCT_OVERLAP, len(p)):
        return False
    tp, td = thicknesses(product), thicknesses(description)
    return not (tp and td and not tp <= td)


_SUPPLIER_BATCH = re.compile(r"^[A-Z]\d{5,}$")


def batch_matches(batch: str, lot: str) -> bool:
    """Exact, or a supplier batch (letter + ≥5 digits, e.g. A14924) that is the
    tail of the workbook's long batch. Never a bare digits-only tail."""
    b, lo = norm(batch), norm(lot)
    if not b or not lo:
        return False
    if b == lo:
        return True
    return bool(_SUPPLIER_BATCH.match(b)) and lo.endswith(b)


# ── reading a certificate ────────────────────────────────────────────────────
def _date(s: Optional[str]) -> Optional[_dt.date]:
    """Day first: 16/03/2026 · 05.12.24 · 2026-03-16."""
    if not s:
        return None
    s = s.strip()
    m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        y, mo, d = (int(x) for x in m.groups())
    else:
        m = re.match(r"(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})", s)
        if not m:
            return None
        d, mo, y = (int(x) for x in m.groups())
        if y < 100:
            y += 2000
    try:
        return _dt.date(y, mo, d)
    except ValueError:
        return None


_DATE = r"(\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}|\d{4}-\d{1,2}-\d{1,2})"


def parse_name(name: str) -> dict:
    """{"batches": [...], "product": str | None} from the file name alone."""
    n = re.sub(r"\.\w{2,5}$", "", name).strip()
    batches: list[str] = []
    product: Optional[str] = None
    m = re.search(r"\(\s*BNO\s*-\s*([\d,\s]+)\)", n, re.I)
    if m:
        batches += [x.strip() for x in m.group(1).split(",") if x.strip()]
        product = n[:m.start()]
    m = re.search(r"Batch\s*-\s*([\d,\s]+)$", n, re.I)
    if m:
        batches += [x.strip() for x in m.group(1).split(",") if x.strip()]
        product = n[:m.start()]
    m = re.match(r"^CUMI\s+FN\s+(\d{3,6})$", n, re.I)
    if m:
        batches.append(m.group(1))
        product = "CUMIFURAN FN POWDER"
    m = re.search(r"(\d{4})-([A-Z]\d)-3\.1-(.+)$", n, re.I)
    if m:
        batches.append(m.group(2).upper() + m.group(1))
        product = m.group(3)
    m = re.search(r"Filler[\s_]+([A-Z]?\d{4})(?:_(\d{4}))?", n, re.I)
    if m and not batches:
        batches += [x for x in m.groups() if x]
        product = "CARBON FILLER"
    if product is None:
        product = n
    product = re.sub(r"^(?:AB\d+_|GI-|MTC\s*-\s*)", "", product.strip(), flags=re.I)
    product = re.sub(r"^\d+\s*1-", "", product).strip(" -_")
    return {"batches": batches, "product": product or None}


_PAGE_PRODUCT = (re.compile(r"Product\s*Name\s*:\s*(.+)", re.I),
                 re.compile(r"Artikel\s*/\s*Product\s+(.+)", re.I))
_PAGE_BATCH = (re.compile(r"Chargen\s*Nr\.?\s*/\s*([A-Z]\d)\s*[–\-]\s*(\d{4})", re.I),
               re.compile(r"Batch\s*(?:No|Number)\.?\s*[:/]\s*([A-Z0-9][A-Z0-9 \-]*)", re.I),
               re.compile(r"Lot\s*(?:No|Number)\.?\s*[:/]\s*([A-Z0-9][A-Z0-9 \-]*)", re.I))
_PAGE_MFD = (re.compile(r"D\.?\s*O\.?\s*M\.?\s*:?\s*" + _DATE, re.I),
             re.compile(r"Herstellungsdatum\s*/\s*" + _DATE, re.I),
             re.compile(r"(?:Date\s+of\s+(?:production|manufactur\w*)|Mfg\.?\s*Date|MFD)\s*[:/]?\s*" + _DATE, re.I))
_PAGE_EXP = (re.compile(r"D\.?\s*O\.?\s*E\.?\s*:?\s*" + _DATE, re.I),
             re.compile(r"(?:Exp(?:iry)?\.?\s*(?:Date)?|Best\s+before|Use\s+before)\s*[:/]?\s*" + _DATE, re.I))


def parse_page(t: str) -> Optional[dict]:
    """One certificate block: {batch, product, mfd, expiry} or None."""
    batch = None
    for rx in _PAGE_BATCH:
        m = rx.search(t)
        if m:
            batch = (m.group(1).upper() + m.group(2)) if m.lastindex == 2 \
                else (m.group(1).strip().split() or [""])[0]
            break
    if not batch:
        return None
    product = None
    for rx in _PAGE_PRODUCT:
        m = rx.search(t)
        if m:
            product = m.group(1).strip()
            break
    mfd = next((_date(m.group(1)) for rx in _PAGE_MFD for m in [rx.search(t)] if m), None)
    exp = next((_date(m.group(1)) for rx in _PAGE_EXP for m in [rx.search(t)] if m), None)
    return {"batch": batch, "product": product, "mfd": mfd, "expiry": exp}


def parse_pdf(data: bytes) -> list[dict]:
    """Every certificate block in a PDF's text layer ([] for a scan)."""
    import io
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            pages = [(p.extract_text() or "") for p in pdf.pages[:40]]
    except Exception:  # noqa: BLE001 — not a readable PDF: the name still counts
        return []
    return [b for b in (parse_page(t) for t in pages) if b]


def candidates(name: str, data: Optional[bytes]) -> list[dict]:
    """(batch, product, mfd, expiry, source) from the text first, then the name."""
    out: list[dict] = []
    if data and name.lower().endswith(".pdf"):
        for b in parse_pdf(data):
            out.append({**b, "source": "text"})
    nm = parse_name(name)
    have = {norm(c["batch"]) for c in out}
    for b in nm["batches"]:
        if norm(b) not in have:
            out.append({"batch": b, "product": nm["product"], "mfd": None, "expiry": None,
                        "source": "name"})
    for c in out:                       # a text block without a product → the name's
        if not c.get("product"):
            c["product"] = nm["product"]
    return out


# ── linking ──────────────────────────────────────────────────────────────────
async def _lots(session: AsyncSession) -> list[dict]:
    return [dict(r) for r in (await session.execute(text('''
        SELECT l.id, l."SAP_Code", l."Lot_Number", COALESCE(l."Site_ID", 'HQ') AS site,
               l."Expiry_Date", l."Expiry_Source", l."MFD_Date",
               i."Equipment_Description" AS description, i."Material_Code"
        FROM lots l LEFT JOIN LATERAL (
            SELECT "Equipment_Description", "Material_Code" FROM inventory i
            WHERE TRIM(i."SAP_Code") = TRIM(l."SAP_Code") LIMIT 1) i ON TRUE'''))).mappings().all()]


def match_lots(cands: list[dict], lots: list[dict]) -> tuple[list[tuple], list[tuple]]:
    """([(cand, lot)] exact, [(cand, lot)] suggested): exact = batch AND
    product agree; suggested = the batch matches but there is no product to
    check it against."""
    exact, suggested = [], []
    for c in cands:
        hits = [lo for lo in lots if batch_matches(c["batch"], lo["Lot_Number"])]
        fam = [lo for lo in hits if family_ok(c.get("product"), lo.get("description"))]
        # A product that names no thickness while the batch spans two (3504 is a
        # 3 MM AND a 5 MM PU batch) cannot say which one it certifies: QC decides.
        spans = {frozenset(thicknesses(lo.get("description"))) for lo in fam}
        if fam and not thicknesses(c.get("product")) and len(spans) > 1:
            suggested += [(c, lo) for lo in fam]
            continue
        exact += [(c, lo) for lo in fam]
        if not c.get("product"):
            suggested += [(c, lo) for lo in hits]
    return exact, suggested


async def file_certificate(session: AsyncSession, *, drive_file_id: int, name: str,
                           mime: Optional[str], cache_path: str, lot: dict,
                           by: str) -> bool:
    """Make the file the certificate of this lot (idempotent). True when new."""
    got = (await session.execute(text(
        'SELECT 1 FROM mtc_documents WHERE drive_file_id = :f AND TRIM("SAP_Code") = TRIM(:s) '
        'AND "Lot_Number" = :l'), {"f": drive_file_id, "s": lot["SAP_Code"],
                                   "l": lot["Lot_Number"]})).first()
    if got:
        return False
    await session.execute(text('''
        INSERT INTO mtc_documents ("Site_ID", "SAP_Code", "Material_Code", "Lot_Number",
            mtc_number, file_name, mime_type, disk_path, status, submitted_by, drive_file_id)
        VALUES (:site, :sap, :mat, :lot, :num, :name, :mime, :path, 'attached', :by, :f)'''),
        {"site": lot["site"], "sap": lot["SAP_Code"], "mat": lot.get("Material_Code"),
         "lot": lot["Lot_Number"], "num": f"Drive: {name}"[:200], "name": name,
         "mime": mime or ("application/pdf" if name.lower().endswith(".pdf") else None),
         "path": cache_path, "by": by, "f": drive_file_id})
    return True


async def apply_dates(session: AsyncSession, lot: dict, cand: dict) -> Optional[dict]:
    """The certificate's expiry onto the lot (Q22-11) and its MFD when empty.
    Returns a disagreement when an app-typed expiry differs, else None."""
    exp, mfd = cand.get("expiry"), cand.get("mfd")
    if mfd and not lot.get("MFD_Date"):
        await session.execute(text('UPDATE lots SET "MFD_Date" = :m WHERE id = :i'),
                              {"m": mfd.isoformat(), "i": lot["id"]})
        lot["MFD_Date"] = mfd.isoformat()
    if not exp:
        return None
    cur = str(lot.get("Expiry_Date") or "")[:10]
    if lot.get("Expiry_Source") == "app":
        if cur and cur != exp.isoformat():
            return {"SAP_Code": lot["SAP_Code"], "Lot_Number": lot["Lot_Number"],
                    "app_expiry": cur, "mtc_expiry": exp.isoformat()}
        return None
    if cur != exp.isoformat() or lot.get("Expiry_Source") != "mtc":
        await session.execute(text(
            'UPDATE lots SET "Expiry_Date" = :e, "Expiry_Source" = \'mtc\' WHERE id = :i'),
            {"e": exp.isoformat(), "i": lot["id"]})
        lot["Expiry_Date"], lot["Expiry_Source"] = exp.isoformat(), "mtc"
    return None


async def link_mtc(session: AsyncSession, *, read_bytes=None) -> dict:
    """Every cached MTC file: exact matches become certificates; batch-only
    matches are proposed for QC (never filed); the rest wait for a person."""
    read_bytes = read_bytes or (lambda p: Path(p).read_bytes())
    files = (await session.execute(text(
        "SELECT id, name, mime, cache_path FROM drive_files WHERE kind = 'mtc' "
        "AND removed_at IS NULL AND cache_path IS NOT NULL ORDER BY name"))).mappings().all()
    lots = await _lots(session)
    out = {"linked": 0, "suggested": 0, "unlinked": 0, "certificates_new": 0,
           "disagreements": []}
    for f in files:
        try:
            data = read_bytes(f["cache_path"])
        except OSError:
            data = None
        cands = candidates(f["name"], data)
        exact, suggested = match_lots(cands, lots)
        for c, lo in exact:
            if await file_certificate(session, drive_file_id=f["id"], name=f["name"],
                                      mime=f["mime"], cache_path=f["cache_path"], lot=lo,
                                      by=SYSTEM_USER):
                out["certificates_new"] += 1
            d = await apply_dates(session, lo, c)
            if d:
                out["disagreements"].append({**d, "file": f["name"]})
        for c, lo in suggested:
            await session.execute(text('''
                INSERT INTO mtc_assignments (drive_file_id, "SAP_Code", "Lot_Number", "Site_ID",
                    source, status, proposed_by, batch_text)
                SELECT :f, :s, :l, :site, 'suggested', 'proposed', :by, :b
                WHERE NOT EXISTS (SELECT 1 FROM mtc_assignments WHERE drive_file_id = :f
                    AND "SAP_Code" = :s AND "Lot_Number" = :l)'''),
                {"f": f["id"], "s": lo["SAP_Code"], "l": lo["Lot_Number"], "site": lo["site"],
                 "by": SYSTEM_USER, "b": c["batch"]})
        manual = (await session.execute(text(
            "SELECT COUNT(*) FROM mtc_assignments WHERE drive_file_id = :f AND status = 'confirmed'"),
            {"f": f["id"]})).scalar() or 0
        status = ("linked" if exact or manual else
                  "suggested" if suggested else "unlinked")
        out[status] += 1
        key = ",".join(sorted({c["batch"] for c in cands}))[:200] or None
        await session.execute(text(
            "UPDATE drive_files SET parsed_key = :k, link_status = :s WHERE id = :i"),
            {"k": key, "s": status, "i": f["id"]})
    return out


async def confirm_assignment(session: AsyncSession, assignment_id: int, *, user: str,
                             read_bytes=None) -> dict:
    """QC confirms a proposed MTC → it becomes the lot's certificate, and the
    certificate's dates are read for THIS lot."""
    a = (await session.execute(text(
        "SELECT a.*, f.name, f.mime, f.cache_path FROM mtc_assignments a "
        "JOIN drive_files f ON f.id = a.drive_file_id WHERE a.id = :i"), {"i": assignment_id})
    ).mappings().first()
    if a is None or a["status"] != "proposed":
        return {"ok": False, "reason": "not a proposed assignment"}
    lot = next((lo for lo in await _lots(session)
                if lo["SAP_Code"] == a["SAP_Code"] and lo["Lot_Number"] == a["Lot_Number"]), None)
    if lot is None:
        return {"ok": False, "reason": "that lot no longer exists"}
    await file_certificate(session, drive_file_id=a["drive_file_id"], name=a["name"],
                           mime=a["mime"], cache_path=a["cache_path"], lot=lot, by=user)
    read_bytes = read_bytes or (lambda p: Path(p).read_bytes())
    try:
        cands = candidates(a["name"], read_bytes(a["cache_path"]))
    except OSError:
        cands = []
    c = next((c for c in cands if batch_matches(c["batch"], lot["Lot_Number"])), None)
    dis = await apply_dates(session, lot, c) if c else None
    await session.execute(text(
        "UPDATE mtc_assignments SET status = 'confirmed', decided_by = :u, "
        "decided_at = CURRENT_TIMESTAMP WHERE id = :i"), {"u": user, "i": assignment_id})
    await session.execute(text(
        "UPDATE drive_files SET link_status = 'linked' WHERE id = :f"), {"f": a["drive_file_id"]})
    return {"ok": True, "disagreement": dis}


async def overview(session: AsyncSession, site_id: Optional[str]) -> dict:
    """For Lots & Expiry: files that need a person, proposals for QC, and the
    Surface Shield lots with no certificate."""
    # `is not None`: a scoped account with no site resolves to '' and must see nothing
    w = 'AND COALESCE(l."Site_ID", \'HQ\') = :site' if site_id is not None else ""
    prm = {"site": site_id} if site_id is not None else {}
    files = [dict(r) for r in (await session.execute(text(
        "SELECT id, name, link_status, parsed_key FROM drive_files WHERE kind = 'mtc' "
        "AND removed_at IS NULL ORDER BY name"))).mappings().all()]
    linked = (await session.execute(text(
        'SELECT m.drive_file_id, m."SAP_Code", m."Lot_Number" FROM mtc_documents m '
        "WHERE m.drive_file_id IS NOT NULL"))).all()
    by_file: dict[int, list] = {}
    for fid, sap, lot in linked:
        by_file.setdefault(fid, []).append({"SAP_Code": sap, "Lot_Number": lot})
    for f in files:
        f["lots"] = by_file.get(f["id"], [])
    proposed = [dict(r) for r in (await session.execute(text(
        'SELECT a.id, a.drive_file_id, f.name AS file, a."SAP_Code", a."Lot_Number", a.source, '
        "a.proposed_by, a.batch_text FROM mtc_assignments a JOIN drive_files f ON f.id = a.drive_file_id "
        "WHERE a.status = 'proposed' ORDER BY f.name, a.\"SAP_Code\""))).mappings().all()]
    missing = [dict(r) for r in (await session.execute(text(f'''
        SELECT l."SAP_Code", l."Lot_Number", COALESCE(l."Site_ID", 'HQ') AS site,
               i."Equipment_Description" AS description
        FROM lots l JOIN inventory i ON TRIM(i."SAP_Code") = TRIM(l."SAP_Code")
             AND COALESCE(i."Site_ID", '') = COALESCE(l."Site_ID", '')
        WHERE i."Category" ILIKE '%surface%' {w}
          AND NOT EXISTS (SELECT 1 FROM mtc_documents m WHERE TRIM(m."SAP_Code") = TRIM(l."SAP_Code")
                          AND m."Lot_Number" = l."Lot_Number")
        ORDER BY i."Equipment_Description", l."Lot_Number"'''), prm)).mappings().all()]
    return {"files": files, "proposed": proposed, "lots_without_mtc": missing}
