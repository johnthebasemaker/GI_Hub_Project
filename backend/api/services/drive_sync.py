"""
backend/api/services/drive_sync.py — Google Drive → the sync folder (Phase 21c).

Rulings Q21-7..11. The operator backs the workbooks up to the Drive folder
*CNCEC PROJECT Backup*. This module fetches the newest copy of each workbook
the Excel sync reads into the sync folder (the repo root, the folder
`tools/pg_excel_sync.py` reads by default), then the caller runs the sync:
SME kinds commit automatically, the ERP ledger is only DRY-RUN and the
operator presses Commit (Q21-8).

⚠️ READ-ONLY, ONE FOLDER. The token is `drive.readonly` (the operator creates
it with `tools/gdrive_sync.py --auth`, `docs/GDRIVE_SETUP.md`), and the only
query this module ever sends is "files whose parent is THIS folder ID" — never
a name search, so a renamed or duplicated folder cannot redirect it.

⚠️ NO NEW DEPENDENCY. Drive's REST API is three calls (token refresh, list,
download) over `httpx`, which the API already ships; the OAuth sign-in is a
loopback redirect with PKCE (`tools/gdrive_sync.py`). The google-api client
library would be ~10 MB of download on a limited connection for the same three
calls.

⚠️ THE .xlsm → .xlsx CONVERSION IS STRUCTURAL, NEVER AN openpyxl RE-SAVE.
`bulk_import` reads every workbook with `data_only=True` — the values Excel
CACHED when it last saved. openpyxl writes formulas WITHOUT those cached
values, so "open with openpyxl, save as .xlsx" turns every formula cell —
`Current Stock` among them — into an empty cell, with no error. An .xlsm and an
.xlsx are the same zip and differ in three places only (the workbook's content
type, the macro part, the relationship to it); `xlsm_to_xlsx` rewrites those
and copies every other byte, then `verify_same_values` compares every cell of
every sheet and refuses on any difference. Suite 21C proves the re-save loses
the values and the structural conversion keeps them.
"""
from __future__ import annotations

import dataclasses
import datetime as _dt
import hashlib
import io
import json
import os
import re
import shutil
import time
import zipfile
from pathlib import Path
from typing import Any, Callable, Optional

ROOT = Path(__file__).resolve().parents[3]
FOLDER_ID = os.environ.get("GI_DRIVE_FOLDER_ID", "1rhTdX3UuBvwheXZwzBwmCViDkO9EMZR9")
# Secrets live in git-ignored deploy/. GI_DRIVE_SECRETS_DIR points elsewhere —
# the E2E stack sets it to an empty path so a developer's real token is never
# used by a test.
_SECRETS = Path(os.environ.get("GI_DRIVE_SECRETS_DIR") or (ROOT / "deploy"))
CLIENT_PATH = _SECRETS / "gdrive_client.json"
TOKEN_PATH = _SECRETS / "gdrive_token.json"
SYNC_DIR = ROOT                      # where pg_excel_sync reads the workbooks
BACKUP_DIR = ROOT / ".backups" / "workbooks"
# Phase 22a (Q22-6): read-only copies of the DN / MTC / request files, served to
# signed-in users (staff have no Drive access). Git-ignored.
CACHE_DIR = Path(os.environ.get("GI_DRIVE_CACHE_DIR") or (ROOT / ".cache" / "drive"))
MANIFEST = BACKUP_DIR / "last_sync.json"
SCOPE = "https://www.googleapis.com/auth/drive.readonly"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/drive/v3"

XLSM_MAIN = "application/vnd.ms-excel.sheet.macroEnabled.main+xml"
XLSX_MAIN = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
_VBA_PARTS = ("xl/vbaProject.bin", "xl/vbaProjectSignature.bin", "xl/vbaData.xml",
              "xl/_rels/vbaProject.bin.rels")


# ── what to fetch ─────────────────────────────────────────────────────────────
@dataclasses.dataclass(frozen=True)
class Target:
    """One workbook the Excel sync reads. `dest` is the name it is saved as in
    the sync folder (what `tools/pg_excel_sync.WORKBOOKS` looks for)."""
    dest: str
    pattern: str            # regex on the Drive file name (case-insensitive)
    side: str               # "erp" (dry-run, the operator commits) | "sme" (auto)
    by_name_date: bool = False
    required_sheets: tuple[str, ...] = ()

    def matches(self, name: str) -> bool:
        return bool(re.match(self.pattern, name, re.IGNORECASE))


