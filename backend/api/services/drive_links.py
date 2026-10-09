"""
backend/api/services/drive_links.py — what a Drive file's NAME links to
(Phase 22, plan §1.2).

`drive_files` is filled by the Drive sync (22a). This module reads each file's
name and writes `parsed_key` / `parsed_date` / `link_status`, so the app can
find "the DN photo of this receipt", "the certificate of this batch" and "the
request this line came from". Every parser is idempotent over the whole table
and runs after each crawl (`relink_all`).

    22b  DN for CNCEC → receipts / returns by DN number  (link_dn)
    22c  MTC          → lots by batch                    (services/mtc_links.py)
    22d  Pending      → material requests                (services/requests_sync.py)

⚠️ NOTHING LINKS SILENTLY WHEN THE MATCH IS NOT EXACT (plan §7): an unmatched
file is listed as unlinked, never guessed.

── 22b: the DN keys ────────────────────────────────────────────────────────
A receipt's `DN_No` and a file's name are both reduced to ONE key:

    "13021", "DN# 13021-03052026.jpeg", "DN# 13021 - 1.jpeg"  → dn:13021
    "CP 8", "Cash Purchase 8.jpeg"                              → cp:8
    "GI/RLP/SAR-348", "DN# GI-RLP-SAR-348.jpeg"                 → dn:GIRLPSAR348
    Return Log "24", "RDN# 024.jpeg", "Return DN#024 (…).xlsx"  → rdn:24

The Receipt Log's **DN. Copy** column already names a file
(`DN for CNCEC\\DN# 15623-29042026.pdf`, 56 rows); that exact file always links.

── 22b: WD — receipts WITHOUT a delivery note (ruling Q22-8) ────────────────
"WD" in the DN column means *Without Delivery Note*; local purchases arrive
the same way. Each such delivery gets an automatic unique number,
`WD-<site>-0001`, allocated ONCE and never renumbered (`receipt_wd`): the
lines of one delivery (same site, day, DN text and vehicle) share it. The
workbook is read-only to GI Hub, so the number lives here, not in Excel.
"""
from __future__ import annotations

import datetime as _dt
import re
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

LINKERS: list = []          # (name, async fn(session) -> dict) — appended by each slice


async def relink_all(session: AsyncSession) -> dict:
    out = {}
    for name, fn in LINKERS:
        out[name] = await fn(session)
    return out


# ── 22b: keys ─────────────────────────────────────────────────────────────────
_CP = re.compile(r"^\s*(?:CP|CASH\s*PURCHASE)\s*[-#.]?\s*(\d*)\s*$", re.I)
_NO_DN = {"", "WD", "W/D", "W.D", "NA", "N/A", "NIL", "-", "--", "WITHOUT DN",
          "WITHOUT DELIVERY NOTE", "NO DN"}


def dn_key(raw) -> Optional[str]:
    """The key a receipt's DN number links by, or None when it is not a DN
    (blank, WD, or words without a digit — "From SAR")."""
    if isinstance(raw, float) and raw.is_integer():
        raw = int(raw)
    s = str(raw if raw is not None else "").strip()
    if s.upper() in _NO_DN:
        return None
    m = _CP.match(s)
    if m:
        return f"cp:{int(m.group(1))}" if m.group(1) else "cp:"
    if not re.search(r"\d", s):
        return None
    if re.fullmatch(r"[\d\s.]+", s):
        return f"dn:{int(re.sub(r'\D', '', s))}"
    return "dn:" + re.sub(r"[^A-Z0-9]", "", s.upper())


def rdn_key(raw) -> Optional[str]:
    """A return's DN number → rdn:<n> (the last number in it: "24",
    "GI/RL/CNCEC-024" and "RDN 024" are the same return DN)."""
    if isinstance(raw, float) and raw.is_integer():
        raw = int(raw)
    m = re.findall(r"\d+", str(raw if raw is not None else ""))
    return f"rdn:{int(m[-1])}" if m else None


def is_without_dn(raw) -> bool:
    return dn_key(raw) is None


