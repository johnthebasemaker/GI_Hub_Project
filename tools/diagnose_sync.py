#!/usr/bin/env python3
"""diagnose_sync.py — backend/frontend sync doctor for GI Hub v2.

Read-only diagnostics for the classic "the app isn't syncing" report:
checks every link in the chain a browser entry travels — Vite dev server →
/api proxy → FastAPI → PostgreSQL — plus the offline-queue replay contract
and the PWA service-worker build. Run it FIRST when someone reports stale
data, stuck offline entries, or a dead portal.

Usage (repo root, venv active):

    .venv/bin/python tools/diagnose_sync.py                 # human report
    .venv/bin/python tools/diagnose_sync.py --json          # machine output
    .venv/bin/python tools/diagnose_sync.py --api http://127.0.0.1:8000 \
        --web http://localhost:5173                         # custom ports

Exit code 0 = every check passed (warnings allowed); 1 = at least one FAIL.
The script NEVER writes to any database and needs no credentials — every
probe is an unauthenticated endpoint or a metadata query.

Documented in docs/DEBUGGING.md (the one-stop gate + diagnostics index).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

RESULTS: list[dict] = []


def record(name: str, ok: bool | None, detail: str) -> None:
    """ok: True=PASS, False=FAIL, None=WARN (non-blocking)."""
    RESULTS.append({"check": name, "status":
                    "PASS" if ok else ("WARN" if ok is None else "FAIL"),
                    "detail": detail})


def _get(url: str, timeout: float = 5.0) -> tuple[int, str]:
    req = urllib.request.Request(url, headers={"User-Agent": "gi-sync-doctor"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (localhost)
        return r.status, r.read().decode("utf-8", "replace")


def check_api(api: str) -> None:
    """FastAPI liveness + its own DB connectivity report (/health)."""
    try:
        status, body = _get(f"{api}/health")
        j = json.loads(body)
        db_ok = bool(j.get("database") in ("ok", True) or j.get("db") in ("ok", True)
                     or j.get("status") in ("ok", "healthy"))
        record("api.health", status == 200 and db_ok,
               f"{api}/health → {status} {body[:120]}")
    except Exception as e:  # noqa: BLE001
        record("api.health", False,
               f"{api}/health unreachable ({e}) — is ./run_api.sh running?")


def check_db_head() -> None:
    """Alembic: the live DB revision must be the single script head."""
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory
        cfg = Config(os.path.join(ROOT, "backend", "alembic.ini"))
        cfg.set_main_option("script_location", os.path.join(ROOT, "backend", "alembic"))
        heads = ScriptDirectory.from_config(cfg).get_heads()
        if len(heads) != 1:
            record("db.alembic-single-head", False, f"multiple heads: {heads}")
            return
        record("db.alembic-single-head", True, f"script head {heads[0]}")
        url = os.environ.get("DATABASE_URL", "")
        if not url:
            record("db.revision-matches", None,
                   "DATABASE_URL not set — skipped live-revision comparison "
                   "(export it to compare the running DB against the head)")
            return
        import sqlalchemy as sa
        eng = sa.create_engine(url.replace("+asyncpg", "+psycopg2"))
        with eng.connect() as c:
            row = c.execute(sa.text("SELECT version_num FROM alembic_version")).scalar()
        record("db.revision-matches", row == heads[0],
               f"db={row} script={heads[0]}")
    except Exception as e:  # noqa: BLE001
        record("db.revision-matches", False, f"alembic/DB probe failed: {e}")


def check_web(web: str, api: str) -> None:
    """Vite dev server up + its /api proxy actually reaches FastAPI."""
    try:
        status, body = _get(f"{web}/")
        record("web.vite", status == 200 and "<div id=\"root\"" in body or status == 200,
               f"{web}/ → {status}")
    except Exception as e:  # noqa: BLE001
        record("web.vite", False,
               f"{web} unreachable ({e}) — is `npm run dev --prefix frontend` running? "
               "NOTE: Vite may bind IPv6 ::1 only; try http://localhost not 127.0.0.1")
        return
    try:
        status, body = _get(f"{web}/api/health")
        record("web.api-proxy", status == 200,
               f"{web}/api/health → {status} (Vite /api → {api} proxy)")
    except Exception as e:  # noqa: BLE001
        record("web.api-proxy", False,
               f"{web}/api/health failed ({e}) — proxy target down or "
               "VITE_API_PROXY mis-set; offline entries will queue forever")


def check_offline_contract(api: str) -> None:
    """The offline queue replays with X-Offline-Replay: 1 — the header must be
    ACCEPTED (CORS/middleware must not reject it). An OPTIONS/GET probe is
    enough to prove the middleware chain doesn't 4xx on the header."""
    try:
        req = urllib.request.Request(f"{api}/health",
                                     headers={"X-Offline-Replay": "1",
                                              "User-Agent": "gi-sync-doctor"})
        with urllib.request.urlopen(req, timeout=5) as r:
            record("offline.replay-header", r.status == 200,
                   f"GET /health with X-Offline-Replay → {r.status}")
    except Exception as e:  # noqa: BLE001
        record("offline.replay-header", False, f"header probe failed: {e}")


def check_pwa_build() -> None:
    """dist/sw.js must exist after a build — without it installed PWAs keep
    serving their cached bundle and users see STALE data after deploys."""
    dist = os.path.join(ROOT, "frontend", "dist")
    sw = os.path.join(dist, "sw.js")
    if not os.path.isdir(dist):
        record("pwa.build", None, "frontend/dist absent — run `npm run build "
               "--prefix frontend` (dev-mode-only machines can ignore)")
    else:
        record("pwa.build", os.path.isfile(sw),
               "dist/sw.js present" if os.path.isfile(sw)
               else "dist exists but sw.js missing — PWA auto-update broken")


def check_ai_ro_role() -> None:
    """After every mirror reload the gi_ai_ro role loses its grants — a known
    trap (ARCHITECTURE §1). Probe only when GI_AI_RO_URL is set."""
    url = os.environ.get("GI_AI_RO_URL", "")
    if not url:
        record("db.gi_ai_ro", None, "GI_AI_RO_URL not set — skipped "
               "(re-run backend/scripts/create_ai_readonly_role.sql after reloads)")
        return
    try:
        import sqlalchemy as sa
        eng = sa.create_engine(url)
        with eng.connect() as c:
            c.execute(sa.text('SELECT 1 FROM inventory LIMIT 1'))
        record("db.gi_ai_ro", True, "read-only AI role can SELECT")
    except Exception as e:  # noqa: BLE001
        record("db.gi_ai_ro", False,
               f"gi_ai_ro probe failed ({e}) — re-run create_ai_readonly_role.sql")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--api", default="http://127.0.0.1:8000")
    ap.add_argument("--web", default="http://localhost:5173")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    check_api(args.api)
    check_db_head()
    check_web(args.web, args.api)
    check_offline_contract(args.api)
    check_pwa_build()
    check_ai_ro_role()

    failed = [r for r in RESULTS if r["status"] == "FAIL"]
    if args.json:
        print(json.dumps({"ok": not failed, "results": RESULTS}, indent=2))
    else:
        icon = {"PASS": "✅", "WARN": "⚠️ ", "FAIL": "❌"}
        for r in RESULTS:
            print(f" {icon[r['status']]} {r['check']:<26} {r['detail']}")
        print(f"\n{'❌ ' + str(len(failed)) + ' check(s) FAILED' if failed else '✅ sync chain healthy'}"
              f" ({len(RESULTS)} checks)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