TARGETS: tuple[Target, ...] = (
    # The operator saves it as CNCEC_Inventory_Smart.xlsm (Q21-7); an .xlsx
    # upload of the same workbook is accepted too.
    Target("CNCEC_Inventory.xlsx", r"^CNCEC_Inventory[^/]*\.(xlsm|xlsx)$", "erp",
           required_sheets=("Inventory", "Consumption Log", "Receipt Log", "Return Log")),
    # The Lot Register: a date at the end of the name, e.g.
    # "Rubber & Brick Materials  - CNCEC(04-10-2026).xlsx" (Q21-11, day first).
    Target("Rubber & Brick Materials  - CNCEC.xlsx",
           r"^Rubber\s*&\s*Brick[^/]*CNCEC[^/]*\.xlsx$", "erp", by_name_date=True),
    Target("For_1_SQM.xlsx", r"^For_1_SQM\.xlsx$", "sme"),
    # ⚠️ exact name: "Equipment list Updated as on 06-09-2026.xlsx" is a
    # different workbook and must not win on recency.
    Target("Equipment.xlsx", r"^Equipment\.xlsx$", "sme"),
    Target("Materials_DetailsAvailable_Qty.xlsx", r"^Materials_DetailsAvailable_Qty\.xlsx$", "sme"),
    Target("Manpower_Hour_Details.xlsx", r"^Manpower_Hour_Details\.xlsx$", "sme"),
)

# Day first (ruling Q21-11): (04-10-2026) · (4.10.26) · (020726) · (02072026)
_DATE_SEP = re.compile(r"\((\d{1,2})[-./](\d{1,2})[-./](\d{2}|\d{4})\)")
_DATE_RUN = re.compile(r"\((\d{6}|\d{8})\)")


def name_date(name: str) -> Optional[_dt.date]:
    """The date at the end of a file name, day first; None when there is none
    or it is not a real date (31-02-2026)."""
    m = _DATE_SEP.search(name)
    if m:
        d, mo, y = (int(x) for x in m.groups())
    else:
        m = _DATE_RUN.search(name)
        if not m:
            return None
        g = m.group(1)
        d, mo, y = int(g[:2]), int(g[2:4]), int(g[4:])
    if y < 100:
        y += 2000
    try:
        return _dt.date(y, mo, d)
    except ValueError:
        return None


def is_lock_file(name: str) -> bool:
    """Excel's `~$Book.xlsx` owner files: 165 bytes, never a workbook."""
    return name.startswith("~$")


def pick(target: Target, files: list[dict]) -> tuple[Optional[dict], list[str]]:
    """The file to fetch for one target, and notes about the choice.

    Newest by the DATE IN THE NAME for a dated target (several dated copies
    sit side by side in the folder), else newest by Drive's `modifiedTime`.
    A dated target whose newest name has no readable date falls back to
    `modifiedTime` — and says so."""
    notes: list[str] = []
    cands = [f for f in files if not is_lock_file(f["name"]) and target.matches(f["name"])
             and f.get("mimeType") != "application/vnd.google-apps.folder"]
    if not cands:
        return None, notes
    if target.by_name_date:
        dated = [(name_date(f["name"]), f) for f in cands]
        undated = [f for d, f in dated if d is None]
        if undated:
            notes.append(f"{len(undated)} file(s) with no readable date in the name: "
                         + ", ".join(sorted(f["name"] for f in undated)[:3]))
        with_date = [(d, f) for d, f in dated if d is not None]
        if with_date:
            best = max(with_date, key=lambda p: (p[0], p[1].get("modifiedTime", "")))
            return best[1], notes
        notes.append("no dated copy — the most recently modified one is used")
    return max(cands, key=lambda f: f.get("modifiedTime", "")), notes


def classify(files: list[dict]) -> dict[str, list[str]]:
    """What the folder holds that this sync does NOT use — listed once so the
    operator can see nothing was silently skipped (ruling Q21-10: DN, PO,
    return and follow-up workbooks wait for a later slice)."""
    out = {"lock_files": [], "folders": [], "unused": []}
    for f in files:
        n = f["name"]
        if f.get("mimeType") == "application/vnd.google-apps.folder":
            out["folders"].append(n)
        elif is_lock_file(n):
            out["lock_files"].append(n)
        elif not any(t.matches(n) for t in TARGETS):
            out["unused"].append(n)
    return {k: sorted(v) for k, v in out.items()}