_DN_FILE = re.compile(r"^\s*DN\s*#\s*(?P<no>.+?)(?:-(?P<d>\d{8}))?(?:\s+-\s*\d+)?\s*\.+\w+$", re.I)
_RDN_FILE = re.compile(r"^\s*(?:RDN|RETURN\s*DN)\s*#\s*(?P<no>\d+)", re.I)
_CP_FILE = re.compile(r"^\s*CASH\s+PURCHASE\s*(?P<no>\d*)\s*\.\w+$", re.I)


def dn_file_key(name: str) -> tuple[Optional[str], Optional[_dt.date]]:
    """(key, date in the name) for a file in *DN for CNCEC*; (None, None) for
    a photo that is not a DN (equipment, "Other Cash Purchase")."""
    m = _RDN_FILE.match(name)
    if m:
        return f"rdn:{int(m.group('no'))}", None
    m = _CP_FILE.match(name)
    if m:
        return (f"cp:{int(m.group('no'))}" if m.group("no") else "cp:"), None
    m = _DN_FILE.match(name)
    if not m:
        return None, None
    d = None
    if m.group("d"):
        g = m.group("d")
        try:
            d = _dt.date(int(g[4:]), int(g[2:4]), int(g[:2]))
        except ValueError:
            d = None
    return dn_key(m.group("no").strip().rstrip(".")), d


def copy_basename(dn_copy) -> Optional[str]:
    """`Mob. DN for CNCEC\\DN# 15623-29042026.pdf` → `dn# 15623-29042026.pdf`."""
    s = str(dn_copy or "").strip()
    if not s:
        return None
    return re.split(r"[\\/]", s)[-1].strip().lower() or None


# ── 22b: link + WD numbers ────────────────────────────────────────────────────
async def link_dn(session: AsyncSession) -> dict:
    """Parse every DN-folder file name, mark it linked when a receipt or
    return names it, and give every without-DN delivery its WD number."""
    files = (await session.execute(text(
        "SELECT id, name FROM drive_files WHERE kind = 'dn' AND removed_at IS NULL"))).all()
    rec = (await session.execute(text('SELECT "DN_No", "DN_Copy" FROM receipts'))).all()
    ret = (await session.execute(text('SELECT "DN_No" FROM returns'))).all()
    rec_keys = {dn_key(r[0]) for r in rec} - {None}
    copies = {copy_basename(r[1]) for r in rec} - {None}
    ret_keys = {rdn_key(r[0]) for r in ret} - {None}
    out = {"linked": 0, "unlinked": 0, "other": 0}
    for fid, name in files:
        key, day = dn_file_key(name)
        if key is None:
            status = "other"
        elif key.startswith("rdn:"):
            status = "linked" if key in ret_keys else "unlinked"
        else:
            status = "linked" if (key in rec_keys or name.strip().lower() in copies) else "unlinked"
        out[status] += 1
        await session.execute(text(
            "UPDATE drive_files SET parsed_key = :k, parsed_date = :d, link_status = :s "
            "WHERE id = :i"), {"k": key, "d": day, "s": status, "i": fid})
    out["wd_new"] = await assign_wd_numbers(session)
    return out


def _wd_group(site, day, raw, vehicle) -> str:
    return "|".join([str(site or ""), str(day or "")[:10], str(raw or "").strip().upper(),
                     str(vehicle or "").strip().upper()])


