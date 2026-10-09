#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# bin/backup_db.sh — timestamped local snapshots of the GI Hub database.
#
#   ./bin/backup_db.sh                take a snapshot now
#   ./bin/backup_db.sh --list         show what is in .backups/
#   ./bin/backup_db.sh --install      run it every day at 02:00 (LaunchAgent)
#   ./bin/backup_db.sh --uninstall    stop the daily job
#   ./bin/backup_db.sh --restore FILE print the exact restore command for FILE
#
# WHY THIS EXISTS: `com.gi.backup` has pointed at
# host_setup/scripts/backup_db.sh since it was written, and that path stopped
# existing in the 2026-07-13 cutover restructure. It has failed silently every
# night since — 25 consecutive `no such file or directory` entries in
# ~/Library/Logs/gi-backup.err. There are currently NO local backups.
# `--install` supersedes that agent (and unloads it) with com.gi.hub-backup.
#
# WHAT IT CAPTURES
#   • `gihub` on :5433 — a plain-SQL pg_dump, gzipped. Plain SQL rather than
#     -Fc so a snapshot stays greppable and restorable with nothing but psql.
#   • `media/catalog/` — the catalogue's pictures (Phase 23d), as a tar.gz;
#     restore with `tar -xzf media_<stamp>.tar.gz -C media/`.
#   • `gi_database.db` — the frozen legacy SQLite, copied READ-ONLY. It is the
#     legacy source of truth, it lives only on this disk, and it is never in
#     git. The copy is verified by comparing the source sha256 before and
#     after, so this script can prove it did not write to it (a standing
#     project rule). Skip it with --no-sqlite.
#
# Snapshots land in .backups/ (gitignored) and NOTHING in this script deletes
# anything outside that directory. Retention prunes to the newest $KEEP.
# ---------------------------------------------------------------------------
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${GI_BACKUP_DIR:-$ROOT/.backups}"
PG_BIN="/opt/homebrew/opt/postgresql@16/bin"
PG_HOST="${PGHOST:-127.0.0.1}"
PG_PORT="${PGPORT:-5433}"
PG_USER="${PGUSER:-postgres}"
PG_DB="${PGDATABASE:-gihub}"
SQLITE_SRC="$ROOT/gi_database.db"
KEEP="${GI_BACKUP_KEEP:-14}"          # snapshots to retain
LABEL="com.gi.hub-backup"
OLD_LABEL="com.gi.backup"             # the broken agent this replaces
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

bold=$'\033[1m'; dim=$'\033[2m'; red=$'\033[31m'; grn=$'\033[32m'
ylw=$'\033[33m'; cyn=$'\033[36m'; off=$'\033[0m'
info() { printf '%s▸%s %s\n' "$cyn" "$off" "$*"; }
ok()   { printf '%s✓%s %s\n' "$grn" "$off" "$*"; }
warn() { printf '%s!%s %s\n' "$ylw" "$off" "$*"; }
die()  { printf '%s✗%s %s\n' "$red" "$off" "$*" >&2; exit 1; }

human() { du -h "$1" 2>/dev/null | cut -f1; }

# --- the snapshot -----------------------------------------------------------