# ── .xlsm → .xlsx, structurally ──────────────────────────────────────────────
def is_xlsm(data: bytes) -> bool:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            return XLSM_MAIN in z.read("[Content_Types].xml").decode("utf-8", "replace")
    except (zipfile.BadZipFile, KeyError):
        return False


def xlsm_to_xlsx(data: bytes) -> bytes:
    """Drop the macros and nothing else. Every other zip member is copied
    byte for byte (same name, same compression), so sheets, formulas, the
    CACHED VALUES the importer reads, styles and validation are untouched."""
    zin = zipfile.ZipFile(io.BytesIO(data))
    names = zin.namelist()
    ct = zin.read("[Content_Types].xml").decode("utf-8")
    ct = ct.replace(XLSM_MAIN, XLSX_MAIN)
    for part in _VBA_PARTS:
        ct = re.sub(r'<Override[^>]*PartName="/' + re.escape(part) + r'"[^>]*/>', "", ct)
    other_bins = [n for n in names if n.lower().endswith(".bin") and n not in _VBA_PARTS]
    if not other_bins:
        ct = re.sub(r'<Default[^>]*Extension="bin"[^>]*ContentType="application/vnd\.ms-office\.vbaProject"[^>]*/>',
                    "", ct)
    rels_name = "xl/_rels/workbook.xml.rels"
    rels = zin.read(rels_name).decode("utf-8") if rels_name in names else None
    if rels is not None:
        rels = re.sub(r'<Relationship[^>]*Type="[^"]*/vbaProject"[^>]*/>', "", rels)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zout:
        for info in zin.infolist():
            if info.filename in _VBA_PARTS:
                continue
            if info.filename == "[Content_Types].xml":
                zout.writestr(info, ct.encode("utf-8"))
            elif info.filename == rels_name and rels is not None:
                zout.writestr(info, rels.encode("utf-8"))
            else:
                zout.writestr(info, zin.read(info.filename))
    return out.getvalue()


def sheet_values(data: bytes) -> dict[str, list[tuple]]:
    """Every sheet's cells as the importer reads them (cached values)."""
    import warnings

    import openpyxl
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")          # "Data Validation extension…"
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    try:
        return {ws.title: [tuple(r) for r in ws.iter_rows(min_row=1, values_only=True)]
                for ws in wb.worksheets}
    finally:
        wb.close()


def verify_same_values(original: bytes, converted: bytes) -> Optional[str]:
    """None when every cell of every sheet reads the same, else the first
    difference in words."""
    a, b = sheet_values(original), sheet_values(converted)
    if list(a) != list(b):
        return f"sheets differ: {list(a)} vs {list(b)}"
    for name in a:
        ra, rb = a[name], b[name]
        if len(ra) != len(rb):
            return f"{name}: {len(ra)} rows vs {len(rb)}"
        for i, (x, y) in enumerate(zip(ra, rb), start=1):
            if x != y:
                return f"{name} row {i} differs"
    return None


def validate(target: Target, data: bytes) -> Optional[str]:
    """None when `data` is a readable workbook carrying the sheets the sync
    needs; else why not. A half-downloaded file must never replace a good one."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            bad = z.testzip()
            if bad:
                return f"corrupt zip member {bad}"
    except zipfile.BadZipFile:
        return "not a workbook (bad zip)"
    try:
        sheets = sheet_values(data) if target.required_sheets else None
    except Exception as e:  # noqa: BLE001 — any unreadable workbook is refused
        return f"openpyxl cannot read it: {type(e).__name__}"
    if sheets is not None:
        have = {s.strip().lower() for s in sheets}
        missing = [s for s in target.required_sheets if s.lower() not in have]
        if missing:
            return f"missing sheet(s): {', '.join(missing)}"
    return None


# ── the Drive client (httpx, three calls) ────────────────────────────────────
class DriveError(RuntimeError):
    pass


class TokenError(DriveError):
    """Google refused the stored refresh token — removed access, or a Testing
    app's 7-day expiry (docs/GDRIVE_SETUP.md, Publish). The banner turns red."""


_TRANSIENT = {"ReadTimeout", "ConnectTimeout", "WriteTimeout", "PoolTimeout",
              "ConnectError", "ReadError", "RemoteProtocolError", "TimeoutException"}


