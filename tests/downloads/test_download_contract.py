"""
tests/downloads/test_download_contract.py — every file GI Hub hands out, as
every role, with the parameters the screen actually sends (Phase 23a).

    .venv/bin/python -m pytest tests/downloads -q

WHY THIS EXISTS
---------------
On 2026-10-09 the operator printed a consumption form from the Execution page
and got "Request failed with status code 422". The route was fine and so was
its schema. The PAGE never sent `site_id`, and printing REGISTERS the form
against a site: a site-bound user gets their own, a global role (admin) has to
name one. No test called the route the way the screen calls it, as the role
that pressed the button. This file does, for every file-returning route there
is, and fails on the class of bug rather than the instance:

  1. DISCOVERY, NOT A LIST. Routes that return a file are found from the app's
     own route table and their source (`StreamingResponse`, `FileResponse`, a
     PDF / XLSX / CSV / image media type, or a `format` query parameter). A new
     download that nobody added a case for FAILS `test_every_file_route_has_a_case`
     — the day it is written, not the day somebody prints from it.
  2. EVERY ROLE. Each case is called as all nine roles. A role may be refused
     (403) or, where the case says why, find nothing (404). It may NEVER get a
     422 or a 5xx: a validation error on a button a role can press is a bug in
     the screen or the route, and a 500 is a bug, full stop.
  3. THE BYTES. A 2xx must be the file it claims: `%PDF`, an XLSX that opens,
     a CSV with a header, a PNG / JPEG signature. Admin must always get one.
  4. THE SITE RULE, EXACTLY. Each global role is also called WITHOUT `site_id`.
     If the route then says 422, the screen has to send it — the case must say
     `site_for_global=True` AND the caller named in `ui` must pass `site_id`.
     That is the operator's bug, as a rule.

⚠️ RULE 15: the live database is never opened. `testdb.provision()` builds a
throwaway one before `backend.api.db` is imported — `gihub_dltest`, so this
gate and `service_tests` can run side by side. ⚠️ RULE 16: nothing here skips.
A case whose data cannot be seeded FAILS.
"""
from __future__ import annotations

import asyncio
import csv
import datetime as _dt
import inspect
import io
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# Same hermetic switches as service_tests — see its header for each reason.
os.environ.setdefault("GI_DOTENV", "0")
os.environ["OLLAMA_HOST"] = "http://127.0.0.1:9"
os.environ["GI_DRIVE_SECRETS_DIR"] = "/nonexistent-download-gate-drive"
_DRIVE_CACHE = tempfile.mkdtemp(prefix="gi-dl-drive-")
os.environ["GI_DRIVE_CACHE_DIR"] = _DRIVE_CACHE
os.environ.setdefault("GI_SCHEDULER", "0")
os.environ.setdefault("JWT_SECRET", "download-gate-only-secret-key-32-bytes-minimum")
os.environ.setdefault("GI_TEST_DB", "gihub_dltest")

from backend.api import testdb  # noqa: E402

testdb.provision()

from httpx import ASGITransport, AsyncClient  # noqa: E402

from backend.api import auth  # noqa: E402
from backend.api.main import app  # noqa: E402

LOOP = asyncio.new_event_loop()
ROLES = list(auth.ROLE_META)
GLOBAL = {r for r, m in auth.ROLE_META.items()
          if m["level"] >= auth.SITE_SCOPE_MIN_LEVEL} | set(auth.QC_OVERSIGHT_ROLES)


def run(coro):
    return LOOP.run_until_complete(coro)


# ── discovery ────────────────────────────────────────────────────────────────
_FILE_SRC = re.compile(
    r"StreamingResponse\(|FileResponse\(|application/pdf|spreadsheetml|text/csv"
    r"|image/(png|jpeg|webp)|application/octet-stream")


def _walk(routes):
    for r in routes:
        if type(r).__name__ == "_IncludedRouter":
            yield from r.effective_route_contexts()
        elif hasattr(r, "dependant"):
            yield r


_HELPERS: dict[str, set[str]] = {}


def _file_helpers(module) -> set[str]:
    """Names of the module's own functions that build a file response — a
    route that returns `_pdf_response(...)` hands out a file just the same."""
    name = module.__name__
    if name not in _HELPERS:
        found = set()
        for attr, obj in vars(module).items():
            if inspect.isfunction(obj) and obj.__module__ == name:
                try:
                    if _FILE_SRC.search(inspect.getsource(obj)):
                        found.add(attr)
                except (OSError, TypeError):
                    pass
        _HELPERS[name] = found
    return _HELPERS[name]