cmd_backup() {
  local with_sqlite="${1:-yes}"
  local pg_dump="$PG_BIN/pg_dump"
  [ -x "$pg_dump" ] || pg_dump="$(command -v pg_dump || true)"
  [ -n "$pg_dump" ] && [ -x "$pg_dump" ] || die "pg_dump not found (looked in $PG_BIN and PATH)"

  "$PG_BIN/pg_isready" -q -h "$PG_HOST" -p "$PG_PORT" 2>/dev/null \
    || pg_isready -q -h "$PG_HOST" -p "$PG_PORT" 2>/dev/null \
    || die "Postgres is not answering on $PG_HOST:$PG_PORT —
   it may be asleep: ${bold}./bin/power.sh wake${off}"

  mkdir -p "$OUT_DIR"
  local stamp; stamp="$(date +%Y-%m-%d_%H%M%S)"
  local sql="$OUT_DIR/${PG_DB}_${stamp}.sql"

  info "dumping $PG_DB from $PG_HOST:${PG_PORT}…"
  # Write to a .part first so an interrupted run can never leave a truncated
  # file sitting there looking like a good backup.
  if ! "$pg_dump" -h "$PG_HOST" -p "$PG_PORT" -U "$PG_USER" \
        --no-owner --no-privileges --clean --if-exists \
        "$PG_DB" > "$sql.part" 2>"$sql.err"; then
    warn "pg_dump failed:"
    sed 's/^/   /' "$sql.err" >&2
    rm -f "$sql.part" "$sql.err"
    die "no snapshot was written"
  fi
  rm -f "$sql.err"

  # A dump that restores nothing is worse than no dump, because it looks like
  # one. Require the schema markers pg_dump always emits for a real database.
  grep -q "PostgreSQL database dump complete" "$sql.part" \
    || { rm -f "$sql.part"; die "dump is incomplete (no end marker) — discarded"; }
  local tables; tables="$(grep -c '^CREATE TABLE' "$sql.part" || true)"
  [ "${tables:-0}" -gt 0 ] || { rm -f "$sql.part"; die "dump contains no tables — discarded"; }

  mv "$sql.part" "$sql"
  gzip -f "$sql"
  ok "$(basename "$sql.gz")   ${dim}$(human "$sql.gz") · $tables tables${off}"

  if [ "$with_sqlite" = "yes" ] && [ -f "$SQLITE_SRC" ]; then
    local before after dst="$OUT_DIR/gi_database_${stamp}.db"
    before="$(shasum -a 256 "$SQLITE_SRC" | cut -d' ' -f1)"
    cp "$SQLITE_SRC" "$dst"
    after="$(shasum -a 256 "$SQLITE_SRC" | cut -d' ' -f1)"
    if [ "$before" != "$after" ]; then
      rm -f "$dst"
      die "gi_database.db changed while being copied — snapshot discarded.
   Nothing should ever write to it; investigate before retrying."
    fi
    gzip -f "$dst"
    ok "$(basename "$dst.gz")   ${dim}$(human "$dst.gz") · source sha256 unchanged${off}"
  fi

  # Phase 23d (ruling Q23-7) — the catalogue's pictures live on disk, not in
  # the database: archive the folder beside the dump so a restore has both.
  local media_dir="${GI_MEDIA_DIR:-$ROOT/media/catalog}"
  if [ -d "$media_dir" ] && [ -n "$(ls -A "$media_dir" 2>/dev/null)" ]; then
    local mtar="$OUT_DIR/media_${stamp}.tar.gz"
    tar -czf "$mtar" -C "$(dirname "$media_dir")" "$(basename "$media_dir")"
    ok "$(basename "$mtar")   ${dim}$(human "$mtar") · pictures${off}"
  fi

  prune
  printf '\n%s  %s\n' "$dim" "$(ls -1 "$OUT_DIR"/*.gz 2>/dev/null | wc -l | tr -d ' ') file(s) in ${OUT_DIR#$ROOT/}, keeping the newest $KEEP of each kind${off}"
}

# Prune each KIND independently so a run that skipped SQLite cannot age the
# Postgres dumps out early (or the other way round). Only ever touches files
# this script's own naming scheme produced, inside OUT_DIR.
prune() {
  local kind f n
  for kind in "${PG_DB}_" "gi_database_" "media_"; do
    n=0
    while IFS= read -r f; do
      n=$((n + 1))
      [ "$n" -gt "$KEEP" ] && rm -f "$f" && printf '%s·%s pruned %s\n' "$dim" "$off" "$(basename "$f")"
    done < <(ls -1t "$OUT_DIR/${kind}"*.gz 2>/dev/null || true)
  done
  return 0
}