class DriveClient:
    """Read-only Drive v3 over httpx with a stored refresh token."""

    def __init__(self, token_path: Path = TOKEN_PATH, client_path: Path = CLIENT_PATH,
                 http: Any = None, sleep: Callable[[float], None] = time.sleep):
        self.sleep = sleep
        if not token_path.exists():
            raise DriveError(f"no Drive token at {_rel(token_path)} — run "
                             "`tools/gdrive_sync.py --auth` once (docs/GDRIVE_SETUP.md)")
        tok = json.loads(token_path.read_text())
        cli = load_client(client_path)
        self.client_id, self.client_secret = cli["client_id"], cli["client_secret"]
        self.refresh_token = tok["refresh_token"]
        self._access: Optional[str] = None
        self._expires = 0.0
        if http is None:
            import httpx
            http = httpx.Client(timeout=120)
        self.http = http

    # Phase 22a: every call is retried — a 600-file folder over a site link
    # timed out once on the first real crawl (2026-10-07, httpx.ReadTimeout).
    RETRIES = 3
    BACKOFF = (2.0, 6.0)

    def _call(self, method: str, url: str, **kw) -> Any:
        last: Optional[BaseException] = None
        for attempt in range(self.RETRIES):
            try:
                r = getattr(self.http, method)(url, **kw)
                if r.status_code in (429, 500, 502, 503, 504) and attempt < self.RETRIES - 1:
                    last = DriveError(f"Google answered {r.status_code}")
                else:
                    return r
            except Exception as e:  # noqa: BLE001 — httpx transport errors; retried
                if type(e).__name__ not in _TRANSIENT:
                    raise
                last = e
            if attempt < self.RETRIES - 1:
                self.sleep(self.BACKOFF[min(attempt, len(self.BACKOFF) - 1)])
        raise DriveError(f"Drive did not answer after {self.RETRIES} tries "
                         f"({type(last).__name__ if last else 'unknown'})")

    def _token(self) -> str:
        if self._access and time.time() < self._expires - 60:
            return self._access
        r = self._call("post", TOKEN_URL, data={
            "client_id": self.client_id, "client_secret": self.client_secret,
            "refresh_token": self.refresh_token, "grant_type": "refresh_token"})
        if r.status_code != 200:
            raise TokenError(f"Google refused the token refresh ({r.status_code}) — the access "
                             "was removed or expired (a Google app still in Testing ends it after "
                             "7 days); run `tools/gdrive_sync.py --auth` again")
        body = r.json()
        self._access = body["access_token"]
        self._expires = time.time() + float(body.get("expires_in", 3600))
        return self._access

    def list_folder(self, folder_id: str = FOLDER_ID) -> list[dict]:
        out, page = [], None
        while True:
            params = {"q": f"'{folder_id}' in parents and trashed = false",
                      "fields": "nextPageToken, files(id,name,mimeType,modifiedTime,size,md5Checksum)",
                      "pageSize": 200, "supportsAllDrives": "true",
                      "includeItemsFromAllDrives": "true"}
            if page:
                params["pageToken"] = page
            r = self._call("get", f"{API}/files", params=params,
                              headers={"Authorization": f"Bearer {self._token()}"})
            if r.status_code != 200:
                raise DriveError(f"listing the folder failed ({r.status_code})")
            body = r.json()
            out += body.get("files", [])
            page = body.get("nextPageToken")
            if not page:
                return out

    def download(self, file_id: str) -> bytes:
        r = self._call("get", f"{API}/files/{file_id}", params={"alt": "media"},
                          headers={"Authorization": f"Bearer {self._token()}"})
        if r.status_code != 200:
            raise DriveError(f"download failed ({r.status_code})")
        return r.content


def _rel(p: Path) -> str:
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


def load_client(path: Path = CLIENT_PATH) -> dict:
    """The Desktop-app OAuth client the operator downloaded (Google wraps it in
    an "installed" key)."""
    if not path.exists():
        raise DriveError(f"no OAuth client at {_rel(path)} — download it from "
                         "Google Cloud (docs/GDRIVE_SETUP.md, Part 1 step 7)")
    raw = json.loads(path.read_text())
    return raw.get("installed") or raw.get("web") or raw


# ── fetch ─────────────────────────────────────────────────────────────────────
def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_manifest(path: Path = MANIFEST) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


SHRINK_LIMIT = 0.02          # a sheet may lose ≤ 2 % of its rows between copies


def row_counts(data: bytes) -> dict[str, int]:
    """Non-empty rows per sheet, as the importer would read them."""
    try:
        return {name: sum(1 for r in rows if any(c not in (None, "") for c in r))
                for name, rows in sheet_values(data).items()}
    except Exception:  # noqa: BLE001 — validate() already refused unreadable files
        return {}


