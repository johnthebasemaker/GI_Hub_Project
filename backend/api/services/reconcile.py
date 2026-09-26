"""
backend/api/services/reconcile.py — Phase 14b: one quantity per bucket when a
QR execution entry and the Excel Consumption Log both speak for the same drums.

THE PROBLEM (PROPOSED_PHASE14_PLAN.md §1.4). A supervisor files a paper form (the
QR path, `execution.post_stock`) and the store keeper logs the same draw in the
workbook (the Excel sync). Before Phase 14b the sync merged the two only when
day, SAP and the Tank No. TEXT all matched and the quantities lined up. Four
paths deducted a drum twice: a tank spelled differently, a quantity split across
two workbook lines, a date one day off, and the Excel-FIRST order, which the
post never looked at.

THE BUCKET. (Site, work date, RESOLVED equipment tag, SAP). The Tank No. text is
resolved through the operator-owned alias table (`sme_tank_alias`, the same one
the Surface Shield routing uses) and an exact normalised match against
`sme_equipment`, in ONE function (`resolver`) that the sync and the post share,
so they cannot disagree about what "J091" is.

THE RULE (invariant L1, operator ruling Q14-1). The ledger holds ONE quantity per
bucket:

    target = max(QR, Excel)          never QR + Excel

and it is reached from SUMS, never from the previous decision, so every re-run
converges (L6):

  · the SYNC, for a bucket that holds a POSTED entry, does not insert its
    unclaimed workbook lines one by one. It computes

        delta = target − (Excel rows already in the ledger) − (rows the QR posted)

    and keeps exactly ONE delta row (label `XLSX:…:D`) at that quantity, linked
    to the entry — or none, when delta is 0.
  · the POST (`adopt_for_line`), for Excel rows already in the bucket, ADOPTS
    them (links them to the entry) and posts only QR − Excel, if positive.

Worked through for all three orders — QR first; Excel first then QR; Excel first,
QR, then more Excel — the formula lands on max(QR, Excel) each time. Suite 14B
pins every one of them.

THE STATUS a bucket carries: `matched` · `excel_extra` (the book shows more —
the extra is the delta row, no second SQM) · `qr_extra` (the form claims more
than the book — a CONFLICT the HOD is told about, corrected through the entry,
never by rewriting the entry's row: rule 3a) · `awaiting_excel` (posted from
the paper, not yet in the workbook) · `possible_duplicate` (an
unattached Excel draw of the same tag and SAP on the day before/after a QR
bucket — reported, never merged: ruling Q14-2).

⚠️ WHAT THIS NEVER DOES: rewrite or relabel an `SME_EXEC:` row (rule 3a, and the
Phase 13 exclusion's anchor); insert a negative quantity; delete a row anything
references.
"""
from __future__ import annotations

import datetime as _dt
from collections import defaultdict
from typing import Callable, Optional

from sqlalchemy import delete, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .ledger import _MD

recon_t = _MD.tables["consumption_reconciliation"]
link_t = _MD.tables["consumption_exec_link"]

# Packs. Workbook quantities are typed to two decimals at most; a hundredth of a
# can is below anything a store counts and above float noise.
TOL = 0.01
DELTA_SUFFIX = ":D"
SME_EXEC_PREFIX = "SME_EXEC:"

Key = tuple[str, str, str]      # (day, tag, sap)


def day(v) -> str:
    return str(v or "")[:10]


def sap_norm(v) -> str:
    return str(v or "").replace(" ", "").strip()


def is_delta_label(ref) -> bool:
    return str(ref or "").startswith("XLSX:") and str(ref).endswith(DELTA_SUFFIX)


async def resolver(session: AsyncSession, site_id: str) -> Callable[[object], tuple[Optional[str], str]]:
    """`raw Tank No. → (tag | None, status)`, status ∈ mapped · ignored ·
    unresolved. Aliases the operator mapped win; otherwise an EXACT normalised
    match against this site's equipment, and only when it is unique. Never a
    suffix guess — `TNK-091` is two vessels on two trains."""
    from ..bulk_import import _NON_EQUIPMENT_ALIASES, alias_norm
    tags = [r[0] for r in (await session.execute(text(
        'SELECT DISTINCT "Equipment_Tag_No" FROM sme_equipment '
        'WHERE "Site_ID" = :s AND COALESCE("Equipment_Tag_No", \'\') <> \'\''),
        {"s": site_id})).all()]
    by_norm: dict[str, set[str]] = defaultdict(set)
    for t in tags:
        by_norm[alias_norm(t)].add(t)
    aliases = {r[0]: (r[1], r[2]) for r in (await session.execute(text(
        'SELECT alias_norm, "Equipment_Tag_No", status FROM sme_tank_alias '
        'WHERE "Site_ID" = :s'), {"s": site_id})).all()}

    def resolve(raw) -> tuple[Optional[str], str]:
        n = alias_norm(raw)
        if not n:
            return None, "unresolved"
        if n in aliases:
            tag, st = aliases[n]
            if st == "mapped" and tag:
                return tag, "mapped"
            if st == "ignored":
                return None, "ignored"
        if n in _NON_EQUIPMENT_ALIASES:
            return None, "ignored"
        exact = by_norm.get(n) or set()
        if len(exact) == 1:
            return next(iter(exact)), "mapped"
        return None, "unresolved"

    return resolve