def _hands_out_a_file(c) -> bool:
    try:
        src = inspect.getsource(c.endpoint)
    except (OSError, TypeError):
        return False
    if "text/event-stream" in src:
        return False
    if any(q.name == "format" for q in c.dependant.query_params) or _FILE_SRC.search(src):
        return True
    mod = sys.modules.get(c.endpoint.__module__)
    helpers = _file_helpers(mod) - {c.endpoint.__name__} if mod else set()
    return any(re.search(rf"\b{re.escape(h)}\(", src) for h in helpers)


def file_routes() -> dict[str, Any]:
    """'METHOD /path' → route context, for every route that hands out a file."""
    out = {}
    for c in _walk(app.routes):
        for method in sorted({"GET", "POST"} & set(c.methods)):
            if _hands_out_a_file(c):
                out[f"{method} {c.path}"] = c
    return out


# Routes the discovery finds that do NOT hand a file to the user. Each has a
# reason; an entry without one is a skip, and rule 16 does not allow those.
NOT_DOWNLOADS = {
    "POST /execution/ocr/upload": "an UPLOAD: it accepts a photo, returns JSON",
    "POST /auth/2fa/enroll": "JSON with the QR as a data: URI inside it, not a file",
}


# ── the data a case needs, seeded into the throwaway database ────────────────
def _sync_url() -> str:
    url = os.environ["DATABASE_URL"]
    for d in ("postgresql+asyncpg://", "postgresql+psycopg2://", "postgresql://"):
        if url.startswith(d):
            return "postgresql://" + url[len(d):]
    return url


def _db():
    import psycopg2
    conn = psycopg2.connect(_sync_url())
    conn.autocommit = True
    return conn


def _one(cur, sql: str, args: tuple = ()) -> Any:
    cur.execute(sql, args)
    row = cur.fetchone()
    return row[0] if row else None


_TINY_JPEG = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb004300080606070605080707070909080a0c"
    "140d0c0b0b0c1912130f141d1a1f1e1d1a1c1c20242e2720222c231c1c2837292c30313434341f27"
    "393d38323c2e333432ffc0000b080001000101011100ffc4001f00000105010101010101000000000"
    "00000000102030405060708090a0bffc400b5100002010303020403050504040000017d0102030004"
    "1105122131410613516107227114328191a1082342b1c11552d1f02433627282090a161718191a252"
    "62728292a3435363738393a434445464748494a535455565758595a636465666768696a7374757677"
    "78797a838485868788898a92939495969798999aa2a3a4a5a6a7a8a9aab2b3b4b5b6b7b8b9bac2c3c"
    "4c5c6c7c8c9cad2d3d4d5d6d7d8d9dae1e2e3e4e5e6e7e8e9eaf1f2f3f4f5f6f7f8f9faffda000801"
    "0100003f00fbd3ffd9")


@dataclass
class Ctx:
    site: str = ""
    sap: str = ""
    employee: str = ""
    system: str = ""
    tag: str = ""
    pr: str = ""
    returnable: int = 0
    attachment: int = 0
    entry: int = 0
    inspection: int = 0
    archive: int = 0
    drive_file: int = 0
    weekly_token: str = ""
    training: tuple[str, str] = ("", "")
    xlsx_upload: bytes = b""