def shrinkage(prev: dict[str, int], now: dict[str, int],
              limit: float = SHRINK_LIMIT) -> list[str]:
    """Sentences for every sheet that lost more than `limit` of its rows (or
    vanished) since the last accepted copy. Small sheets (< 50 rows) are
    compared by count only when they lose more than 2 rows."""
    out = []
    for sheet, before in sorted(prev.items()):
        after = now.get(sheet)
        if after is None:
            if before >= 5:
                out.append(f"sheet “{sheet}” is gone ({before} rows before)")
            continue
        lost = before - after
        if lost <= 0:
            continue
        if (before >= 50 and lost > before * limit) or (before < 50 and lost > 2):
            out.append(f"“{sheet}” shrank from {before:,} to {after:,} rows")
    return out


def fetch(client: DriveClient, dest_dir: Optional[Path] = None, *, folder_id: str = FOLDER_ID,
          manifest_path: Optional[Path] = None, backup_dir: Optional[Path] = None,
          now: Optional[Callable[[], _dt.datetime]] = None,
          accept_shrink: bool = False, files: Optional[list[dict]] = None) -> dict:
    """Bring the sync folder up to date with Drive. Returns a report:

        changed    [{dest, source, modifiedTime, converted, side}]
        unchanged  [dest]           (same Drive file + modifiedTime as last run)
        missing    [dest]           (no file in Drive matches)
        refused    [{dest, source, reason}]   (kept the old file)
        notes      [str]
        folder     classify() of what is not used

    ⚠️ ATOMIC PER FILE: download → (convert + verify) → validate → write a temp
    file beside the target → move the old one to .backups/workbooks/<stamp>/ →
    `os.replace`. A failure at any step leaves the previous file in place.

    Phase 22a — "check the files arrive correctly": the download must match
    Drive's own md5 (when Drive has one), and every changed workbook reports
    its sheet row counts; a sheet that SHRANK by more than 2 % since the last
    accepted copy is refused (a half-saved upload, the wrong file) unless the
    operator accepts it (`accept_shrink`)."""
    # resolved at CALL time (module attributes), so a test can point them elsewhere
    dest_dir = dest_dir if dest_dir is not None else SYNC_DIR
    manifest_path = manifest_path if manifest_path is not None else MANIFEST
    backup_dir = backup_dir if backup_dir is not None else BACKUP_DIR
    stamp = (now or _dt.datetime.now)().strftime("%Y-%m-%d_%H%M%S")
    if files is None:
        files = client.list_folder(folder_id)
    manifest = load_manifest(manifest_path)
    rep: dict[str, Any] = {"at": stamp, "changed": [], "unchanged": [], "missing": [],
                           "refused": [], "notes": [], "folder": classify(files)}
    for t in TARGETS:
        f, notes = pick(t, files)
        rep["notes"] += [f"{t.dest}: {n}" for n in notes]
        if f is None:
            rep["missing"].append(t.dest)
            continue
        prev = manifest.get(t.dest) or {}
        dest = dest_dir / t.dest
        if (prev.get("id") == f["id"] and prev.get("modifiedTime") == f.get("modifiedTime")
                and dest.exists()):
            rep["unchanged"].append(t.dest)
            continue
        data = client.download(f["id"])
        want_md5 = f.get("md5Checksum")
        if want_md5 and hashlib.md5(data).hexdigest() != want_md5:   # noqa: S324 — integrity, not security
            rep["refused"].append({"dest": t.dest, "source": f["name"],
                                   "reason": "the download does not match Drive's checksum "
                                             "(incomplete) — try again"})
            continue
        converted = False
        if is_xlsm(data):
            out = xlsm_to_xlsx(data)
            diff = verify_same_values(data, out)
            if diff:
                rep["refused"].append({"dest": t.dest, "source": f["name"],
                                       "reason": f"conversion changed a value ({diff})"})
                continue
            data, converted = out, True
        why = validate(t, data)
        if why:
            rep["refused"].append({"dest": t.dest, "source": f["name"], "reason": why})
            continue
        rows = row_counts(data)
        shrunk = shrinkage(prev.get("rows") or {}, rows)
        if shrunk and not accept_shrink:
            rep["refused"].append({"dest": t.dest, "source": f["name"], "shrink": True,
                                   "reason": "; ".join(shrunk) + " — the previous file was kept "
                                             "(press “Accept the smaller file” if that is right)"})
            continue
        _swap_in(dest, data, backup_dir / stamp, also_move=_stale_twins(t, dest_dir))
        manifest[t.dest] = {"id": f["id"], "name": f["name"], "modifiedTime": f.get("modifiedTime"),
                            "sha256": _sha(data), "converted_from_xlsm": converted, "at": stamp,
                            "rows": rows}
        rep["changed"].append({"dest": t.dest, "source": f["name"], "side": t.side,
                               "modifiedTime": f.get("modifiedTime"), "converted": converted,
                               "rows": rows, "prev_rows": prev.get("rows") or {},
                               "md5_checked": bool(want_md5)})
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=1, sort_keys=True))
    return rep


