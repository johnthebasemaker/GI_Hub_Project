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
# (api-training reuses the api build context; `up` builds it from cache.)
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

# ── 4b. Practice sandbox (rule 17) — only when configured ───────────────────
# Rebuilt from scratch on EVERY deploy: cutover_migrate builds the seed at
# alembic head, so Practice never runs migrations-behind, and a deploy is the
# natural moment to refresh the synthetic dates. Runs inside the api image
# (it carries tools/ and legacy/), connecting as the cluster admin for the
# wall and the seed's CREATE DATABASE, and as gi_training for everything else.
envval() { grep -E "^$1=" "${HERE}/.env" | tail -1 | cut -d= -f2-; }
PROFILES=""
if [ -n "$(envval PRACTICE_JWT_SECRET)" ]; then
    if [ "$(envval PRACTICE_JWT_SECRET)" = "$(envval JWT_SECRET)" ]; then
        echo "ABORT: PRACTICE_JWT_SECRET equals JWT_SECRET — Practice and Live must never share a signing key"
        exit 1
    fi
    [ -n "$(envval PRACTICE_DB_PASSWORD)" ] || { echo "ABORT: PRACTICE_DB_PASSWORD is empty"; exit 1; }
    echo "==> [4b] Practice sandbox: wall + rebuild (tools/practice_db.py)"
    PG_ADMIN="postgresql://$(envval POSTGRES_USER):$(envval POSTGRES_PASSWORD)@db:5432/postgres"
    PG_PRACTICE="postgresql://gi_training:$(envval PRACTICE_DB_PASSWORD)@db:5432/gihub_training"
    for step in wall build verify; do
        $NEW run --rm --no-deps \
            -e PRACTICE_ADMIN_URL="$PG_ADMIN" -e PRACTICE_DATABASE_URL="$PG_PRACTICE" \
            -e PRACTICE_DB_PASSWORD="$(envval PRACTICE_DB_PASSWORD)" \
            -e PRACTICE_PASSWORD="$(envval PRACTICE_PASSWORD)" \
            -e PRACTICE_ADMIN_PASSWORD="$(envval PRACTICE_ADMIN_PASSWORD)" \
            -e LIVE_DB="$(envval POSTGRES_DB)" \
            api python tools/practice_db.py "$step"
    done
    PROFILES="--profile practice"
else
    echo "    (Practice not configured — PRACTICE_JWT_SECRET is blank; skipping)"
fi

echo "==> [5/7] Starting v2 (no port handover needed under the tunnel)"
# Under the Cloudflare Tunnel the v2 stack publishes NO host ports, so it no
# longer contends with the v1 root nginx on :80/:443 — both can run at once and
# v1 is left alone here. Which stack the public hostname reaches is decided by
# the TUNNEL ROUTE in the Zero Trust dashboard, not by who holds the port.
$NEW $PROFILES up -d --remove-orphans

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