def seed() -> Ctx:
    """Rows every case can rely on. Read what the fixture has; create the rest."""
    ctx = Ctx()
    conn = _db()
    cur = conn.cursor()
    try:
        # A reused test database (GI_TEST_DB_REUSE=1) still holds the last
        # run's rows; clear them so every run seeds the same state.
        for sql in ("""DELETE FROM sme_execution_entry WHERE "Entry_No" = 'DL-ENTRY-1'""",
                    """DELETE FROM pr_master WHERE "PR_Number" = 'PR-DLGATE-0001'""",
                    """DELETE FROM returnable_items WHERE borrower_name = 'Download Gate'""",
                    """DELETE FROM entry_attachments WHERE doc_number = 'DL-1'""",
                    """DELETE FROM qc_inspections WHERE source_ref = 'DL-1'""",
                    """DELETE FROM mtc_documents WHERE mtc_number = 'DL-MTC-1'"""):
            cur.execute(sql)
        ctx.site =_one(cur, """SELECT "Site_ID" FROM inventory WHERE COALESCE("Site_ID",'')<>''
                                GROUP BY 1 ORDER BY count(*) DESC LIMIT 1""")
        assert ctx.site, "no site in the test database"
        ctx.sap = _one(cur, """SELECT "SAP_Code" FROM inventory WHERE "Site_ID"=%s
                               ORDER BY "SAP_Code" LIMIT 1""", (ctx.site,))
        ctx.employee = _one(cur, """SELECT "ID_Number" FROM employees
                                    WHERE COALESCE("ID_Number",'')<>'' AND "Site_ID"=%s
                                    ORDER BY 1 LIMIT 1""", (ctx.site,))
        if not ctx.employee:
            cur.execute("""INSERT INTO employees ("ID_Number","Name","Site_ID","Department")
                           VALUES ('DL-0001','Download Gate',%s,'Store')""", (ctx.site,))
            ctx.employee = "DL-0001"
        ctx.system = _one(cur, """SELECT "Lining_System_Code" FROM sme_recipe
                                  WHERE COALESCE("Lining_System_Code",'')<>''
                                  GROUP BY 1 ORDER BY count(*) DESC LIMIT 1""")
        assert ctx.system, "no SME recipe to print a form from"
        ctx.tag = _one(cur, """SELECT "Equipment_Tag_No" FROM sme_equipment
                               WHERE COALESCE("Equipment_Tag_No",'')<>'' ORDER BY 1 LIMIT 1""") or ""

        # a PR at the site (PR numbers are free text)
        ctx.pr = "PR-DLGATE-0001"
        cur.execute("""INSERT INTO pr_master ("PR_Number","SAP_Code","Requested_Qty","Site_ID",
                                              status, workflow_state)
                       SELECT %s, %s, 2, %s, 'Pending', 'draft'
                       WHERE NOT EXISTS (SELECT 1 FROM pr_master WHERE "PR_Number"=%s)""",
                    (ctx.pr, ctx.sap, ctx.site, ctx.pr))

        # a loan out at the site
        ctx.returnable = _one(cur, """INSERT INTO returnable_items
            (material_name, uom, qty, borrower_name, given_time, status, "Site_ID", "SAP_Code")
            VALUES ('Grinder 7in','EA',1,'Download Gate', now(), 'out', %s, %s)
            RETURNING id""", (ctx.site, ctx.sap))

        # an attachment stored in the database
        ctx.attachment = _one(cur, """INSERT INTO entry_attachments
            ("Site_ID", doc_type, doc_number, file_name, mime_type, file_size, file_blob, uploaded_by)
            VALUES (%s,'DN','DL-1','dl.jpg','image/jpeg',%s,%s,'dl_store_keeper') RETURNING id""",
            (ctx.site, len(_TINY_JPEG), _TINY_JPEG))

        # an execution entry with its photographed form (the crop endpoint)
        ctx.entry = _one(cur, """INSERT INTO sme_execution_entry
            ("Site_ID","Entry_No","Work_Date","Equipment_Tag_No","Lining_System_Code",
             "Execution_Sub_Activity_Code", status, "OCR_Image", "OCR_Image_Mime", created_by)
            VALUES (%s,'DL-ENTRY-1',%s,%s,%s,'ESC1','sk_submitted',%s,'image/jpeg','dl_gate')
            RETURNING id""",
            (ctx.site, _dt.date.today().isoformat(), ctx.tag or "DL-TAG", ctx.system, _TINY_JPEG))

        # a QC inspection with a decision (its certificate)
        ctx.inspection = _one(cur, """INSERT INTO qc_inspections
            ("Site_ID","SAP_Code", source_type, source_ref, submitted_qty, approved_qty,
             rejected_qty, status, inspected_by, inspected_at, created_by)
            VALUES (%s,%s,'receipt','DL-1',5,5,0,'approved','dl_qc',now(),'dl_store_keeper')
            RETURNING id""", (ctx.site, ctx.sap))
        mtc = _one(cur, """INSERT INTO mtc_documents
            ("Site_ID","SAP_Code", mtc_number, file_name, mime_type, file_blob, status, submitted_by,
             qc_inspection_id)
            VALUES (%s,%s,'DL-MTC-1','mtc.pdf','application/pdf',%s,'approved','dl_store_keeper',%s)
            RETURNING id""", (ctx.site, ctx.sap, b"%PDF-1.4\n%dl-gate mtc\n%%EOF\n", ctx.inspection))
        cur.execute("UPDATE qc_inspections SET mtc_document_id=%s WHERE id=%s", (mtc, ctx.inspection))

        # a Drive copy in the cache folder (the DN / MTC viewer)
        f = Path(_DRIVE_CACHE) / "dn" / "dl-gate.jpg"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(_TINY_JPEG)
        ctx.drive_file = _one(cur, """INSERT INTO drive_files
            (drive_id, kind, folder, name, mime, size, cache_path, link_status)
            VALUES ('dl-gate-1','dn','DN for X','DN 90001.jpg','image/jpeg',%s,%s,'linked')
            ON CONFLICT (drive_id) DO UPDATE SET cache_path = EXCLUDED.cache_path
            RETURNING id""", (len(_TINY_JPEG), str(f)))

        # an unauthenticated weekly-report link (token stored as its sha256)
        import hashlib
        ctx.weekly_token = "dl-gate-" + "x" * 40
        h = hashlib.sha256(ctx.weekly_token.encode()).hexdigest()
        cur.execute("DELETE FROM generated_reports WHERE token_hash=%s", (h,))
        today = _dt.date.today().isoformat()
        cur.execute("""INSERT INTO generated_reports
                       (kind, "Site_ID", date_from, date_to, filename, content, token_hash, expires_at)
                       VALUES ('weekly-exec', %s, %s, %s, 'weekly.pdf', %s, %s,
                               now() + interval '1 day')""",
                    (ctx.site, today, today, b"%PDF-1.4\n%dl-gate\n%%EOF\n", h))
    finally:
        cur.close()
        conn.close()

    # a stock workbook to check (the marked-copy download takes an upload)
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["SAP Code", "Description", "Qty"])
    ws.append([ctx.sap, "gate row", 1])
    buf = io.BytesIO()
    wb.save(buf)
    ctx.xlsx_upload = buf.getvalue()
    return ctx