async def qr_totals(session: AsyncSession, site_id: str) -> dict[Key, dict]:
    """Every POSTED execution entry's material lines, summed per bucket, in
    PACKS (a line written in KG is converted with its factor — 14a)."""
    from . import units as U
    rows = (await session.execute(text(
        'SELECT e.id AS entry_id, e."Work_Date", e."Equipment_Tag_No", e."Qty_Unit", '
        '       m.id AS line_id, m."SAP_Code", m."Actual_Qty" '
        'FROM sme_execution_entry e '
        'JOIN sme_execution_entry_material m ON m."Entry_ID" = e.id '
        'WHERE e."Site_ID" = :s AND e."Stock_Posted_At" IS NOT NULL'),
        {"s": site_id})).mappings().all()
    umap = await U.unit_map(session, [r["SAP_Code"] for r in rows])
    out: dict[Key, dict] = {}
    for r in rows:
        sap = sap_norm(r["SAP_Code"])
        q = float(r["Actual_Qty"] or 0)
        if r["Qty_Unit"] == "base" and sap in umap:
            q = U.pack_from_base(q, umap[sap]["factor"]) or 0.0
        k = (day(r["Work_Date"]), str(r["Equipment_Tag_No"] or "").strip(), sap)
        b = out.setdefault(k, {"qr": 0.0, "entries": set(), "lines": []})
        b["qr"] += q
        b["entries"].add(int(r["entry_id"]))
        b["lines"].append(int(r["line_id"]))
    return out


def label_for_delta(site_id: str, k: Key) -> str:
    from ..bulk_import import _label_prefix
    return _label_prefix(site_id, "consumption", k[0], k[2], k[1]) + DELTA_SUFFIX


def status_of(qr: float, excel: float) -> str:
    # ⚠️ NOTHING IN THE BOOK YET IS NOT A CONFLICT. The workbook is typed up
    # after the day; a QR bucket the sync has not seen an Excel line for is
    # waiting, and telling the HOD "conflict" every sync until the store keeper
    # catches up would train them to ignore the one that matters.
    if excel <= TOL and qr > TOL:
        return "awaiting_excel"
    if abs(qr - excel) <= TOL:
        return "matched"
    return "excel_extra" if excel > qr else "qr_extra"


