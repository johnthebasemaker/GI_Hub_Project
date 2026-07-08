#!/usr/bin/env bash
# ============================================================================
# deploy/deploy-v2.sh — server-side deploy of the v2 (React/FastAPI/Postgres)
# stack. Invoked over SSH by .github/workflows/deploy-v2.yml (manual trigger
# only). Runs on the Hetzner host, from the existing repo checkout.
#
# Flow:
#   pre-flight → git reset --hard origin/main → build (SHA-tagged images)
#   → db up + `alembic upgrade head` → PORT-HANDOVER (stop v1 nginx) → v2 up
#   → health-check.sh → on success: record SHA + Slack; on failure: rollback.sh
#     (which restores the v1 nginx port-handover) + Slack.
#
# The server already mirrors the repo for v1, and the v2 compose build context
# IS the repo root, so no rsync is needed — a git reset is the whole sync.
#
# Environment (forwarded by the workflow / present on the server):
#   SLACK_WEBHOOK_URL   optional; notifications no-op if unset.
# Requires: deploy/.env present (compose secrets), docker + docker compose v2.
# ============================================================================
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "${HERE}/.." && pwd)"
NEW="docker compose -f ${HERE}/docker-compose.prod.yml"
V1="docker compose -f ${ROOT}/docker-compose.yml"
PROJECT="gi-hub-newstack"
SHA_FILE="${HERE}/.deployed_sha"
SLACK_WEBHOOK_URL="${SLACK_WEBHOOK_URL:-}"

slack() {   # slack "<text>"
    [ -n "$SLACK_WEBHOOK_URL" ] || { echo "[deploy] (slack skipped) $1"; return 0; }
    curl -fsS -m 10 -X POST -H 'Content-Type: application/json' \
        --data "{\"text\": \"$1\"}" "$SLACK_WEBHOOK_URL" >/dev/null 2>&1 \
        || echo "[deploy] WARN — Slack notify failed"
}

cd "$ROOT"

echo "==> [1/7] Pre-flight checks"
[ -f "${HERE}/.env" ] || { echo "ABORT: ${HERE}/.env is missing (compose secrets)"; exit 1; }
command -v docker >/dev/null || { echo "ABORT: docker not found"; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "ABORT: docker compose v2 not found"; exit 1; }
# Fail early if the disk is nearly full (image builds need headroom).
avail_kb="$(df -Pk "$ROOT" | awk 'NR==2{print $4}')"
[ "${avail_kb:-0}" -gt 2097152 ] || { echo "ABORT: < 2 GB free on $(df -Ph "$ROOT" | awk 'NR==2{print $6}')"; exit 1; }

PREV_SHA="$(cat "$SHA_FILE" 2>/dev/null || echo "")"

echo "==> [2/7] Syncing working tree to origin/main"
git fetch --prune origin
git reset --hard origin/main    # server is a pure mirror; runtime state is in volumes
NEW_SHA="$(git rev-parse --short HEAD)"
echo "    prev=${PREV_SHA:-none}  new=${NEW_SHA}"

echo "==> [3/7] Building v2 images (SHA-tagged: ${NEW_SHA})"
$NEW build api web
# Tag the freshly built images with the git SHA so rollback has a concrete target.
for svc in api web; do
    docker tag "${PROJECT}-${svc}:latest" "${PROJECT}-${svc}:${NEW_SHA}" 2>/dev/null \
        || echo "[deploy] WARN — could not SHA-tag ${PROJECT}-${svc}"
done

echo "==> [4/7] Database up + migrations (alembic upgrade head)"
$NEW up -d db
# Wait for the db healthcheck before migrating.
for i in $(seq 1 30); do
    if $NEW exec -T db pg_isready -U "$(grep -E '^POSTGRES_USER=' "${HERE}/.env" | cut -d= -f2)" >/dev/null 2>&1; then
        break
    fi
    sleep 2
done
$NEW run --rm api sh -c 'cd /app/backend && alembic upgrade head'

echo "==> [5/7] PORT-HANDOVER — stopping v1 nginx (frees :80/:443), starting v2"
# The v1 root nginx and v2 web both bind :80/:443 — only one can serve. This is
# the deliberate cutover handover; rollback.sh reverses it on failure.
$V1 stop nginx || echo "[deploy] (v1 nginx not running — nothing to stop)"
$NEW up -d --remove-orphans

echo "==> [6/7] Health check"
if bash "${HERE}/health-check.sh"; then
    echo "==> [7/7] Healthy — recording ${NEW_SHA}, pruning old layers"
    echo "$NEW_SHA" > "$SHA_FILE"
    docker image prune -f >/dev/null 2>&1 || true
    slack ":white_check_mark: GI Hub v2 deployed — ${NEW_SHA} (prev ${PREV_SHA:-none}). Users are on React."
    echo "[deploy] SUCCESS"
else
    echo "==> [7/7] Health check FAILED — rolling back"
    slack ":rotating_light: GI Hub v2 deploy ${NEW_SHA} FAILED health check — rolling back to ${PREV_SHA:-v1}."
    bash "${HERE}/rollback.sh" "$PREV_SHA" || true
    slack ":leftwards_arrow_with_hook: Rollback complete — users restored to v1. v2 needs manual repair."
    echo "[deploy] ROLLED BACK"
    exit 1
fi