CTX: Ctx | None = None


def ctx() -> Ctx:
    global CTX
    if CTX is None:
        CTX = seed()
    return CTX


# ── calling ─────────────────────────────────────────────────────────────────
def token(role: str, site: str) -> str:
    bound = "" if role in GLOBAL else site
    wh = "WH-DL" if role == "warehouse_user" else ""
    return auth._make_token(f"dl_{role}", role, bound, auth.ACCESS_TTL, warehouse_id=wh)


async def _call(method: str, path: str, role: Optional[str], params: dict,
                body: Any = None, files: Any = None, data: Any = None):
    headers = {}
    if role:
        headers["Authorization"] = f"Bearer {token(role, ctx().site)}"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://dl") as c:
        if method == "GET":
            return await c.get(path, params=params, headers=headers)
        if files is not None:
            return await c.post(path, params=params, headers=headers, files=files, data=data)
        return await c.post(path, params=params, headers=headers, json=body)


def call(method, path, role, params, body=None, files=None, data=None):
    return run(_call(method, path, role, params, body, files, data))


# ── what a file must look like ──────────────────────────────────────────────
def check_bytes(kind: str, r) -> Optional[str]:
    b = r.content
    if kind == "pdf":
        return None if b[:5] == b"%PDF-" else f"not a PDF: {b[:12]!r}"
    if kind == "xlsx":
        import openpyxl
        try:
            openpyxl.load_workbook(io.BytesIO(b), read_only=True)
            return None
        except Exception as e:  # noqa: BLE001
            return f"not an XLSX: {type(e).__name__} {b[:12]!r}"
    if kind == "csv":
        text = b.decode("utf-8-sig", errors="replace")
        rows = list(csv.reader(io.StringIO(text)))
        return None if rows and any(rows[0]) else "an empty CSV"
    if kind == "png":
        return None if b[:8] == b"\x89PNG\r\n\x1a\n" else f"not a PNG: {b[:8]!r}"
    if kind == "jpeg":
        return None if b[:3] == b"\xff\xd8\xff" else f"not a JPEG: {b[:4]!r}"
    if kind == "image":
        return check_bytes("png", r) and check_bytes("jpeg", r)
    if kind == "video":
        return None if len(b) > 0 else "an empty video"
    raise AssertionError(f"unknown kind {kind}")


# ── the cases ───────────────────────────────────────────────────────────────
@dataclass
class Case:
    route: str                                   # 'GET /path/{param}' as discovered
    id: str
    path: Callable[[Ctx], str]
    kind: str
    ui: str                                      # the screen that calls it ('' = none)
    params: Callable[[Ctx], dict] = lambda c: {}
    body: Optional[Callable[[Ctx], Any]] = None
    upload: Optional[Callable[[Ctx], tuple]] = None
    site_for_global: bool = False                # the UI sends site_id for a global role
    # status → why a role may legitimately get it (403 is always allowed)
    allow: dict[int, str] = field(default_factory=dict)
    anonymous: bool = False                      # called WITHOUT a token (a link)
    site_in: str = "query"                       # where the UI puts site_id: query | body | form
    admin_may_fail: bool = False                 # only with a reason in `allow`
    roles: Optional[list[str]] = None            # the UI's audience, when narrower