# ── the SYNC side ─────────────────────────────────────────────────────────────
async def plan_buckets(session: AsyncSession, site_id: str, *, file_rows: list[dict],
                       claimed: dict, db_rows: list[dict],
                       delta_rows: list[dict]) -> dict:
    """Decide, per bucket holding a posted entry, what the sync writes.

    Returns {"skip": set(id(fr)), "suppress_conflicts": set(db ids),
             "buckets": [...], "delta_upserts": [...], "delta_deletes": [...]}.
    Pure planning — writes nothing.
    """
    qr = await qr_totals(session, site_id)
    plan = {"skip": set(), "suppress_conflicts": set(), "buckets": [],
            "delta_upserts": [], "delta_deletes": []}
    if not qr:
        # No posted entries, but a delta row left from an earlier state (the
        # entry's stock was later reversed) must still go if nothing uses it.
        for r in delta_rows:
            plan["delta_deletes"].append(int(r["id"]))
        return plan
    resolve = await resolver(session, site_id)

    excel: dict[Key, float] = defaultdict(float)
    x_claimed: dict[Key, float] = defaultdict(float)
    for fr in file_rows:
        v = fr["vals"]
        tag, st = resolve(v.get("Tank_No"))
        if st != "mapped":
            continue
        k = (day(v["Date"]), tag, sap_norm(v["SAP_Code"]))
        if k not in qr:
            continue
        q = float(v["Quantity"] or 0)
        excel[k] += q
        cl = fr.get("_claim")
        if cl is None:
            plan["skip"].add(id(fr))            # represented by the delta row
        elif cl["how"] == "conflict":
            # The generic matcher called this a disagreement with an app row;
            # the bucket status now says it better, and says it once.
            plan["suppress_conflicts"].add(int(cl["row"]["id"]))
        elif cl["row"].get("_owner") in ("xlsx", "legacy"):
            x_claimed[k] += q                   # an Excel row that stays

    s_posted: dict[Key, float] = defaultdict(float)
    remainders: dict[Key, list[dict]] = defaultdict(list)
    for r in db_rows:
        ref = str(r.get("Source_Ref") or "")
        if ref.startswith(SME_EXEC_PREFIX):
            k = (day(r.get("Date")), str(r.get("Tank_No") or "").strip(),
                 sap_norm(r.get("SAP_Code")))
            if k in qr:
                s_posted[k] += float(r.get("Quantity") or 0)
                if ref.endswith(":R"):
                    remainders[k].append(r)
    existing_delta = {(day(r.get("Date")), str(r.get("Tank_No") or "").strip(),
                       sap_norm(r.get("SAP_Code"))): r for r in delta_rows}

    plan["remainder_updates"] = []
    for k, b in qr.items():
        target = max(b["qr"], excel[k])
        need = round(target - x_claimed[k] - s_posted[k], 4)
        entry = min(b["entries"])
        cur = existing_delta.pop(k, None)
        # ⚠️ THE BOOK CAUGHT UP. Excel first (9), paper later (12 → 3 posted as
        # a REMAINDER), then the store edits its line to 12 in place: without
        # this the ledger reads 9→12 + 3 = 15. A remainder is reconciliation's
        # own figure, so it shrinks — never an entry's authored row (rule 3a).
        over = -need if need < -TOL else 0.0
        for rr in remainders.get(k, []):
            if over <= TOL:
                break
            q = float(rr.get("Quantity") or 0)
            cut = min(q, over)
            plan["remainder_updates"].append({"id": int(rr["id"]), "Quantity": round(q - cut, 4)})
            over -= cut
            need += cut
        need = round(need, 4)
        if need > TOL:
            plan["delta_upserts"].append({
                "Date": k[0], "SAP_Code": k[2], "Quantity": need, "Site_ID": site_id,
                "Tank_No": k[1], "Source_Ref": label_for_delta(site_id, k),
                "Remarks": (f"Excel shows more than execution entry #{entry} — the "
                            f"difference, posted once (Phase 14b)"),
                "_entry": entry})
        elif cur is not None:
            plan["delta_deletes"].append(int(cur["id"]))
        ledger = round(target + (-need if need < -TOL else 0.0), 4)
        plan["buckets"].append({
            "key": k, "qr": round(b["qr"], 4), "excel": round(excel[k], 4),
            "ledger": ledger,
            # Still over after every remainder shrank: two authored figures
            # (an Excel row AND a full QR post) cover the same drums. Nothing
            # here may rewrite either — the HOD is told.
            "status": ("over_ledger" if need < -TOL else status_of(b["qr"], excel[k])),
            "entries": sorted(b["entries"])})
    # A delta row whose bucket no longer holds a posted entry.
    plan["delta_deletes"].extend(int(r["id"]) for r in existing_delta.values())
    return plan


async def apply_buckets(session: AsyncSession, site_id: str, plan: dict,
                        username: str) -> dict:
    """Write what `plan_buckets` decided, AFTER the ledger upsert has run."""
    linked = deleted = 0
    for u in plan.get("remainder_updates", []):
        # The pattern is a BIND parameter: `:R` inside a literal would be read
        # as a parameter name by `text()`.
        await session.execute(text(
            'UPDATE consumption SET "Quantity" = :q WHERE id = :i '
            'AND "Source_Ref" LIKE :rem'),
            {"q": u["Quantity"], "i": u["id"], "rem": SME_EXEC_PREFIX + "%:R"})
    for d in plan.get("delta_upserts", []):
        rid = (await session.execute(text(
            'SELECT id FROM consumption WHERE "Site_ID" = :s AND "Source_Ref" = :r'),
            {"s": site_id, "r": d["Source_Ref"]})).scalar()
        if rid is not None:
            await session.execute(pg_insert(link_t).values(
                Consumption_ID=int(rid), Entry_ID=int(d["_entry"]), via="excel_extra")
                .on_conflict_do_nothing(index_elements=["Consumption_ID"]))
            linked += 1
    for rid in plan.get("delta_deletes", []):
        used = (await session.execute(text(
            'SELECT 1 FROM sme_consumption_log WHERE "Consumption_ID" = :i LIMIT 1'),
            {"i": rid})).first()
        if used:
            continue            # never delete a row anything points at
        await session.execute(delete(link_t).where(link_t.c["Consumption_ID"] == rid))
        await session.execute(text('DELETE FROM consumption WHERE id = :i'), {"i": rid})
        deleted += 1
    for b in plan.get("buckets", []):
        await upsert_bucket(session, site_id, b["key"], qr=b["qr"], excel=b["excel"],
                            ledger=b["ledger"], entries=b["entries"], status=b["status"],
                            username=username)
    dup = await scan_neighbours(session, site_id)
    return {"linked": linked, "delta_deleted": deleted,
            "buckets": len(plan.get("buckets", [])), "possible_duplicates": dup}