async def assign_wd_numbers(session: AsyncSession) -> int:
    """Every receipt without a DN gets the WD number of its delivery (same
    site, day, DN text, vehicle). Numbers are allocated once, per site, in
    date order, and never renumbered. Returns how many receipts got one now."""
    rows = (await session.execute(text('''
        SELECT r.id, COALESCE(r."Site_ID", 'HQ') AS site, r."Date", r."DN_No", r."Vehicle_No"
        FROM receipts r ORDER BY r."Date", r.id'''))).mappings().all()
    have = {r["receipt_id"]: (r["wd_no"], r["group_key"]) for r in (await session.execute(text(
        "SELECT receipt_id, wd_no, group_key FROM receipt_wd"))).mappings().all()}
    by_group = {g: no for no, g in have.values()}
    nxt: dict[str, int] = {}
    for site, n in (await session.execute(text(
            "SELECT \"Site_ID\", MAX(CAST(split_part(wd_no, '-', 3) AS INTEGER)) "
            "FROM receipt_wd GROUP BY 1"))).all():
        nxt[site] = int(n or 0)
    new = 0
    for r in rows:
        if r["id"] in have or not is_without_dn(r["DN_No"]):
            continue
        g = _wd_group(r["site"], r["Date"], r["DN_No"], r["Vehicle_No"])
        no = by_group.get(g)
        if no is None:
            nxt[r["site"]] = nxt.get(r["site"], 0) + 1
            no = f"WD-{r['site']}-{nxt[r['site']]:04d}"
            by_group[g] = no
        await session.execute(text(
            'INSERT INTO receipt_wd (receipt_id, "Site_ID", wd_no, group_key) '
            "VALUES (:i, :s, :n, :g) ON CONFLICT (receipt_id) DO NOTHING"),
            {"i": r["id"], "s": r["site"], "n": no, "g": g})
        new += 1
    return new


async def ledger_docs(session: AsyncSession, kind: str, site_id: Optional[str]) -> dict:
    """{row id: {"files": [{id, name, mime}], "wd": "WD-…" | None}} for every
    receipt (or return) of the site that has a DN file or a WD number — what
    Records → Receipts / Returns shows in its DN column."""
    w = 'WHERE COALESCE(x."Site_ID", \'HQ\') = :site' if site_id is not None else ""
    prm = {"site": site_id} if site_id is not None else {}
    files = (await session.execute(text(
        "SELECT id, name, mime, parsed_key FROM drive_files "
        "WHERE kind = 'dn' AND removed_at IS NULL AND cache_path IS NOT NULL"))).mappings().all()
    by_key: dict[str, list] = {}
    by_name: dict[str, dict] = {}
    for f in files:
        d = {"id": f["id"], "name": f["name"], "mime": f["mime"]}
        if f["parsed_key"]:
            by_key.setdefault(f["parsed_key"], []).append(d)
        by_name[f["name"].strip().lower()] = d
    out: dict[int, dict] = {}
    if kind == "receipts":
        rows = (await session.execute(text(
            f'SELECT x.id, x."DN_No", x."DN_Copy", w.wd_no FROM receipts x '
            f'LEFT JOIN receipt_wd w ON w.receipt_id = x.id {w}'), prm)).mappings().all()
        for r in rows:
            fs = list(by_key.get(dn_key(r["DN_No"]) or "", []))
            cp = by_name.get(copy_basename(r["DN_Copy"]) or "")
            if cp and cp not in fs:
                fs.insert(0, cp)
            if fs or r["wd_no"]:
                out[r["id"]] = {"files": sorted(fs, key=lambda f: f["name"]), "wd": r["wd_no"]}
    else:
        rows = (await session.execute(text(
            f'SELECT x.id, x."DN_No" FROM returns x {w}'), prm)).mappings().all()
        for r in rows:
            fs = by_key.get(rdn_key(r["DN_No"]) or "", [])
            if fs:
                out[r["id"]] = {"files": sorted(fs, key=lambda f: f["name"]), "wd": None}
    return out


async def dn_coverage(session: AsyncSession) -> dict:
    """For the Drive card: receipt DNs with no file, files with no receipt
    (Q22-7: listed, never guessed), and how many deliveries are WD."""
    rec = (await session.execute(text('SELECT DISTINCT "DN_No" FROM receipts'))).all()
    keys: dict[str, str] = {}
    for (raw,) in rec:
        k = dn_key(raw)
        if k:
            keys.setdefault(k, str(int(raw) if isinstance(raw, float) and raw.is_integer() else raw))
    files = (await session.execute(text(
        "SELECT name, parsed_key, link_status FROM drive_files "
        "WHERE kind = 'dn' AND removed_at IS NULL"))).mappings().all()
    fkeys = {f["parsed_key"] for f in files if f["parsed_key"]}
    no_file = sorted(v for k, v in keys.items() if k not in fkeys)
    unlinked = sorted(f["name"] for f in files if f["link_status"] == "unlinked")
    wd = (await session.execute(text("SELECT COUNT(DISTINCT wd_no), COUNT(*) FROM receipt_wd"))).one()
    return {"receipt_dns_without_file": no_file, "files_without_receipt": unlinked,
            "wd_deliveries": int(wd[0] or 0), "wd_lines": int(wd[1] or 0)}