def _stale_twins(t: Target, dest_dir: Path) -> list[Path]:
    """Other copies of a DATED target in the sync folder. `pg_excel_sync` reads
    the Lot Register by pattern and takes the newest by mtime, so a leftover
    `(15-09-2026)` copy could win after a clock change — only one may stay."""
    if not t.by_name_date:
        return []
    return [p for p in dest_dir.glob("*.xlsx")
            if p.name != t.dest and not is_lock_file(p.name) and t.matches(p.name)]


def _swap_in(dest: Path, data: bytes, backup: Path, also_move: list[Path]) -> None:
    tmp = dest.with_name(f".{dest.name}.drive-tmp")
    tmp.write_bytes(data)
    for old in [dest, *also_move]:
        if old.exists():
            backup.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old), str(backup / old.name))
    os.replace(tmp, dest)


# ── after the fetch: run the Excel sync ──────────────────────────────────────
def sync_commands(rep: dict, site: str = "CNCEC") -> dict[str, list[str]]:
    """Which `pg_excel_sync` runs this fetch calls for (ruling Q21-8):
    SME files changed → commit the SME kinds; ERP files changed → DRY-RUN the
    ERP kinds (the operator commits from the Admin Console)."""
    sides = {c["side"] for c in rep.get("changed", [])}
    base = ["tools/pg_excel_sync.py", "--site", site]
    cmds: dict[str, list[str]] = {}
    if "sme" in sides:
        cmds["sme_commit"] = base + ["--commit"]
    if "erp" in sides:
        cmds["erp_dry_run"] = base + ["--kinds", "inventory,ledger,lots"]
    return cmds


ERP_COMMIT = ["tools/pg_excel_sync.py", "--site", "CNCEC", "--kinds", "inventory,ledger,lots",
              "--commit"]


def summarise_sync_output(text_out: str) -> list[str]:
    """The lines worth a notification from a pg_excel_sync run: its totals,
    warnings and errors — never the whole log."""
    keep = re.compile(r"(❌|⚠|warning|lot problem|row\(s\) name a lot|inserted|updated|"
                      r"COMMIT|DRY-RUN|rejects?)", re.IGNORECASE)
    lines = [ln.strip() for ln in text_out.splitlines() if keep.search(ln)]
    return lines[:40]


# ── Phase 22a: the subfolders (DN, MTC, Pending Material Follow-up) ──────────
# Index, don't import (plan §1.2): each file is a row in `drive_files` and a
# read-only copy in CACHE_DIR; later slices parse the names (22b DN, 22c MTC,
# 22d requests). Matched by EXACT folder name under the one backup folder —
# never a Drive-wide search. Waste Disposal is ignored (ruling Q22-22).
SUBFOLDERS: dict[str, str] = {
    "dn for cncec": "dn",
    "mtc": "mtc",
    "pending material follow-up": "pending",
    # Phase 23d (ruling Q23-6): pictures named by GI code — the operator makes it
    "material images": "images",
}
# Phase 23d — workbooks in the ROOT that are indexed and cached like a
# subfolder's files: the material catalogue and the site plant & tools list
# (ruling Q23-9; `Equipment.xlsx` — the SME tanks — is not this one).
ROOT_FILES: tuple[tuple[str, str], ...] = (
    (r"^all\s*material\s*codes.*\.xlsx?$", "catalogue"),
    (r"^equipment\s*list.*\.xlsx?$", "equipment"),
)
IGNORED_FOLDERS = ("waste disposal",)
FOLDER_MIME = "application/vnd.google-apps.folder"
CACHE_BUDGET_S = 600          # one run downloads for at most 10 min; the rest next run


def subfolder_kind(name: str) -> Optional[str]:
    return SUBFOLDERS.get(re.sub(r"\s+", " ", name.strip().lower()))


