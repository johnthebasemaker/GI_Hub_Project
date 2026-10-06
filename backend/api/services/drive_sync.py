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
BACKUP_DIR = ROOT / ".backups" / "workbooks"
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


class DriveClient:
    """Read-only Drive v3 over httpx with a stored refresh token."""

    def __init__(self, token_path: Path = TOKEN_PATH, client_path: Path = CLIENT_PATH,
                 http: Any = None):
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

    def _token(self) -> str:
        if self._access and time.time() < self._expires - 60:
            return self._access
        r = self.http.post(TOKEN_URL, data={
            "client_id": self.client_id, "client_secret": self.client_secret,
            "refresh_token": self.refresh_token, "grant_type": "refresh_token"})
        if r.status_code != 200:
            raise DriveError(f"Google refused the token refresh ({r.status_code}) — the access "
                             "may have been removed; run `tools/gdrive_sync.py --auth` again")
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
            r = self.http.get(f"{API}/files", params=params,
                              headers={"Authorization": f"Bearer {self._token()}"})
            if r.status_code != 200:
                raise DriveError(f"listing the folder failed ({r.status_code})")
            body = r.json()
            out += body.get("files", [])
            page = body.get("nextPageToken")
            if not page:
                return out

    def download(self, file_id: str) -> bytes:
        r = self.http.get(f"{API}/files/{file_id}", params={"alt": "media"},
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


def fetch(client: DriveClient, dest_dir: Path = ROOT, *, folder_id: str = FOLDER_ID,
          manifest_path: Path = MANIFEST, backup_dir: Path = BACKUP_DIR,
          now: Optional[Callable[[], _dt.datetime]] = None) -> dict:
    """Bring the sync folder up to date with Drive. Returns a report:

        changed    [{dest, source, modifiedTime, converted, side}]
        unchanged  [dest]           (same Drive file + modifiedTime as last run)
        missing    [dest]           (no file in Drive matches)
        refused    [{dest, source, reason}]   (kept the old file)
        notes      [str]
        folder     classify() of what is not used

    ⚠️ ATOMIC PER FILE: download → (convert + verify) → validate → write a temp
    file beside the target → move the old one to .backups/workbooks/<stamp>/ →
    `os.replace`. A failure at any step leaves the previous file in place."""
    stamp = (now or _dt.datetime.now)().strftime("%Y-%m-%d_%H%M%S")
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
        _swap_in(dest, data, backup_dir / stamp, also_move=_stale_twins(t, dest_dir))
        manifest[t.dest] = {"id": f["id"], "name": f["name"], "modifiedTime": f.get("modifiedTime"),
                            "sha256": _sha(data), "converted_from_xlsm": converted, "at": stamp}
        rep["changed"].append({"dest": t.dest, "source": f["name"], "side": t.side,
                               "modifiedTime": f.get("modifiedTime"), "converted": converted})
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