# ── the POST side (Excel first, QR second) ────────────────────────────────────
async def adopt_for_line(session: AsyncSession, *, site_id: str, work_date: str,
                         tag: str, sap: str, qty: float, entry_id: int,
                         line_id: int) -> dict:
    """Before an entry posts a line, adopt the Excel rows already in its bucket.

    Returns {"post": qty still to post (≥ 0), "adopted": [ids], "excel": sum}.
    """
    resolve = await resolver(session, site_id)
    rows = (await session.execute(text(
        'SELECT c.id, c."Quantity", c."Tank_No", c."Source_Ref" '
        'FROM consumption c '
        'WHERE c."Site_ID" = :s AND LEFT(c."Date", 10) = :d '
        "  AND REPLACE(TRIM(c.\"SAP_Code\"), ' ', '') = :sap "
        "  AND (c.\"Source_Ref\" IS NULL OR c.\"Source_Ref\" LIKE 'XLSX:%') "
        '  AND NOT EXISTS (SELECT 1 FROM consumption_exec_link x '
        '                  WHERE x."Consumption_ID" = c.id)'),
        {"s": site_id, "d": day(work_date), "sap": sap_norm(sap)})).mappings().all()
    mine = [r for r in rows if resolve(r["Tank_No"])[0] == tag]
    excel = round(sum(float(r["Quantity"] or 0) for r in mine), 4)
    for r in mine:
        await session.execute(pg_insert(link_t).values(
            Consumption_ID=int(r["id"]), Entry_ID=entry_id, Line_ID=line_id,
            via="adopted").on_conflict_do_nothing(index_elements=["Consumption_ID"]))
    post = round(max(float(qty) - excel, 0.0), 4)
    return {"post": post if post > TOL else 0.0, "adopted": [int(r["id"]) for r in mine],
            "excel": excel}


# ── records ───────────────────────────────────────────────────────────────────
async def upsert_bucket(session: AsyncSession, site_id: str, k: Key, *, qr: float,
                        excel: float, ledger: float, entries: list[int],
                        status: str, username: str, detail: Optional[str] = None) -> None:
    vals = {"QR_Qty": qr, "Excel_Qty": excel, "Ledger_Qty": ledger, "status": status,
            "entry_ids": ",".join(str(e) for e in entries), "detail": detail,
            "updated_at": _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)}
    prev = (await session.execute(select(recon_t).where(
        recon_t.c["Site_ID"] == site_id, recon_t.c["Work_Date"] == k[0],
        recon_t.c["Equipment_Tag_No"] == k[1], recon_t.c["SAP_Code"] == k[2]
    ))).mappings().first()
    if prev is None:
        await session.execute(pg_insert(recon_t).values(
            Site_ID=site_id, Work_Date=k[0], Equipment_Tag_No=k[1], SAP_Code=k[2], **vals))
    else:
        if prev["status"] != status:
            # A NEW situation needs a new acknowledgement.
            vals.update(acknowledged_by=None, acknowledged_at=None, acknowledge_note=None)
        await session.execute(update(recon_t).where(recon_t.c["id"] == prev["id"]).values(**vals))
    notified = prev["notified_status"] if prev is not None else None
    if status in ("qr_extra", "over_ledger", "possible_duplicate") and notified != status:
        await _tell_hod(session, site_id, k, status, qr, excel, entries, username)
        await session.execute(update(recon_t).where(
            recon_t.c["Site_ID"] == site_id, recon_t.c["Work_Date"] == k[0],
            recon_t.c["Equipment_Tag_No"] == k[1], recon_t.c["SAP_Code"] == k[2]
        ).values(notified_status=status))