def _keys(fn_src: str) -> list[str]:
    return sorted(set(re.findall(r'key == "([a-z0-9_-]+)"', fn_src)))


def _src(route: str) -> str:
    return inspect.getsource(file_routes()[route].endpoint)


def _report_keys() -> list[str]:
    from backend.api import reports
    return sorted(reports.REPORTS)


def _master_entities() -> list[str]:
    from backend.api import documents
    return sorted(documents._MASTER)


_SP = lambda c: {"site_id": c.site}  # noqa: E731


def cases() -> list[Case]:
    cs: list[Case] = [
        Case("GET /execution/forms/{system_code}", "consumption-form",
             lambda c: f"/execution/forms/{c.system}", "pdf",
             "frontend/src/api/hooks.ts", site_for_global=True),
        Case("GET /execution/forms/{system_code}", "consumption-form-x3",
             lambda c: f"/execution/forms/{c.system}", "pdf",
             "frontend/src/api/hooks.ts", params=lambda c: {"copies": 3}, site_for_global=True),
        Case("GET /execution/entries/{entry_id}/crop", "entry-crop",
             lambda c: f"/execution/entries/{c.entry}/crop", "image",
             "frontend/src/pages/ExecutionPage.tsx", params=lambda c: {"row": 0}),
        Case("GET /documents/employee-badge/{id_number}", "badge-png",
             lambda c: f"/documents/employee-badge/{c.employee}", "png",
             "frontend/src/pages/DocumentsPage.tsx"),
        Case("GET /documents/employee-badges", "badges-pdf",
             lambda c: "/documents/employee-badges", "pdf",
             "frontend/src/pages/DocumentsPage.tsx", site_for_global=True),
        Case("GET /documents/material-stickers", "stickers",
             lambda c: "/documents/material-stickers", "pdf",
             "frontend/src/pages/DocumentsPage.tsx", site_for_global=True),
        Case("GET /documents/qr-labels", "qr-labels",
             lambda c: "/documents/qr-labels", "pdf",
             "frontend/src/pages/DocumentsPage.tsx", site_for_global=True),
        *[Case("GET /documents/reference/{key}", f"reference-{k}",
               (lambda k: lambda c: f"/documents/reference/{k}")(k), "pdf",
               "frontend/src/pages/DocumentsPage.tsx") for k in ("sop", "manual")],
        Case("GET /drive/files/{file_id}", "drive-file",
             lambda c: f"/drive/files/{c.drive_file}", "jpeg",
             "frontend/src/components/DriveDocs.tsx"),
        Case("GET /entry/attachments/{aid}/download", "attachment",
             lambda c: f"/entry/attachments/{c.attachment}/download", "jpeg",
             "frontend/src/pages/DocumentLibraryPage.tsx"),
        Case("GET /entry/returnables/{rid}/slip", "loan-slip",
             lambda c: f"/entry/returnables/{c.returnable}/slip", "pdf",
             "frontend/src/api/returnablesHooks.ts"),
        Case("GET /hod/prs/{pr_number}/pdf", "pr-pdf",
             lambda c: f"/hod/prs/{c.pr}/pdf", "pdf",
             "frontend/src/api/hooks.ts", site_for_global=True),
        Case("GET /hod/executive-summary/export.pdf", "exec-summary-pdf",
             lambda c: "/hod/executive-summary/export.pdf", "pdf",
             "frontend/src/api/hooks.ts", site_for_global=True),
        Case("GET /hod/executive-summary/export.xlsx", "exec-summary-xlsx",
             lambda c: "/hod/executive-summary/export.xlsx", "xlsx",
             "frontend/src/api/hooks.ts", site_for_global=True),
        Case("GET /hod/valuation/export.pdf", "valuation-pdf",
             lambda c: "/hod/valuation/export.pdf", "pdf", "", site_for_global=True),
        Case("GET /lot-register/problems.xlsx", "lot-problems",
             lambda c: "/lot-register/problems.xlsx", "xlsx",
             "frontend/src/api/lotHooks.ts", site_for_global=True),
        Case("GET /qc/inspections/{iid}/certificate", "qc-certificate",
             lambda c: f"/qc/inspections/{c.inspection}/certificate", "pdf",
             "frontend/src/pages/QcInspectionsPage.tsx", params=lambda c: {"inline": 1},
             allow={404: "an inspection outside the caller's scope is a 404, never a 403 "
                         "(qc.inspection_certificate: a direct fetch must not confirm it exists)"}),
        Case("GET /reports/weekly-exec/{token}", "weekly-exec-link",
             lambda c: f"/reports/weekly-exec/{c.weekly_token}", "pdf", "", anonymous=True),
        Case("POST /stock/excel-check/marked", "excel-check-marked",
             lambda c: "/stock/excel-check/marked", "xlsx",
             "frontend/src/components/ExcelCheck.tsx",
             upload=lambda c: ({"file": ("stock.xlsx", c.xlsx_upload,
                                         "application/vnd.openxmlformats-officedocument."
                                         "spreadsheetml.sheet")}, {}),
             site_for_global=True, site_in="form"),
        Case("POST /sme/export/rows", "sme-rows",
             lambda c: "/sme/export/rows", "xlsx", "frontend/src/sme/ExecutionPlan.tsx",
             body=lambda c: {"title": "Gate", "columns": ["A", "B"],
                             "rows": [["x", 1], ["=1+1", 2]], "format": "xlsx"}),
        Case("POST /sme/plan/export", "sme-plan",
             lambda c: "/sme/plan/export", "xlsx", "frontend/src/sme/TotalOverview.tsx",
             body=lambda c: {"priority_order": [], "key": "session-full", "format": "xlsx"}),
        Case("POST /mh/planner/session/export", "mh-session",
             lambda c: "/mh/planner/session/export", "xlsx",
             "frontend/src/pages/SessionManpowerReport.tsx",
             body=lambda c: {"priority_order": [c.tag] if c.tag else [], "format": "xlsx"},
             site_for_global=True, site_in="body"),
        Case("GET /execution/sme-link/history/export", "sme-link-history",
             lambda c: "/execution/sme-link/history/export", "xlsx",
             "frontend/src/pages/SurfaceShieldLogPage.tsx", params=lambda c: {"format": "xlsx"},
             site_for_global=True),
    ]
    # ?format= exports of JSON views (the Excel / CSV buttons)
    for route, path, ui in (
            ("GET /execution/report/variance", "/execution/report/variance",
             "frontend/src/pages/ExecutionReportTabs.tsx"),
            ("GET /execution/report/reasons", "/execution/report/reasons",
             "frontend/src/pages/ExecutionReportTabs.tsx"),
            ("GET /execution/report/surface-prep", "/execution/report/surface-prep",
             "frontend/src/pages/ExecutionReportTabs.tsx")):
        for fmt in ("xlsx", "csv"):
            cs.append(Case(route, f"{path.rsplit('/', 1)[1]}-{fmt}",
                           (lambda p: lambda c: p)(path), fmt, ui,
                           params=(lambda f: lambda c: {"format": f})(fmt)))
    # keyed exports — the keys are read from the route's own source, so a new key
    # is covered without anybody remembering to add it here
    for key in _report_keys():
        for fmt in ("xlsx", "csv", "pdf"):
            cs.append(Case("GET /reports/{key}", f"report-{key}-{fmt}",
                           (lambda k: lambda c: f"/reports/{k}")(key), fmt,
                           "frontend/src/api/hooks.ts",
                           params=(lambda f: lambda c: {"format": f})(fmt),
                           site_for_global=True))
    for entity in _master_entities():
        cs.append(Case("GET /documents/master/{entity}", f"master-{entity}",
                       (lambda e: lambda c: f"/documents/master/{e}")(entity), "xlsx",
                       "frontend/src/pages/DocumentsPage.tsx", site_for_global=True))
    for route, prefix, ui in (("GET /sme/export/{key}", "/sme/export", "frontend/src/pages/SmePage.tsx"),
                              ("GET /mh/export/{key}", "/mh/export", "frontend/src/pages/ManHoursPage.tsx"),
                              ("GET /ppe/export/{key}", "/ppe/export", "frontend/src/api/hooks.ts")):
        for key in _keys(_src(route)):
            # what the screen adds beyond the format (EmployeesPage passes the
            # employee for a PPE history; the rest pass only the site)
            extra = (lambda c: {"id_number": c.employee}) if (prefix, key) == ("/ppe/export", "history") \
                else (lambda c: {})
            for fmt in ("xlsx", "pdf"):
                cs.append(Case(route, f"{prefix.strip('/').replace('/', '-')}-{key}-{fmt}",
                               (lambda p, k: lambda c: f"{p}/{k}")(prefix, key), fmt, ui,
                               params=(lambda f, e: lambda c: {"format": f, **e(c)})(fmt, extra),
                               site_for_global=True))
    return cs


