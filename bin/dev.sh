#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# bin/dev.sh — one command to raise or level the whole GI Hub dev stack.
#
#   ./bin/dev.sh localhost   # Postgres + API + Vite on plain http://localhost
#   ./bin/dev.sh tunnel      # …plus the cloudflared connector → local.giinventory.com
#   ./bin/dev.sh gi          # …serving the gi.giinventory.com mirror (NO connector)
#   ./bin/dev.sh stop        # kill API + Vite + OUR connector, guaranteed
#   ./bin/dev.sh status      # what is up, on which ports, since when
#   ./bin/dev.sh logs [api|practice|web|tunnel]
#
# WHY A SCRIPT AND NOT `concurrently`: the four pieces are not four npm
# processes. Postgres is a brew LaunchAgent that must be adopted rather than
# spawned, uvicorn's --reload forks a child that a naive kill orphans, and the
# cloudflared connector must never be confused with the ROOT daemon that serves
# gi.giinventory.com. Those three rules are process management, not task
# running, so this stays a shell script with real PID/process-group handling.
#
# Each child is launched under job control (`set -m`), which puts it in its own
# process group — so `stop` can signal the GROUP and take uvicorn's reloader
# child and Vite's node child down with their parent. A pattern sweep scoped to
# THIS repo and THIS user then catches anything orphaned by an earlier crash.
#
# ⚠️ What this script deliberately does NOT touch (both belong to bin/power.sh,
#    which sleeps and wakes the always-on services for battery life):
#   • Postgres — a durable, autostarting brew service shared with the legacy
#     app and every test suite. `stop` leaves it running; use `--db` to include.
#   • The ROOT cloudflared LaunchDaemon (`com.cloudflare.cloudflared`), which is
#     the single always-on connector. Killing it is how Error 1033 comes back;
#     every sweep here is scoped to `-u $(id -u)` so root's daemon is untouchable.
# ---------------------------------------------------------------------------
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="$ROOT/.dev"          # pidfiles + logs; gitignored
# Overridable, and defaulted defensively: `set -u` turns a missing
# assignment into a hard abort deep inside a function, which reads as a
# bug in the caller rather than a missing constant.
PG_FORMULA="${PG_FORMULA:-postgresql@16}"
PG_HOST="127.0.0.1"
PG_PORT="5433"
API_PORT="8000"
PRACTICE_PORT="8001"      # rule 17: the Practice API, a second process
WEB_PORT="5173"
TUNNEL_NAME="gi-hub"
TUNNEL_CONFIG="$ROOT/deploy/cloudflared/config.yml"
TUNNEL_HOSTNAME="local.giinventory.com"
GI_HOSTNAME="gi.giinventory.com"

bold=$'\033[1m'; dim=$'\033[2m'; red=$'\033[31m'; grn=$'\033[32m'
ylw=$'\033[33m'; cyn=$'\033[36m'; off=$'\033[0m'
say()  { printf '%s\n' "$*"; }
info() { printf '%s▸%s %s\n' "$cyn" "$off" "$*"; }
ok()   { printf '%s✓%s %s\n' "$grn" "$off" "$*"; }
warn() { printf '%s!%s %s\n' "$ylw" "$off" "$*"; }
die()  { printf '%s✗%s %s\n' "$red" "$off" "$*" >&2; exit 1; }

# --- process helpers -------------------------------------------------------

pidfile() { printf '%s/%s.pid' "$RUN_DIR" "$1"; }
logfile() { printf '%s/%s.log' "$RUN_DIR" "$1"; }

# PID from a pidfile, but only if that process is still alive.
live_pid() {
  local f; f="$(pidfile "$1")"
  [ -f "$f" ] || return 1
  local p; p="$(cat "$f" 2>/dev/null || true)"
  [ -n "$p" ] || return 1
  kill -0 "$p" 2>/dev/null || return 1
  printf '%s' "$p"
}

port_pid() { lsof -nP -tiTCP:"$1" -sTCP:LISTEN 2>/dev/null | head -1; }
port_busy() { [ -n "$(port_pid "$1")" ]; }

