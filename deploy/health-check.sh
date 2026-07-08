#!/usr/bin/env bash
# ============================================================================
# deploy/health-check.sh — post-deploy health gate for the v2 stack.
# Exits 0 only if ALL of:
#   1. FastAPI /health returns 200 within 2s (checked inside the api container).
#   2. The React SPA responds on the host (web publishes :80) with < 400.
#   3. Alembic is at head (no un-applied migrations).
# Any failure exits non-zero → deploy-v2.sh triggers rollback.sh.
#
# Run from the repo root (or anywhere — it locates the compose file itself).
# Not intended to be run locally against a dev box; it targets the server stack.
# ============================================================================
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
COMPOSE="docker compose -f ${HERE}/docker-compose.prod.yml"

fail() { echo "[health] FAIL — $1" >&2; exit 1; }

# 1. FastAPI /health < 2s, from inside the api container (no host port on api).
echo "[health] api /health (<2s)…"
$COMPOSE exec -T api python -c "
import sys, urllib.request
try:
    r = urllib.request.urlopen('http://localhost:8000/health', timeout=2)
    sys.exit(0 if r.status == 200 else 1)
except Exception as e:
    print(e, file=sys.stderr); sys.exit(1)
" || fail "api /health did not return 200 within 2s"

# 2. SPA reachable on the host. Accept any < 400 (200 or a TLS/redirect 3xx).
echo "[health] web / (host :80)…"
code="$(curl -fsS -o /dev/null -w '%{http_code}' -m 5 http://localhost/ 2>/dev/null || echo 000)"
[ "$code" -lt 400 ] 2>/dev/null || fail "web / returned HTTP ${code}"

# 3. Alembic at head — current revision must be in the set of heads.
echo "[health] alembic at head…"
cur="$($COMPOSE exec -T api sh -c 'cd /app/backend && alembic current 2>/dev/null' \
        | grep -oE '^[0-9a-f]{12}' | head -n1)"
head="$($COMPOSE exec -T api sh -c 'cd /app/backend && alembic heads 2>/dev/null' \
        | grep -oE '^[0-9a-f]{12}' | head -n1)"
[ -n "$cur" ] && [ "$cur" = "$head" ] || fail "alembic not at head (current='${cur}' head='${head}')"

echo "[health] OK — api healthy, web serving, schema at head."
exit 0