cmd_list() {
  [ -d "$OUT_DIR" ] || { warn "no snapshots yet — run ./bin/backup_db.sh"; return 0; }
  local n; n="$(ls -1 "$OUT_DIR"/*.gz 2>/dev/null | wc -l | tr -d ' ')"
  [ "$n" -gt 0 ] || { warn "no snapshots yet — run ./bin/backup_db.sh"; return 0; }
  printf '%s%s%s  (%s total)\n\n' "$bold" "$OUT_DIR" "$off" "$(du -sh "$OUT_DIR" | cut -f1)"
  ls -1t "$OUT_DIR"/*.gz | while IFS= read -r f; do
    printf '  %-46s %6s  %s\n' "$(basename "$f")" "$(human "$f")" \
      "$(stat -f '%Sm' -t '%Y-%m-%d %H:%M' "$f")"
  done
  printf '\n%s  Restore one with: ./bin/backup_db.sh --restore <file>%s\n' "$dim" "$off"
}

cmd_restore() {
  local f="${1:-}"
  [ -n "$f" ] || die "usage: ./bin/backup_db.sh --restore <file>"
  [ -f "$f" ] || f="$OUT_DIR/$f"
  [ -f "$f" ] || die "no such snapshot: ${1}"
  # Deliberately PRINTS the command instead of running it: restoring drops and
  # recreates every table in the target database. That is the operator's call,
  # made with their eyes open, not a flag on a backup script.
  cat <<EOF
${bold}Restore $(basename "$f")${off}

${ylw}This DROPS and recreates every table in '$PG_DB'. Take a snapshot first.${off}

  ${bold}./bin/backup_db.sh${off}
  ${bold}gunzip -c "$f" | $PG_BIN/psql -h $PG_HOST -p $PG_PORT -U $PG_USER -d $PG_DB${off}

Then re-apply the read-only AI role, which a reload wipes:

  ${bold}$PG_BIN/psql -h $PG_HOST -p $PG_PORT -U $PG_USER -d $PG_DB -f backend/scripts/create_ai_readonly_role.sql${off}
EOF
}

# --- the daily job ----------------------------------------------------------

cmd_install() {
  mkdir -p "$HOME/Library/LaunchAgents" "$OUT_DIR"
  # Retire the broken predecessor rather than leaving two backup agents around.
  if launchctl print "gui/$(id -u)/$OLD_LABEL" >/dev/null 2>&1; then
    launchctl bootout "gui/$(id -u)/$OLD_LABEL" 2>/dev/null || true
    launchctl disable "gui/$(id -u)/$OLD_LABEL" 2>/dev/null || true
    warn "unloaded $OLD_LABEL — it pointed at a script deleted in the cutover
   and had failed every night since (its plist is left on disk)"
  fi
  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$ROOT/bin/backup_db.sh</string>
  </array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>$PG_BIN:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    <key>TZ</key><string>Asia/Riyadh</string>
  </dict>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>2</integer><key>Minute</key><integer>0</integer></dict>
  <key>RunAtLoad</key><false/>
  <key>StandardOutPath</key><string>$HOME/Library/Logs/gi-hub-backup.log</string>
  <key>StandardErrorPath</key><string>$HOME/Library/Logs/gi-hub-backup.err</string>
</dict>
</plist>
EOF
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  launchctl enable "gui/$(id -u)/$LABEL" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$PLIST" \
    || die "could not load $LABEL — check $PLIST"
  ok "installed $LABEL — daily at 02:00"
  printf '   %splist: %s%s\n' "$dim" "$PLIST" "$off"
  printf '   %slogs:  ~/Library/Logs/gi-hub-backup.{log,err}%s\n' "$dim" "$off"
  printf '   %sNOTE: launchd runs a missed 02:00 job at the next wake, but only\n' "$dim"
  printf '   if the Mac is on. Run it by hand after a long shutdown.%s\n' "$off"
}

cmd_uninstall() {
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  launchctl disable "gui/$(id -u)/$LABEL" 2>/dev/null || true
  [ -f "$PLIST" ] && rm -f "$PLIST" && ok "removed $PLIST" || warn "$LABEL was not installed"
  printf '   %sSnapshots in %s are left untouched.%s\n' "$dim" "$OUT_DIR" "$off"
}

usage() {
  cat <<EOF
${bold}GI Hub — local database snapshots${off}

  ${bold}./bin/backup_db.sh${off}                 take a snapshot now
  ${bold}./bin/backup_db.sh --no-sqlite${off}     …Postgres only
  ${bold}./bin/backup_db.sh --list${off}          what is in .backups/
  ${bold}./bin/backup_db.sh --install${off}       daily at 02:00 via launchd
  ${bold}./bin/backup_db.sh --uninstall${off}     stop the daily job
  ${bold}./bin/backup_db.sh --restore FILE${off}  print the restore command

Snapshots: ${OUT_DIR#$ROOT/}/  (gitignored) · keeping the newest $KEEP of each kind
Override with GI_BACKUP_DIR and GI_BACKUP_KEEP.
EOF
}

case "${1:-}" in
  ''|--now|now)        cmd_backup yes ;;
  --no-sqlite)         cmd_backup no ;;
  --list|-l|list)      cmd_list ;;
  --install|install)   cmd_install ;;
  --uninstall|uninstall) cmd_uninstall ;;
  --restore|restore)   shift; cmd_restore "${1:-}" ;;
  -h|--help|help)      usage ;;
  *) usage; die "unknown option '${1}'" ;;
esac