# Launch a component in its OWN process group so stop can signal the group.
# `set -m` (job control) is what makes each background job a group leader; on
# macOS bash 3.2 that is the portable stand-in for setsid.
start_bg() {
  local name="$1"; shift
  mkdir -p "$RUN_DIR"
  set -m
  ( exec "$@" ) >>"$(logfile "$name")" 2>&1 &
  local pid=$!
  set +m
  printf '%s' "$pid" > "$(pidfile "$name")"
  printf '%s' "$pid"
}

# TERM the process group, then KILL what survives the grace period.
stop_group() {
  local pid="$1"
  kill -TERM -"$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
  local i=0
  while [ $i -lt 30 ] && kill -0 "$pid" 2>/dev/null; do sleep 0.1; i=$((i + 1)); done
  if kill -0 "$pid" 2>/dev/null; then
    kill -KILL -"$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
  fi
}

# Belt-and-braces sweep for processes this repo owns that no pidfile knows
# about (a crashed shell, a run started by hand). Every pattern is anchored on
# THIS repo's absolute path or its unique module path, and every call is scoped
# to the current uid — the root cloudflared daemon can never match.
sweep() {
  pkill -u "$(id -u)" -f "backend.api.main:app" 2>/dev/null || true
  pkill -u "$(id -u)" -f "$ROOT/frontend" 2>/dev/null || true
  pkill -u "$(id -u)" -f "cloudflared tunnel --config $TUNNEL_CONFIG" 2>/dev/null || true
}

wait_for() { # wait_for <seconds> <label> <test-command...>
  local secs="$1" label="$2"; shift 2
  local tries=$((secs * 4)) i=0
  while [ $i -lt $tries ]; do
    if "$@" >/dev/null 2>&1; then return 0; fi
    sleep 0.25; i=$((i + 1))
  done
  warn "$label did not come up within ${secs}s — check ${dim}$RUN_DIR${off}"
  return 1
}

http_ok() { curl -fsS -o /dev/null --max-time 3 "$1"; }

# --- components ------------------------------------------------------------

ensure_postgres() {
  if pg_isready -q -h "$PG_HOST" -p "$PG_PORT" 2>/dev/null; then
    ok "Postgres already up on :$PG_PORT"
    return 0
  fi
  info "Postgres not answering on :$PG_PORT — starting ${PG_FORMULA}…"
  brew services start "$PG_FORMULA" >/dev/null 2>&1 \
    || die "could not start $PG_FORMULA (try: brew services start $PG_FORMULA)"
  wait_for 25 "Postgres" pg_isready -q -h "$PG_HOST" -p "$PG_PORT" \
    || die "Postgres never became ready on :$PG_PORT"
  ok "Postgres started on :$PG_PORT"
}

start_api() {
  local pid; pid="$(start_bg api "$ROOT/run_api.sh")"
  wait_for 60 "API" http_ok "http://127.0.0.1:$API_PORT/health" || true
  ok "API      pid $pid   http://127.0.0.1:$API_PORT/docs"
  # Phase 15a: Live is never migrated by a script — only told about.
  if curl -fsS --max-time 3 "http://127.0.0.1:$API_PORT/health" 2>/dev/null | grep -q '"schema":"behind"'; then
    warn "Live DB is BEHIND the code's migrations — reads of new columns will 500.
   ${bold}cd backend && ../.venv/bin/alembic upgrade head${off}   (take a backup first)"
  fi
}

# Rule 17: the Practice API. Started only when its sandbox exists — a missing
# sandbox is a setup step the developer has not done yet, not a reason to
# refuse the Live stack. Connecting AS gi_training is the check, so a wall that
# was never built shows up here too.
start_practice() {
  if ! psql -h "$PG_HOST" -p "$PG_PORT" -U gi_training -d gihub_training -tAc 'SELECT 1' >/dev/null 2>&1; then
    warn "Practice  not started — no sandbox yet. Build it once:
   ${bold}.venv/bin/python tools/practice_db.py wall && .venv/bin/python tools/practice_db.py build${off}"
    return 0
  fi
  # Phase 15a: bring seed + sandbox to the code's migration head first (backup
  # taken, trainee data kept; a no-op when already there). The Practice API
  # refuses to boot behind head, so skipping this only moves the failure.
  if ! "$ROOT/.venv/bin/python" "$ROOT/tools/practice_db.py" migrate; then
    warn "Practice  not started — its databases could not be migrated (see above)."
    return 0
  fi
  local pid; pid="$(start_bg practice "$ROOT/bin/practice_api.sh")"
  wait_for 60 "Practice API" http_ok "http://127.0.0.1:$PRACTICE_PORT/instance" || true
  ok "Practice pid $pid   http://127.0.0.1:$PRACTICE_PORT/instance  (Live | Practice toggle on the login page)"
}