CASES = cases()


def _report_archive_case() -> Case:
    return Case("GET /reports/archive/{aid}/download", "report-archive",
                lambda c: f"/reports/archive/{c.archive}/download", "pdf",
                "frontend/src/api/hooks.ts")


def _training_case() -> Case:
    return Case("GET /training/media/{module_key}/{language}.{ext}", "training-media",
                lambda c: "/training/media", "video", "frontend/src/pages/TrainingPage.tsx",
                allow={404: "the rendered tutorial videos are not committed (Phase 12: "
                            "renders are not a gate) — 'not published on this server' "
                            "is the supported state"}, admin_may_fail=True)


CASES += [_report_archive_case(), _training_case()]


# ── 1. discovery ────────────────────────────────────────────────────────────
def test_every_file_route_has_a_case():
    found = set(file_routes()) - set(NOT_DOWNLOADS)
    covered = {c.route for c in CASES}
    missing = sorted(found - covered)
    assert not missing, (
        "these routes hand out a file and no case calls them — add a Case "
        f"(or, if it is not a download, an entry in NOT_DOWNLOADS with a reason): {missing}")
    stale = sorted(covered - set(file_routes()))
    assert not stale, f"cases for routes that no longer exist: {stale}"


def test_discovery_finds_the_operators_route():
    """The route that broke must be one the discovery sees, or nothing above
    protects it."""
    assert "GET /execution/forms/{system_code}" in file_routes()


