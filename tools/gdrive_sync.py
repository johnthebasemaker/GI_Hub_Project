#!/usr/bin/env python3
"""
tools/gdrive_sync.py — fetch the newest workbooks from Google Drive and run the
Excel sync (Phase 21c, rulings Q21-7..11).

    .venv/bin/python tools/gdrive_sync.py --auth        # once: Google sign-in, read-only
    .venv/bin/python tools/gdrive_sync.py --list        # what would be fetched (downloads nothing)
    .venv/bin/python tools/gdrive_sync.py               # fetch → SME commit → ERP dry run
    .venv/bin/python tools/gdrive_sync.py --fetch-only  # fetch, no sync
    .venv/bin/python tools/gdrive_sync.py --commit-erp  # the operator's Commit: ERP ledger

Setup, step by step: docs/GDRIVE_SETUP.md. The token is `drive.readonly` and
lives in git-ignored deploy/gdrive_token.json. The same code runs from the
Admin Console (Drive sync card) and the daily 07:30 run.

⚠️ LIVE ONLY. Practice never syncs from Drive (rule 17): refuses under
GI_INSTANCE=training.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import http.server
import json
import os
import secrets
import subprocess
import sys
import threading
import urllib.parse
import webbrowser
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from backend.api.services import drive_sync as DS  # noqa: E402

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"


def _auth() -> int:
    """Loopback sign-in with PKCE. Stores ONLY the refresh token (and the
    scope), chmod 600, in deploy/gdrive_token.json."""
    import httpx
    cli = DS.load_client()
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    got: dict = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            got.update({k: v[0] for k, v in q.items()})
            ok = "code" in got and got.get("state") == state
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(("<h3>GI-Hub has read-only access. You can close this tab.</h3>" if ok
                              else "<h3>Sign-in did not complete — go back to the terminal.</h3>")
                             .encode())

        def log_message(self, *a):  # silence
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    redirect = f"http://127.0.0.1:{srv.server_port}/"
    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": cli["client_id"], "redirect_uri": redirect, "response_type": "code",
        "scope": DS.SCOPE, "code_challenge": challenge, "code_challenge_method": "S256",
        "access_type": "offline", "prompt": "consent", "state": state})
    print("▶ opening Google sign-in in your browser (read-only Drive access)…")
    print(f"  if it does not open, paste this into the browser:\n  {url}\n")
    t = threading.Thread(target=srv.handle_request, daemon=True)
    t.start()
    webbrowser.open(url)
    t.join(timeout=600)
    srv.server_close()
    if got.get("state") != state or "code" not in got:
        print(f"❌ sign-in did not complete ({got.get('error', 'no answer within 10 minutes')})")
        return 1
    r = httpx.post(DS.TOKEN_URL, data={
        "code": got["code"], "client_id": cli["client_id"], "client_secret": cli["client_secret"],
        "redirect_uri": redirect, "grant_type": "authorization_code", "code_verifier": verifier},
        timeout=60)
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code != 200 or "refresh_token" not in body:
        print(f"❌ Google did not return a token ({r.status_code}: {body.get('error', '')})")
        return 1
    if DS.SCOPE not in body.get("scope", ""):
        print(f"❌ the token is not the read-only Drive scope ({body.get('scope')}) — refusing to store it")
        return 1
    DS.TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    DS.TOKEN_PATH.write_text(json.dumps({"refresh_token": body["refresh_token"],
                                         "scope": body.get("scope")}, indent=1))
    os.chmod(DS.TOKEN_PATH, 0o600)
    print(f"✅ token saved to {DS._rel(DS.TOKEN_PATH)} (read-only, git-ignored)")
    return 0


def _list() -> int:
    c = DS.DriveClient()
    files = c.list_folder()
    print(f"▶ Drive folder {DS.FOLDER_ID}: {len(files)} item(s)\n")
    for t in DS.TARGETS:
        f, notes = DS.pick(t, files)
        side = "ERP — dry run, you commit" if t.side == "erp" else "SME — commits itself"
        print(f"  {t.dest:<42} ← {f['name'] if f else '— nothing matches —'}"
              f"{'  (' + f.get('modifiedTime', '')[:16] + ')' if f else ''}   [{side}]")
        for n in notes:
            print(f"      note: {n}")
    cls = DS.classify(files)
    print(f"\n  not used: {len(cls['unused'])} file(s) · {len(cls['folders'])} folder(s) "
          f"· {len(cls['lock_files'])} Excel lock file(s)")
    for n in cls["unused"]:
        print(f"      – {n}")
    return 0


def _run_sync(argv: list[str]) -> tuple[int, str]:
    env = dict(os.environ)
    p = subprocess.run([sys.executable, *argv], cwd=str(_ROOT), env=env, text=True,
                       capture_output=True)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def main() -> int:
    ap = argparse.ArgumentParser(description="Google Drive → workbooks → Excel sync (Phase 21c)")
    ap.add_argument("--auth", action="store_true", help="one-time Google sign-in (read-only)")
    ap.add_argument("--list", action="store_true", help="show what would be fetched")
    ap.add_argument("--fetch-only", action="store_true", help="fetch, do not run the sync")
    ap.add_argument("--commit-erp", action="store_true",
                    help="COMMIT the ERP ledger from the fetched workbook (the operator's click)")
    ap.add_argument("--site", default="CNCEC")
    a = ap.parse_args()
    if os.environ.get("GI_INSTANCE", "").lower() == "training":
        print("❌ Practice never syncs from Drive (rule 17).")
        return 2
    try:
        if a.auth:
            return _auth()
        if a.list:
            return _list()
        if a.commit_erp:
            rc, out = _run_sync(DS.ERP_COMMIT)
            print(out)
            return rc
        rep = DS.fetch(DS.DriveClient())
    except DS.DriveError as e:
        print(f"❌ {e}")
        return 1
    print(f"▶ fetched {len(rep['changed'])} changed · {len(rep['unchanged'])} unchanged · "
          f"{len(rep['missing'])} missing · {len(rep['refused'])} refused")
    for c in rep["changed"]:
        print(f"  ✅ {c['dest']} ← {c['source']}{' (converted from .xlsm)' if c['converted'] else ''}")
    for r in rep["refused"]:
        print(f"  ❌ {r['dest']} ← {r['source']}: {r['reason']} — the previous file was kept")
    for n in rep["notes"]:
        print(f"  note: {n}")
    if a.fetch_only:
        return 1 if rep["refused"] else 0
    rc_all = 1 if rep["refused"] else 0
    for label, argv in DS.sync_commands(rep, a.site).items():
        print(f"\n▶ {label}: {' '.join(argv)}")
        rc, out = _run_sync(argv)
        print(out[-6000:])
        rc_all = rc_all or rc
    if not rep["changed"]:
        print("\nnothing new in Drive — no sync run")
    return rc_all


if __name__ == "__main__":
    sys.exit(main())
