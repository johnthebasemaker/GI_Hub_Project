#!/usr/bin/env bash
# ============================================================================
# deploy/rollback.sh — revert a failed v2 deploy. Called by deploy-v2.sh when
# the health check fails (or manually with a target SHA).
#
#   rollback.sh [PREV_SHA]
#
# It does TWO things, in this order, so users are never left on a broken v2:
#   1. Revert the port-handover: stop v2 `web` and bring the v1 root nginx back
#      up on :80/:443 (the last known-good app; its SQLite is untouched).
#   2. If PREV_SHA is given and images for it exist, retag them to :latest and
#      bring the v2 stack back to that revision internally (so a fix-and-retry
#      starts from the last-good build). NEVER downgrades the DB schema —
#      Postgres migrations are forward-only here; schema rollback stays manual.
#
# Idempotent and defensive: every step tolerates "already in that state".
# ============================================================================
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "${HERE}/.." && pwd)"
NEW="docker compose -f ${HERE}/docker-compose.prod.yml"          # v2 stack
V1="docker compose -f ${ROOT}/docker-compose.yml"               # v1 root stack
PROJECT="gi-hub-newstack"                                        # v2 compose project name
PREV_SHA="${1:-}"

echo "[rollback] === reverting v2 deploy (prev_sha='${PREV_SHA:-none}') ==="

# 1. Port-handover revert — free :80/:443 for v1, restore known-good serving.
echo "[rollback] stopping v2 web (releasing :80/:443)…"
$NEW stop web || true
echo "[rollback] restarting v1 nginx (reclaiming :80/:443)…"
$V1 up -d nginx || echo "[rollback] WARN — could not start v1 nginx; check the box manually"

# 2. Optional image revert to the last known-good SHA.
if [ -n "$PREV_SHA" ]; then
    for svc in api web; do
        img="${PROJECT}-${svc}"
        if docker image inspect "${img}:${PREV_SHA}" >/dev/null 2>&1; then
            echo "[rollback] retagging ${img}:${PREV_SHA} → :latest"
            docker tag "${img}:${PREV_SHA}" "${img}:latest"
        else
            echo "[rollback] no ${img}:${PREV_SHA} image on disk — skipping retag"
        fi
    done
    # Bring the v2 stack (minus web, still stopped) back to prev images.
    # DB is left as-is on purpose: no schema downgrade.
    echo "[rollback] restoring v2 services (db/api/ollama/backup) at prev images…"
    $NEW up -d db api ollama backup certbot || true
else
    echo "[rollback] no PREV_SHA — left v2 web stopped; v1 is serving."
fi

echo "[rollback] done. Users are on v1; repair v2 and re-run the deploy workflow."