# ── 2. the special-shape cases need a little data first ─────────────────────
def _prepare_archive(c: Ctx) -> None:
    if c.archive:
        return
    r = call("POST", "/reports/archive", "admin", {},
             body={"key": "stock", "format": "pdf", "site_id": c.site})
    assert r.status_code in (200, 201), f"could not archive a report: {r.status_code} {r.text[:300]}"
    c.archive = int(r.json().get("id"))


def _training_path(c: Ctx, role: str) -> tuple[str, dict]:
    """A ticket for the first module this role can see (any language)."""
    r = call("GET", "/training/modules", role, {})
    if r.status_code != 200:
        return "/training/media/none/en.mp4", {}
    body = r.json()
    for m in body.get("modules") or []:
        key = m.get("module_key") or m.get("key")
        for lang in (body.get("languages") or ["en"]):
            t = call("GET", f"/training/media-ticket/{key}/{lang}", role, {})
            if t.status_code == 200:
                return f"/training/media/{key}/{lang}.mp4", {"ticket": t.json().get("ticket")}
    return "/training/media/none/en.mp4", {}


# ── 3. every case, every role ───────────────────────────────────────────────
def _resolve(case: Case, role: Optional[str]) -> tuple[str, dict]:
    c = ctx()
    if case.id == "report-archive":
        _prepare_archive(c)
    if case.id == "training-media":
        return _training_path(c, role or "admin")
    params = dict(case.params(c))
    if case.site_for_global and role in GLOBAL and case.site_in == "query":
        params["site_id"] = c.site
    return case.path(c), params


def _site_extra(case: Case, role: Optional[str], drop_site: bool) -> Optional[str]:
    """The site a body / form carries for this role, as the screen would send it."""
    if drop_site or not (case.site_for_global and role in GLOBAL):
        return None
    return ctx().site


def _do(case: Case, role: Optional[str], params_override: Optional[dict] = None,
        drop_site: bool = False):
    path, params = _resolve(case, role)
    if params_override is not None:
        params = params_override
    method = case.route.split(" ", 1)[0]
    site = _site_extra(case, role, drop_site)
    if case.upload:
        files, data = case.upload(ctx())
        if site and case.site_in == "form":
            data = {**data, "site_id": site}
        return call(method, path, role, params, files=files, data=data)
    body = case.body(ctx()) if case.body else None
    if site and case.site_in == "body" and isinstance(body, dict):
        body = {**body, "site_id": site}
    return call(method, path, role, params, body=body)


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_download(case: Case):
    roles = [None] if case.anonymous else (case.roles or ROLES)
    problems, ok_roles = [], []
    for role in roles:
        r = _do(case, role)
        who = role or "anonymous"
        if 200 <= r.status_code < 300:
            bad = check_bytes(case.kind, r)
            if bad:
                problems.append(f"{who}: {r.status_code} but {bad}")
            else:
                ok_roles.append(who)
        elif r.status_code == 403:
            continue
        elif r.status_code in case.allow:
            continue
        else:
            problems.append(f"{who}: {r.status_code} {r.text[:240]}")
    assert not problems, f"{case.route} [{case.id}]\n  " + "\n  ".join(problems)
    if not case.admin_may_fail:
        expect = "anonymous" if case.anonymous else "admin"
        assert expect in ok_roles, (
            f"{case.route} [{case.id}]: {expect} never got the file "
            f"(roles that did: {ok_roles or 'none'}) — the seed is wrong or the route is")