start_web() { # start_web <npm-script>
  local script="$1" pid
  pid="$(start_bg web npm --prefix "$ROOT/frontend" run "$script")"
  wait_for 90 "Vite" port_busy "$WEB_PORT" || true
  ok "Vite     pid $pid   npm run $script → :$WEB_PORT"
}

# One hostname, one tunnel, one connector: a second user-level connector on a
# different tunnel ID is exactly what produces Error 1033. Checked in preflight,
# BEFORE anything is spawned — refusing after the API and Vite are up would
# leave a half-raised stack behind.
assert_no_foreign_connector() {
  local other
  other="$(pgrep -u "$(id -u)" -f 'cloudflared tunnel' 2>/dev/null | head -1 || true)"
  [ -n "$other" ] || return 0
  die "another cloudflared connector is already running as you (pid $other):
   $(ps -o command= -p "$other" 2>/dev/null | cut -c1-90)
   Kill it first — two connectors on different tunnels is the Error 1033 trap
   (deploy/cloudflared/README.md). Nothing was started."
}

start_tunnel() {
  [ -f "$TUNNEL_CONFIG" ] || die "missing $TUNNEL_CONFIG"
  local pid
  pid="$(start_bg tunnel cloudflared tunnel --config "$TUNNEL_CONFIG" run "$TUNNEL_NAME")"
  wait_for 30 "Tunnel" grep -q "Registered tunnel connection" "$(logfile tunnel)" || true
  ok "Tunnel   pid $pid   $TUNNEL_NAME ← $TUNNEL_CONFIG"

  # Registered connectors prove the TUNNEL is healthy, not that the HOSTNAME
  # routes to it — so probe the real URL. Cloudflare's edge needs a few seconds
  # after registration before it will route to a brand-new connector, and a
  # single early request comes back 530; retry rather than cry 1033 wrongly.
  local code="000" i=0
  while [ $i -lt 8 ]; do
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "https://$TUNNEL_HOSTNAME/" || echo 000)"
    case "$code" in 2*|3*) break ;; esac
    sleep 2.5; i=$((i + 1))
  done
  case "$code" in
    2*|3*) ok "https://$TUNNEL_HOSTNAME → HTTP $code" ;;
    *) warn "https://$TUNNEL_HOSTNAME → HTTP $code after ~20s.
   530/1033 here means the hostname is not routed to '$TUNNEL_NAME'; the DNS
   record moves with (your call — it rewrites a CNAME):
     ${bold}cloudflared tunnel route dns $TUNNEL_NAME $TUNNEL_HOSTNAME${off}
   Background: deploy/cloudflared/README.md → THE WORKFLOW." ;;
  esac
}

# --- preflight -------------------------------------------------------------

# A port held by our own previous run is ours to reclaim; a port held by
# something else is the developer's to resolve, so say exactly what holds it.
free_port_or_die() {
  local port="$1" label="$2" holder
  port_busy "$port" || return 0
  holder="$(port_pid "$port")"
  local ours=""
  for c in api practice web; do
    local p; p="$(live_pid "$c" || true)"
    [ -n "$p" ] && [ "$p" = "$holder" ] && ours="yes"
  done
  if [ -n "$ours" ]; then
    info "reclaiming :$port from our own previous run"
    cmd_stop quiet
    return 0
  fi
  die "$label port :$port is held by pid $holder —
   $(ps -o command= -p "$holder" 2>/dev/null | cut -c1-90)
   Run '${bold}./bin/dev.sh stop${off}' if that is a stale stack, or stop it yourself."
}

