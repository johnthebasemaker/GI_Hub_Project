"""
backend/api/services/tutorial_staleness.py — Phase 14d: which recorded
tutorials the app may have outgrown.

Every tutorial manifest (`docs/tutorials/out/*.manifest.json`, P12-5) records
the ROUTES it walked and the git SHA it was recorded at. That is enough to
answer "has anything that tutorial shows changed?" mechanically:

  route  → the page component `frontend/src/App.tsx` renders for it
         → that page's own relative imports (one level: the cards and modals a
           page is built from, e.g. ExecutionPage → SmeLinkCard)
  files  → `git diff --name-only <recorded sha> HEAD -- <files>`

A tutorial whose files changed is POSSIBLY STALE, with the files named.

⚠️ IT NEVER GATES (Phase 12: a tutorial render is not a gate). It is a warning
with a reason, delivered to ADMINS ONLY (ruling Q14-14).

⚠️ "CANNOT TELL" IS ITS OWN ANSWER, NEVER "FRESH" (rule 16's reasoning). No
git (the API image ships without `.git`), a SHA this clone does not have, or a
route with no page are reported as `unknown`, with the reason.

⚠️ RE-RECORDING IS `tools/generate_tutorial.py`, not `make_tutorial_db.py` —
that one only rebuilds the synthetic dataset. The flag names the right command.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[3]
SETTING_KEY = "tutorial_staleness"

_LAZY = re.compile(r"const\s+(\w+)\s*=\s*lazy\(\s*\(\)\s*=>\s*import\(\s*'([^']+)'\s*\)\s*\)")
_STATIC = re.compile(r"^import\s+(\w+)\s+from\s+'(\.[^']+)'", re.M)
_ROUTE = re.compile(r"<Route\s+(index|path=\"([^\"]*)\")\s+element=\{<(\w+)\s*/>\}")
_REL_IMPORT = re.compile(r"""^import\s+(?:[^'"]+\s+from\s+)?['"](\.{1,2}/[^'"]+)['"]""", re.M)


def _resolve(base: Path, spec: str) -> Optional[Path]:
    p = (base / spec).resolve()
    for cand in (p, p.with_suffix(".tsx"), p.with_suffix(".ts"),
                 p / "index.tsx", p / "index.ts"):
        if cand.is_file():
            return cand
    return None


def route_table(root: Path = ROOT) -> dict[str, Path]:
    """`/execution` → frontend/src/pages/ExecutionPage.tsx, read from App.tsx."""
    src_dir = root / "frontend" / "src"
    src = (src_dir / "App.tsx").read_text(encoding="utf-8")
    comps: dict[str, Path] = {}
    for m in list(_LAZY.finditer(src)) + list(_STATIC.finditer(src)):
        f = _resolve(src_dir, m.group(2))
        if f:
            comps[m.group(1)] = f
    out: dict[str, Path] = {}
    for m in _ROUTE.finditer(src):
        path = "/" if m.group(1) == "index" else "/" + (m.group(2) or "").strip("/")
        comp = comps.get(m.group(3))
        if comp:
            out[path] = comp
    return out


def _match(route: str, table: dict[str, Path]) -> Optional[Path]:
    route = route.rstrip("/") or "/"
    if route in table:
        return table[route]
    for pattern, f in table.items():          # /stock/material/:sap
        rx = "^" + re.sub(r":\w+", "[^/]+", pattern) + "$"
        if re.match(rx, route):
            return f
    return None


# ⚠️ PLUMBING IS NOT WATCHED. `api/client.ts` or `lib/units.ts` change for
# reasons no viewer could see, and every page imports them — watching them
# would call every tutorial stale after every commit, and a warning that is
# always on is one nobody reads. Only files that RENDER are watched.
_PLUMBING = ("api", "lib", "auth", "config", "offline", "hooks", "utils", "types")


def _renders(f: Path, src_dir: Path) -> bool:
    rel = f.relative_to(src_dir).parts
    return f.suffix == ".tsx" and not (rel and rel[0] in _PLUMBING)


def page_files(page: Path, root: Path = ROOT) -> list[Path]:
    """The page and the components it imports directly (one level)."""
    src_dir = (root / "frontend" / "src").resolve()
    files = [page]
    for m in _REL_IMPORT.finditer(page.read_text(encoding="utf-8")):
        f = _resolve(page.parent, m.group(1))
        if f and f not in files and src_dir in f.parents and _renders(f, src_dir):
            files.append(f)
    return files


def _git(*args: str, root: Path = ROOT) -> Optional[str]:
    try:
        r = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def git_available(root: Path = ROOT) -> bool:
    return _git("rev-parse", "HEAD", root=root) is not None


def check_manifest(m: dict, table: dict[str, Path], root: Path = ROOT) -> dict:
    tid = m.get("tutorial_id") or m.get("training_module_key")
    routes = list(dict.fromkeys((m.get("routes") or {}).get("declared", [])
                                + (m.get("routes") or {}).get("visited", [])))
    sha = (m.get("git") or {}).get("sha")
    base = {"tutorial": tid, "title": m.get("title"),
            "module_key": m.get("training_module_key"), "recorded_sha": sha,
            "recorded_at": m.get("generated_at"), "routes": routes,
            "rerecord": f".venv/bin/python tools/generate_tutorial.py --script {m.get('script_path') or '?'} --force"}
    files: list[Path] = []
    missing = []
    for r in routes:
        page = _match(r, table)
        if page is None:
            missing.append(r)
            continue
        files += [f for f in page_files(page, root) if f not in files]
    if not sha or _git("cat-file", "-e", f"{sha}^{{commit}}", root=root) is None:
        return {**base, "status": "unknown",
                "reason": "this clone does not have the commit it was recorded at"
                if sha else "the manifest records no git SHA", "changed": []}
    if not files:
        return {**base, "status": "unknown",
                "reason": f"no page renders {', '.join(missing) or 'its routes'}", "changed": []}
    rr = root.resolve()
    rel = [str(f.resolve().relative_to(rr)) for f in files]
    diff = _git("diff", "--name-only", sha, "HEAD", "--", *rel, root=root)
    if diff is None:
        return {**base, "status": "unknown", "reason": "git diff failed", "changed": []}
    changed = [x for x in diff.splitlines() if x.strip()]
    return {**base, "status": "possibly_stale" if changed else "current",
            "changed": changed, "watched": rel,
            "reason": (f"{len(changed)} file(s) it shows changed since it was recorded"
                       if changed else "nothing it shows has changed"),
            **({"unmapped_routes": missing} if missing else {})}


def scan(out_dir: Optional[Path] = None, *, announced: Optional[dict[str, str]] = None,
         root: Path = ROOT) -> dict:
    """Every manifest, checked. `announced` maps module_key → announcement key
    for announcements that declared `rerender: true` — flagged explicitly."""
    out_dir = out_dir or root / "docs" / "tutorials" / "out"
    if not git_available(root):
        return {"available": False, "reason": "git is not available here (the API "
                "image ships without .git) — run tools/tutorial_staleness.py on the "
                "deploy host", "items": []}
    table = route_table(root)
    items = []
    for p in sorted(out_dir.glob("*.manifest.json")):
        try:
            m = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            items.append({"tutorial": p.name, "status": "unknown",
                          "reason": f"unreadable manifest: {e}", "changed": []})
            continue
        it = check_manifest(m, table, root)
        key = it.get("module_key") or it.get("tutorial")
        if announced and key in announced:
            it["announced_rerender"] = announced[key]
            if it["status"] == "current":
                it["status"], it["reason"] = "possibly_stale", (
                    f"announcement '{announced[key]}' says it needs re-recording")
        items.append(it)
    stale = [i for i in items if i["status"] == "possibly_stale"]
    head = (_git("rev-parse", "HEAD", root=root) or "").strip()
    fp = hashlib.sha256(json.dumps(
        sorted((i["tutorial"], tuple(i.get("changed") or [])) for i in stale)).encode()).hexdigest()[:16]
    return {"available": True, "head": head, "items": items, "stale": len(stale),
            "unknown": sum(1 for i in items if i["status"] == "unknown"),
            "fingerprint": fp}


def summary_line(result: dict) -> str:
    stale = [i for i in result.get("items", []) if i["status"] == "possibly_stale"]
    if not stale:
        return "No tutorial shows a page that has changed since it was recorded."
    parts = []
    for i in stale[:3]:
        f = (i.get("changed") or ["(announced)"])[0].rsplit("/", 1)[-1]
        parts.append(f"{i['tutorial']} — {f} changed")
    more = f" and {len(stale) - 3} more" if len(stale) > 3 else ""
    return f"{len(stale)} tutorial(s) may be out of date: " + "; ".join(parts) + more


async def record(session, result: dict, *, notify_admins: bool = True) -> bool:
    """Store the result (the Training Hub admin view reads it) and ring the
    ADMIN bell — once per distinct finding: re-running on an unchanged tree
    does not ring it again. Returns True when the bell rang."""
    from sqlalchemy import text
    from .notifications import notify
    prev = (await session.execute(text("SELECT value FROM app_settings WHERE key = :k"),
                                  {"k": SETTING_KEY})).scalar()
    prev_fp = None
    if prev:
        try:
            prev_fp = json.loads(prev).get("fingerprint")
        except ValueError:
            prev_fp = None
    from datetime import datetime, timezone
    result = {**result, "checked_at": datetime.now(timezone.utc).isoformat()}
    await session.execute(text(
        "INSERT INTO app_settings (key, value) VALUES (:k, :v) "
        "ON CONFLICT (key) DO UPDATE SET value = excluded.value"),
        {"k": SETTING_KEY, "v": json.dumps(result)})
    if (notify_admins and result.get("available") and result.get("stale")
            and result.get("fingerprint") != prev_fp):
        await notify(session, event_key="tutorial_stale", severity="warning",
                     title="Tutorials may be out of date", body=summary_line(result),
                     recipient_role="admin", link_page="/training",
                     related_table="training_modules", related_ref=result["fingerprint"])
        return True
    return False


async def last(session) -> Optional[dict]:
    from sqlalchemy import text
    v = (await session.execute(text("SELECT value FROM app_settings WHERE key = :k"),
                               {"k": SETTING_KEY})).scalar()
    try:
        return json.loads(v) if v else None
    except ValueError:
        return None