# ── 4. the site rule — the operator's bug, as a rule ─────────────────────────
SITE_CASES = [c for c in CASES if not c.anonymous and c.id not in ("training-media",)]


@pytest.mark.parametrize("case", SITE_CASES, ids=[c.id for c in SITE_CASES])
def test_global_role_without_site(case: Case):
    """Called as admin with NO site_id. If the route then needs one, the screen
    has to send it: the case must say so and the caller must pass `site_id`."""
    path, params = _resolve(case, "admin")
    params.pop("site_id", None)
    r = _do(case, "admin", params_override=params, drop_site=True)
    if r.status_code != 422:
        return
    assert case.site_for_global, (
        f"{case.route} needs site_id from a global role (422: {r.text[:200]}) "
        "but the case says the screen does not send it — fix the screen and the case")
    assert case.ui, f"{case.route} needs site_id but names no screen that calls it"
    assert _ui_sends_site(case), (
        f"{case.route} needs site_id from a global role, and the call in {case.ui} does "
        "not send one — this is the 2026-10-09 consumption-form bug")


def _ui_sends_site(case: Case, window: int = 25) -> bool:
    """`site_id` within a few lines of where the screen names this route.

    Searching the whole file proves nothing — `api/hooks.ts` mentions site_id
    two hundred times — so this looks only around the call itself."""
    template = case.route.split(" ", 1)[1]
    stem = template.split("{", 1)[0].rstrip("/") or template
    lines = (ROOT / case.ui).read_text(encoding="utf-8").splitlines()
    hits = [i for i, line in enumerate(lines) if stem in line]
    return any("site_id" in "\n".join(lines[max(0, i - window): i + window]) for i in hits)


def test_site_rule_catches_the_original_bug():
    """Mutation check: the consumption-form caller WITHOUT its site line must
    fail the rule, or the rule is decoration."""
    case = next(c for c in CASES if c.id == "consumption-form")
    src = (ROOT / case.ui).read_text(encoding="utf-8")
    assert _ui_sends_site(case), "the fixed caller should pass"
    broken = "\n".join(line for line in src.splitlines() if "site_id" not in line)
    tmp = ROOT / "tests" / "downloads" / "_mutant_hooks.ts"
    try:
        tmp.write_text(broken, encoding="utf-8")
        mutant = Case(case.route, "mutant", case.path, case.kind,
                      str(tmp.relative_to(ROOT)), site_for_global=True)
        assert not _ui_sends_site(mutant), "the rule did not notice the missing site_id"
    finally:
        tmp.unlink(missing_ok=True)


def test_ui_callers_exist():
    """A case that points at a screen file that is not there is pointing nowhere."""
    missing = sorted({c.ui for c in CASES if c.ui and not (ROOT / c.ui).is_file()})
    assert not missing, f"cases name UI files that do not exist: {missing}"


# ── 5. the list caps the pickers rely on ────────────────────────────────────
_LIST_CALL = re.compile(r"useList\(\s*'(/[a-z0-9/_-]+)'\s*,\s*\{[^}]*\blimit:\s*(\d+)")
_QS_CALL = re.compile(r"'(/[a-z0-9/_-]+)\?limit=(\d+)")


def _route_limit_caps() -> dict[str, int]:
    caps = {}
    for c in _walk(app.routes):
        if "GET" not in c.methods:
            continue
        for q in c.dependant.query_params:
            if q.name != "limit":
                continue
            for m in getattr(q.field_info, "metadata", []) or []:
                le = getattr(m, "le", None)
                if le is not None:
                    caps[c.path] = int(le)
    return caps


def test_frontend_limits_fit_the_route_caps():
    """The Documents page asked /inventory for 600 rows and the OCR page for
    1000, against a cap of 500: both got a 422 and showed an EMPTY picker."""
    caps = _route_limit_caps()
    over = []
    for f in (ROOT / "frontend" / "src").rglob("*.ts*"):
        text = f.read_text(encoding="utf-8")
        for rx in (_LIST_CALL, _QS_CALL):
            for m in rx.finditer(text):
                path, n = m.group(1), int(m.group(2))
                cap = caps.get(path)
                if cap is not None and n > cap:
                    over.append(f"{f.relative_to(ROOT)}: {path} limit {n} > cap {cap}")
    assert not over, "\n".join(over)