async def _tell_hod(session, site_id, k, status, qr, excel, entries, username) -> None:
    from .notifications import notify
    if status == "over_ledger":
        title = f"Consumption counted twice — {k[2]} on {k[1]}, {k[0]}"
        body = (f"Both the execution form and the Excel log posted these drums, so "
                f"the ledger is above both ({qr:g} on paper, {excel:g} in the book). "
                f"Correct it through entry #{', #'.join(str(e) for e in entries)} or "
                f"the workbook — nothing was rewritten automatically.")
        sev = "warning"
    elif status == "qr_extra":
        title = f"Consumption conflict — {k[2]} on {k[1]}, {k[0]}"
        body = (f"The execution form claims {qr:g} but the store's Excel log shows "
                f"{excel:g}. The ledger holds {qr:g} (never the sum). Check the "
                f"entry #{', #'.join(str(e) for e in entries)} and correct it through "
                f"the entry if the form is wrong.")
        sev = "warning"
    else:
        title = f"Possible duplicate — {k[2]} on {k[1]}, {k[0]}"
        body = ("An Excel draw of this material on this equipment sits one day away "
                "from a QR execution entry. It was NOT merged (only the exact day "
                "merges). Check whether it is the same drum.")
        sev = "info"
    try:
        await notify(session, event_key=f"consumption_{status}", title=title, body=body,
                     severity=sev, recipient_role="hod", recipient_site=site_id,
                     link_page="/sme", related_table="consumption_reconciliation",
                     related_ref=f"{k[0]}|{k[1]}|{k[2]}")
    except Exception:                                   # noqa: BLE001 — never fatal
        pass


async def scan_neighbours(session: AsyncSession, site_id: str) -> int:
    """±1-day possible duplicates (Q14-2): an Excel draw of the same tag and SAP,
    not attached to any entry, on the day before or after a QR bucket that has
    no QR bucket of its own. Reported, never merged. Stale reports are cleared."""
    qr = await qr_totals(session, site_id)
    resolve = await resolver(session, site_id)
    found: set[Key] = set()
    for (d, tag, sap) in qr:
        base = _dt.date.fromisoformat(d) if len(d) == 10 else None
        if base is None:
            continue
        for off in (-1, 1):
            nd = (base + _dt.timedelta(days=off)).isoformat()
            if (nd, tag, sap) in qr:
                continue
            rows = (await session.execute(text(
                'SELECT c."Tank_No" FROM consumption c '
                'WHERE c."Site_ID" = :s AND LEFT(c."Date", 10) = :d '
                "  AND REPLACE(TRIM(c.\"SAP_Code\"), ' ', '') = :sap "
                "  AND (c.\"Source_Ref\" IS NULL OR c.\"Source_Ref\" LIKE 'XLSX:%') "
                '  AND NOT EXISTS (SELECT 1 FROM consumption_exec_link x '
                '                  WHERE x."Consumption_ID" = c.id)'),
                {"s": site_id, "d": nd, "sap": sap})).all()
            if any(resolve(r[0])[0] == tag for r in rows):
                found.add((nd, tag, sap))
    for k in found:
        prev = (await session.execute(select(recon_t.c["status"]).where(
            recon_t.c["Site_ID"] == site_id, recon_t.c["Work_Date"] == k[0],
            recon_t.c["Equipment_Tag_No"] == k[1], recon_t.c["SAP_Code"] == k[2]))).scalar()
        if prev in (None, "possible_duplicate"):
            neighbours = sorted(x for x in qr if x[1] == k[1] and x[2] == k[2]
                                and abs((_dt.date.fromisoformat(x[0])
                                         - _dt.date.fromisoformat(k[0])).days) == 1)
            await upsert_bucket(session, site_id, k, qr=0.0, excel=0.0, ledger=0.0,
                                entries=sorted({e for x in neighbours for e in qr[x]["entries"]}),
                                status="possible_duplicate", username="reconcile",
                                detail="QR entry on " + ", ".join(x[0] for x in neighbours))
    # Clear reports whose condition no longer holds (and nobody acknowledged).
    stale = (await session.execute(select(recon_t).where(
        recon_t.c["Site_ID"] == site_id, recon_t.c["status"] == "possible_duplicate"))
    ).mappings().all()
    for r in stale:
        if (r["Work_Date"], r["Equipment_Tag_No"], r["SAP_Code"]) not in found \
                and not r["acknowledged_by"]:
            await session.execute(delete(recon_t).where(recon_t.c["id"] == r["id"]))
    return len(found)


async def list_buckets(session: AsyncSession, *, site_id: Optional[str],
                       status: Optional[str] = None, limit: int = 500) -> list[dict]:
    stmt = select(recon_t)
    if site_id is not None:
        stmt = stmt.where(recon_t.c["Site_ID"] == site_id)
    if status:
        stmt = stmt.where(recon_t.c["status"] == status)
    stmt = stmt.order_by(recon_t.c["Work_Date"].desc(), recon_t.c["id"].desc()).limit(limit)
    return [dict(r) for r in (await session.execute(stmt)).mappings().all()]