preflight() {
  [ -x "$ROOT/.venv/bin/uvicorn" ] || die "no .venv — expected $ROOT/.venv/bin/uvicorn"
  [ -d "$ROOT/frontend/node_modules" ] || die "frontend deps missing — run: npm ci --prefix frontend"
  mkdir -p "$RUN_DIR"
  # A previous stack still holding its pidfiles is simply replaced.
  if live_pid api >/dev/null 2>&1 || live_pid practice >/dev/null 2>&1 \
     || live_pid web >/dev/null 2>&1 || live_pid tunnel >/dev/null 2>&1; then
    info "an existing stack is running — stopping it first"
    cmd_stop quiet
  fi
  free_port_or_die "$API_PORT" API
  free_port_or_die "$PRACTICE_PORT" Practice
  free_port_or_die "$WEB_PORT" Vite
}

# --- commands --------------------------------------------------------------

cmd_start() { # cmd_start <env>
  local env="$1"
  preflight
  # NOT `[ … ] && assert_no_foreign_connector` — under `set -e` an AND-list
  # that ends false is itself a non-zero command and aborts the script.
  if [ "$env" = "tunnel" ]; then assert_no_foreign_connector; fi
  ensure_postgres
  start_api
  start_practice
  case "$env" in
    localhost) start_web dev ;;
    tunnel)    start_web dev:local; start_tunnel ;;
    gi)        start_web dev:gi ;;
  esac
  echo "$env" > "$RUN_DIR/env"

  say ""
  case "$env" in
    localhost)
      say "  ${bold}Open http://localhost:$WEB_PORT${off}  ${dim}(HMR live)${off}" ;;
    tunnel)
      say "  ${bold}Open https://$TUNNEL_HOSTNAME${off}  ${dim}(HMR over the tunnel; localhost has no HMR in this mode)${off}" ;;
    gi)
      say "  ${bold}Open https://$GI_HOSTNAME${off}"
      say "  ${dim}Served by the ROOT cloudflared LaunchDaemon — no connector was started here.${off}"
      pgrep -f 'cloudflared tunnel run --token' >/dev/null 2>&1 \
        || warn "that root daemon is NOT running — it may simply be asleep:
   ${bold}./bin/power.sh wake${off}   (or: sudo launchctl kickstart -k system/com.cloudflare.cloudflared)"
      warn "$GI_HOSTNAME becomes the PRODUCTION hostname once Hetzner is live —
   from that day, serving it from this Mac is a hijack, not a mirror." ;;
  esac
  say "  ${dim}Logs: $RUN_DIR/{api,web,tunnel}.log   ·   Stop: ./bin/dev.sh stop${off}"
}

cmd_stop() {
  local quiet="${1:-}"
  local stopped=0 c p
  for c in tunnel web practice api; do
    if p="$(live_pid "$c")"; then
      stop_group "$p"
      [ -n "$quiet" ] || ok "stopped $c (pid $p)"
      stopped=$((stopped + 1))
    fi
    rm -f "$(pidfile "$c")"
  done
  sweep                      # orphans from a crashed shell or a by-hand run
  rm -f "$RUN_DIR/env"

  if [ "${2:-}" = "--db" ] || [ "$quiet" = "--db" ]; then
    brew services stop "$PG_FORMULA" >/dev/null 2>&1 && ok "stopped $PG_FORMULA" || true
  fi

  if [ -z "$quiet" ] || [ "$quiet" = "--db" ]; then
    sleep 0.3
    local leftover=""
    port_busy "$API_PORT" && leftover="$leftover :$API_PORT"
    port_busy "$PRACTICE_PORT" && leftover="$leftover :$PRACTICE_PORT"
    port_busy "$WEB_PORT" && leftover="$leftover :$WEB_PORT"
    if [ -n "$leftover" ]; then
      warn "still listening —$leftover (not ours; lsof -nP -iTCP$leftover -sTCP:LISTEN)"
    else
      [ "$stopped" -gt 0 ] && ok "clean slate — :$API_PORT and :$WEB_PORT are free" \
                           || ok "nothing was running — :$API_PORT and :$WEB_PORT are free"
    fi
    pgrep -u "$(id -u)" -f 'cloudflared tunnel' >/dev/null 2>&1 \
      && warn "a cloudflared connector of yours is still up (pgrep -fl 'cloudflared tunnel')" || true
  fi
}

