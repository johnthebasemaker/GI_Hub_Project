#!/usr/bin/env bash
# ----------------------------------------------------------------------
# host_setup/scripts/run_locate_anything.sh — Phase 8B
#
# Wrapper exec'd by the launchd plist (com.gi.locate-anything.plist).
# Mirrors the run_streamlit.sh pattern so the venv binds and TZ defaults
# stay consistent across services.
#
# Binds to 127.0.0.1:8503 — NEVER 0.0.0.0. The sidecar is a localhost-
# only inference service; the Cloudflare Tunnel does NOT expose this port.
#
# Single worker — concurrent requests serialise on the single in-process
# model instance (Phase 8A architectural contract).
# ----------------------------------------------------------------------

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${PROJECT_DIR}"

export PATH="${PROJECT_DIR}/.venv/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export TZ="${TZ:-Asia/Riyadh}"
# Sidecar reads weights from this dir; mirror model_loader.WEIGHTS_DIR.
export GI_LOCATE_WEIGHTS_DIR="${GI_LOCATE_WEIGHTS_DIR:-${HOME}/Library/Caches/gi_locate}"

exec uvicorn ai.locate_anything.server:app \
    --host 127.0.0.1 \
    --port 8503 \
    --workers 1 \
    --log-level info
