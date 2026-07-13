"""
pwa/run.py — launcher for the offline scan-and-stage PWA service
==================================================================
Run me from the project root:

    python pwa/run.py            # binds 0.0.0.0:8001 by default
    python pwa/run.py --reload   # dev-mode hot reload
    PWA_PORT=8080 python pwa/run.py

Floor users then point their phone browser at:

    http://<your-mac-ip>:8001/

and tap "Add to Home Screen" once to install the PWA. After that, the app
opens like a native app and works fully offline; scans queue locally and
sync to `pending_issues` automatically when the phone reaches the LAN.

This launcher is a separate process from `streamlit run main.py` — both
share the same SQLite file (WAL mode keeps writers happy).
"""

from __future__ import annotations

import argparse
import os
import sys

import uvicorn


def main() -> int:
    parser = argparse.ArgumentParser(description="GI Floor PWA launcher")
    parser.add_argument("--host", default=os.environ.get("PWA_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PWA_PORT", "8001")))
    parser.add_argument("--reload", action="store_true",
                        help="Auto-reload on code changes (dev only)")
    args = parser.parse_args()

    print(f"⚡ GI Floor PWA → http://{args.host}:{args.port}/")
    print(f"   API docs    → http://{args.host}:{args.port}/api/docs")
    print("   Streamlit and this service share the same gi_database.db (WAL).")

    uvicorn.run(
        "pwa.api:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
