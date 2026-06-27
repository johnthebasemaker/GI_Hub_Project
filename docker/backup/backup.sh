#!/bin/sh
# ============================================================================
# backup.sh — nightly SQLite online backup + uploads/photos mirror
# Runs inside the `backup` service (alpine + sqlite + rsync), fired by crond.
# Writes .last_success / .last_failure markers that the Admin "Service Health"
# card will read in the backup step. 14-day retention on DB snapshots.
# The NAS/Hetzner-Storage-Box destination is wired via the gi-backups volume.
# ============================================================================
set -eu

DATA_DIR="${GI_DATA_DIR:-/data}"
BACKUP_DIR="${GI_BACKUP_DIR:-/backups}"
STAMP="$(date +%Y%m%d_%H%M%S)"

mkdir -p "$BACKUP_DIR"

if [ ! -f "$DATA_DIR/gi_database.db" ]; then
    echo "[backup] no DB yet at $DATA_DIR/gi_database.db — skipping this pass"
    exit 0
fi

# SQLite online backup is safe even while Streamlit holds the file (WAL).
if sqlite3 "$DATA_DIR/gi_database.db" ".backup '$BACKUP_DIR/sqlite_${STAMP}.db'"; then
    rsync -a --delete "$DATA_DIR/uploads/"          "$BACKUP_DIR/uploads_latest/"         2>/dev/null || true
    rsync -a --delete "$DATA_DIR/material_photos/"  "$BACKUP_DIR/material_photos_latest/" 2>/dev/null || true
    find "$BACKUP_DIR" -name 'sqlite_*.db' -mtime +14 -delete 2>/dev/null || true
    date -u +%Y-%m-%dT%H:%M:%SZ > "$BACKUP_DIR/.last_success"
    echo "[backup] ok → sqlite_${STAMP}.db"
else
    date -u +%Y-%m-%dT%H:%M:%SZ > "$BACKUP_DIR/.last_failure"
    echo "[backup] FAILED" >&2
    exit 1
fi