cmd_status() {
  local env="—"; [ -f "$RUN_DIR/env" ] && env="$(cat "$RUN_DIR/env")"
  say "${bold}GI Hub dev stack${off}  ${dim}(mode: $env)${off}"
  if pg_isready -q -h "$PG_HOST" -p "$PG_PORT" 2>/dev/null; then
    ok "Postgres  :$PG_PORT   $PG_FORMULA"
  else
    warn "Postgres  :$PG_PORT   down"
  fi
  local c p label
  for c in api practice web tunnel; do
    case "$c" in
      api)    label="API       :$API_PORT" ;;
      practice) label="Practice  :$PRACTICE_PORT" ;;
      web)    label="Vite      :$WEB_PORT" ;;
      tunnel) label="Tunnel    $TUNNEL_NAME" ;;
    esac
    if p="$(live_pid "$c")"; then
      ok "$label   pid $p   ${dim}up $(ps -o etime= -p "$p" | tr -d ' ')${off}"
    else
      warn "$label   down"
    fi
  done
  local root_daemon
  root_daemon="$(pgrep -f 'cloudflared tunnel run --token' | head -1 || true)"
  [ -n "$root_daemon" ] \
    && ok  "Root cloudflared daemon (serves $GI_HOSTNAME)   pid $root_daemon" \
    || warn "Root cloudflared daemon is NOT running"
}

cmd_logs() {
  local which="${1:-}"
  if [ -n "$which" ]; then
    [ -f "$(logfile "$which")" ] || die "no log for '$which' (api|practice|web|tunnel)"
    tail -n 80 -f "$(logfile "$which")"
  else
    local files="" c
    for c in api practice web tunnel; do
      [ -f "$(logfile "$c")" ] && files="$files $(logfile "$c")"
    done
    [ -n "$files" ] || die "no logs yet — start the stack first"
    # shellcheck disable=SC2086
    tail -n 40 -f $files
  fi
}

usage() {
  cat <<EOF
${bold}GI Hub — unified dev stack${off}

  ${bold}./bin/dev.sh localhost${off}   Postgres + API + Practice API + Vite → http://localhost:$WEB_PORT
  ${bold}./bin/dev.sh tunnel${off}      …plus the cloudflared connector  → https://$TUNNEL_HOSTNAME
  ${bold}./bin/dev.sh gi${off}          …serving the legacy mirror       → https://$GI_HOSTNAME
                          (no connector — the root daemon already serves it)

  ${bold}./bin/dev.sh stop${off}        kill API + Vite + our connector  (add ${bold}--db${off} to stop Postgres too)
  ${bold}./bin/dev.sh status${off}      what is up, on which ports, for how long
  ${bold}./bin/dev.sh logs${off} [api|practice|web|tunnel]

The Practice API (rule 17) starts on :$PRACTICE_PORT whenever its sandbox exists;
build it once with ${bold}tools/practice_db.py wall${off} then ${bold}build${off}.

Only ONE mode runs at a time — all three want :$WEB_PORT, and Vite's strictPort
makes a second one fail loudly instead of drifting to :5174.
Postgres is a shared brew service: 'stop' leaves it running unless you pass --db.

For the ALWAYS-ON services (Postgres + the root cloudflared daemon), use the
power manager — ${bold}./bin/power.sh sleep${off} when you close the lid, ${bold}wake${off} when you
sit down. ${bold}./bin/power.sh status${off} also reports the stale legacy LaunchAgents
that respawn on a timer and are the biggest idle battery cost.
EOF
}

case "${1:-}" in
  localhost|local) cmd_start localhost ;;
  tunnel|local.gi) cmd_start tunnel ;;
  gi|legacy)       cmd_start gi ;;
  stop|down)       shift; cmd_stop "${1:-}" ;;
  status|ps)       cmd_status ;;
  logs|tail)       shift; cmd_logs "${1:-}" ;;
  ''|-h|--help|help) usage ;;
  *) usage; die "unknown command '${1}'" ;;
esac
