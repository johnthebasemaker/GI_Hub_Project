#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# bin/practice_api.sh — the PRACTICE API process (rule 17), dev/bare-metal.
#
# Started by `bin/dev.sh` next to the Live API — never by hand in a shell that
# outlives the turn (machine rule). Same code as Live; a different process
# with a different environment, which is the whole design:
#
#   GI_INSTANCE=training   → config.assert_instance_safe() refuses to boot
#                            unless DATABASE_URL names a *_training database
#   DATABASE_URL           → gihub_training as the gi_training role, which has
#                            NO CONNECT on Live's `gihub` (tools/practice_db.py wall)
#   JWT_SECRET             → PRACTICE_JWT_SECRET, or unset so the Practice dev
#                            fallback key applies — never Live's key
#   GI_DOTENV=0 + unset    → no WhatsApp/SMTP/cloud-vision credential can reach
#                            it (and boot refuses if one does)
#   GI_REPORTS_ARCHIVE_DIR → its own directory; Live's archive is not shared
#
# Build the sandbox first:  .venv/bin/python tools/practice_db.py wall && \
#                           .venv/bin/python tools/practice_db.py build
# ---------------------------------------------------------------------------
set -euo pipefail
cd "$(dirname "$0")/.."

unset WHATSAPP_TOKEN WHATSAPP_PHONE_NUMBER_ID SMTP_HOST SMTP_SERVER SMTP_USER \
      SMTP_PASS GI_AI_VISION_API_KEY GI_AI_RO_URL GI_BACKUPS_DIR 2>/dev/null || true
export GI_INSTANCE=training
export GI_DOTENV=0
export GI_OUTBOUND=off
export DATABASE_URL="${PRACTICE_DATABASE_URL:-postgresql+asyncpg://gi_training@127.0.0.1:5433/gihub_training}"
export JWT_SECRET="${PRACTICE_JWT_SECRET:-}"
export GI_REPORTS_ARCHIVE_DIR="${PRACTICE_REPORTS_DIR:-.dev/practice_reports_archive}"
export GI_AI_CONCURRENCY="${PRACTICE_AI_CONCURRENCY:-1}"
PORT="${PRACTICE_PORT:-8001}"

echo "GI Hub PRACTICE API (rule 17)"
echo "  DB    : ${DATABASE_URL}"
echo "  Health: http://localhost:${PORT}/health   Identity: http://localhost:${PORT}/instance"
echo

# ONE worker: the reset's DROP … WITH (FORCE) can terminate only sessions this
# role may signal, and one process is also all a class of trainees needs.
exec .venv/bin/uvicorn backend.api.main:app --reload --host 127.0.0.1 --port "${PORT}"