def crawl(client: DriveClient, root_files: list[dict], *, max_depth: int = 2) -> dict:
    """{files: [{…drive file, kind, folder}], ignored: [folder], unknown: [folder]}
    for the subfolders this sync reads — one level deep plus any nested
    folder inside them (photos are sometimes grouped)."""
    out: dict[str, list] = {"files": [], "ignored": [], "unknown": []}

    def walk(folder: dict, kind: str, path: str, depth: int) -> None:
        for f in client.list_folder(folder["id"]):
            if f.get("mimeType") == FOLDER_MIME:
                if depth < max_depth:
                    walk(f, kind, f"{path}/{f['name']}", depth + 1)
                continue
            if is_lock_file(f["name"]):
                continue
            out["files"].append({**f, "kind": kind, "folder": path})

    for f in root_files:
        if f.get("mimeType") != FOLDER_MIME:
            if is_lock_file(f["name"]):
                continue
            for rx, kind in ROOT_FILES:
                if re.match(rx, f["name"].strip(), re.IGNORECASE):
                    out["files"].append({**f, "kind": kind, "folder": "(root)"})
                    break
            continue
        kind = subfolder_kind(f["name"])
        if kind:
            walk(f, kind, f["name"], 1)
        elif f["name"].strip().lower() in IGNORED_FOLDERS:
            out["ignored"].append(f["name"])
        else:
            out["unknown"].append(f["name"])
    return out


def cache_path(f: dict, cache_dir: Optional[Path] = None) -> Path:
    """One file per Drive id — a rename in Drive does not orphan the copy."""
    cache_dir = cache_dir if cache_dir is not None else CACHE_DIR
    ext = Path(f["name"]).suffix.lower()[:8] or ".bin"
    return cache_dir / f["kind"] / f"{f['id']}{ext}"


def cache_files(client: DriveClient, files: list[dict], known: dict[str, dict], *,
                cache_dir: Optional[Path] = None, budget_s: float = CACHE_BUDGET_S,
                clock: Callable[[], float] = time.monotonic) -> list[dict]:
    """Download what is new or changed (by md5, else modifiedTime) into the
    cache, verified against Drive's md5. Returns one row per file:
    {…file, cache_path, state: new|changed|same|failed|later, error}."""
    t0 = clock()
    rows = []
    for f in files:
        prev = known.get(f["id"]) or {}
        dest = cache_path(f, cache_dir)
        same = dest.exists() and (
            (f.get("md5Checksum") and prev.get("md5") == f.get("md5Checksum"))
            or (not f.get("md5Checksum") and prev.get("modified_time") == f.get("modifiedTime")))
        row = {**f, "cache_path": str(dest), "error": None}
        if same:
            rows.append({**row, "state": "same"})
            continue
        if clock() - t0 > budget_s:
            rows.append({**row, "state": "later", "cache_path": str(dest) if dest.exists() else None})
            continue
        try:
            data = client.download(f["id"])
            if f.get("md5Checksum") and hashlib.md5(data).hexdigest() != f["md5Checksum"]:  # noqa: S324
                raise DriveError("download does not match Drive's checksum")
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(f".{dest.name}.tmp")
            tmp.write_bytes(data)
            os.replace(tmp, dest)
            rows.append({**row, "state": "changed" if prev else "new", "size": len(data)})
        except DriveError as e:
            rows.append({**row, "state": "failed", "error": str(e),
                         "cache_path": str(dest) if dest.exists() else None})
    return rows


# ── Phase 22a: ERP auto-commit when the dry run only ADDS (ruling Q22-1) ─────
# Keys of `pg_excel_sync --report-json` that mean "an existing row would
# change or go". Any non-zero one holds the commit for the operator.
_LEDGER_EDITS = ("updates", "corrections", "conflicts", "vanished", "relabelled")


def additions_only(report: Optional[dict]) -> tuple[bool, list[str]]:
    """(True, []) when the ERP dry run would only INSERT rows; else (False,
    why) — edits, removals, rejects or lot changes wait for the operator."""
    if not report:
        return False, ["no dry-run report"]
    why: list[str] = []
    kinds = report.get("kinds") or {}
    inv = kinds.get("inventory") or {}
    if inv.get("updates"):
        why.append(f"inventory: {inv['updates']} item(s) edited")
    if inv.get("rejects"):
        why.append(f"inventory: {inv['rejects']} row(s) rejected")
    for sec, v in ((kinds.get("ledger") or {}).items()):
        for k in _LEDGER_EDITS:
            if v.get(k):
                why.append(f"{sec}: {v[k]} {k}")
    lots = kinds.get("lots") or {}
    if lots.get("lot_changes"):
        why.append(f"lots: {lots['lot_changes']} lot(s) changed")
    if report.get("rejects"):
        why.append(f"{report['rejects']} row(s) rejected")
    if report.get("error"):
        why.append(str(report["error"]))
    return (not why), why