def read_return_dn(data: bytes) -> Optional[dict]:
    """A return-DN workbook (`RDN# 024.xlsx`): {"ref", "lines", "total"} from
    its `S.No. / Item Description / Unit / Quantity` table — or None when the
    file is not one. The DN carries no SAP code, so the check is by count and
    total, never by item."""
    import io
    import warnings

    import openpyxl
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        rows = [tuple(r) for r in wb.active.iter_rows(values_only=True)]
        wb.close()
    except Exception:  # noqa: BLE001 — not a readable workbook
        return None
    ref, qi, lines, total = None, None, 0, 0.0
    for r in rows:
        cells = [str(c).strip() if c is not None else "" for c in r]
        low = [c.lower() for c in cells]
        if ref is None and "ref no." in low:
            nxt = [c for c in cells[low.index("ref no.") + 1:] if c]
            ref = nxt[0] if nxt else None
        if qi is None and "quantity" in low and any("description" in c for c in low):
            qi = low.index("quantity")
            continue
        if qi is not None:
            if any(c.startswith("total") or "nothing follows" in c for c in low):
                break
            try:
                q = float(r[qi]) if r[qi] not in (None, "") else None
            except (TypeError, ValueError):
                q = None
            if q is not None:
                lines += 1
                total += q
    if qi is None:
        return None
    return {"ref": ref, "lines": lines, "total": round(total, 3)}


async def return_dn_check(session: AsyncSession) -> list[dict]:
    """Each cached return-DN workbook against the Return Log's rows of that DN
    (§11.1): a different line count or total is listed — reported, never fixed."""
    from pathlib import Path
    files = (await session.execute(text(
        "SELECT name, cache_path, parsed_key FROM drive_files WHERE kind = 'dn' "
        "AND removed_at IS NULL AND parsed_key LIKE 'rdn:%' AND lower(name) LIKE '%.xlsx' "
        "AND cache_path IS NOT NULL ORDER BY name"))).mappings().all()
    if not files:
        return []
    by_key: dict[str, list] = {}
    for rid, dn, qty, row in (await session.execute(text(
            'SELECT id, "DN_No", "Quantity", "Source_Row" FROM returns'))).all():
        k = rdn_key(dn)
        if k:
            by_key.setdefault(k, []).append((rid, float(qty or 0), row))
    out = []
    for f in files:
        try:
            dn = read_return_dn(Path(f["cache_path"]).read_bytes())
        except OSError:
            continue
        if not dn:
            continue
        log = by_key.get(f["parsed_key"], [])
        log_total = round(sum(q for _, q, _ in log), 3)
        if dn["lines"] != len(log) or abs(dn["total"] - log_total) > 1e-6:
            out.append({"file": f["name"], "dn": f["parsed_key"][4:], "dn_lines": dn["lines"],
                        "dn_total": dn["total"], "log_lines": len(log), "log_total": log_total,
                        "log_rows": sorted(r for _, _, r in log if r)})
    return out


LINKERS.append(("dn", link_dn))


# 22c — certificates → lots (imported last: mtc_links does not import this module)
from . import mtc_links as _mtc  # noqa: E402

LINKERS.append(("mtc", _mtc.link_mtc))

# 22d — the request workbooks (Pending Material Follow-up)
from . import requests_sync as _req  # noqa: E402

LINKERS.append(("pending", _req.link_requests))

# 23d — the material catalogue, the site equipment list, the Material Images folder
from . import catalogue as _cat  # noqa: E402

LINKERS.append(("catalogue", _cat.link_catalogue))
LINKERS.append(("equipment", _cat.link_equipment))
LINKERS.append(("images", _cat.link_images))