def inserts_total(report: Optional[dict]) -> int:
    kinds = (report or {}).get("kinds") or {}
    n = (kinds.get("inventory") or {}).get("inserts", 0)
    n += sum(v.get("inserts", 0) for v in (kinds.get("ledger") or {}).values())
    return n + (kinds.get("lots") or {}).get("lots_new", 0)


# ── Phase 22a: the schedule (ruling Q22-2: 07:30 and 19:30, set in the UI) ───
DEFAULT_SCHEDULE = {"enabled": True, "times": ["07:30", "19:30"], "auto_commit_additions": True}
_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
CATCH_UP = _dt.timedelta(hours=3)     # a slot missed while the server was down runs ≤ 3 h late


def clean_times(times: list[str]) -> list[str]:
    """Validated, de-duplicated, sorted HH:MM list (1–6 times). ValueError
    with a sentence when one is not a time."""
    out = []
    for t in times:
        t = str(t).strip()
        if len(t) == 4 and t[1] == ":":
            t = "0" + t
        if not _HHMM.match(t):
            raise ValueError(f"“{t}” is not a time (use HH:MM, 24-hour)")
        out.append(t)
    out = sorted(set(out))
    if not 1 <= len(out) <= 6:
        raise ValueError("choose between 1 and 6 times a day")
    return out


def parse_schedule(raw: Optional[str], env_default: Optional[str] = None) -> dict:
    """The stored schedule, or the default. `GI_DRIVE_SYNC_AT` (Phase 21c's one
    daily time) is still honoured as the default's first time when set."""
    sch = dict(DEFAULT_SCHEDULE)
    if env_default:
        try:
            sch["times"] = clean_times([env_default] + [t for t in sch["times"] if t != "07:30"])
        except ValueError:
            pass
    if raw:
        try:
            v = json.loads(raw)
            if isinstance(v, dict):
                sch.update({k: v[k] for k in ("enabled", "times", "auto_commit_additions") if k in v})
                sch["times"] = clean_times(sch["times"])
        except (ValueError, TypeError):
            sch = dict(DEFAULT_SCHEDULE)
    return sch


def due_slots(now: _dt.datetime, times: list[str]) -> list[tuple[str, _dt.datetime]]:
    """The (HH:MM, slot datetime) whose time has come within the catch-up
    window — today's, or yesterday's late slot just after midnight."""
    out = []
    for t in times:
        hh, mm = (int(x) for x in t.split(":"))
        for day in (now.date(), now.date() - _dt.timedelta(days=1)):
            slot = _dt.datetime.combine(day, _dt.time(hh, mm))
            if slot <= now < slot + CATCH_UP:
                out.append((t, slot))
    return out


def next_slot(now: _dt.datetime, times: list[str]) -> Optional[_dt.datetime]:
    best = None
    for t in times:
        hh, mm = (int(x) for x in t.split(":"))
        slot = _dt.datetime.combine(now.date(), _dt.time(hh, mm))
        if slot <= now:
            slot += _dt.timedelta(days=1)
        best = slot if best is None or slot < best else best
    return best


STALE_AFTER = _dt.timedelta(hours=26)    # ruling Q22-4


def freshness_status(*, practice: bool, connected: bool, running: bool, last: Optional[dict],
                     last_ok_at: Optional[str], now: _dt.datetime) -> str:
    """One word for the top-bar banner:
    practice · not_connected · running · token · failed · never · stale ·
    pending_commit · ok."""
    if practice:
        return "practice"
    if not connected:
        return "not_connected"
    if running:
        return "running"
    if last and last.get("token_error"):
        return "token"
    if last and last.get("ok") is False:
        return "failed"
    if not last_ok_at:
        return "never"
    try:
        if now - _dt.datetime.fromisoformat(last_ok_at) > STALE_AFTER:
            return "stale"
    except ValueError:
        return "never"
    if last and last.get("erp_pending"):
        return "pending_commit"
    return "ok"
