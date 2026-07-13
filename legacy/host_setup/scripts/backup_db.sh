#!/usr/bin/env zsh
# -----------------------------------------------------------------------------
# backup_db.sh — nightly DB + attachments backup for GI Hub
# Fired at 02:00 by ~/Library/LaunchAgents/com.gi.backup.plist.
#
# What it does:
#   1. SQLite online backup → ~/Library/Mobile Documents/com~apple~CloudDocs/GI_Hub_Backups/
#      (iCloud Drive, encrypted, syncs to Apple's servers — independent of
#      Cloudflare, Fly, or any other vendor).
#   2. rsync attachments mirror so disk-mirror BLOBs are also versioned.
#   3. Prune snapshots older than 14 days.
#   4. Optional second destination (set GI_BACKUP_EXTRA env var to a path).
#
# Restore:
#   .venv/bin/python -c "
#   import sqlite3
#   src = sqlite3.connect('PATH_TO_BACKUP/gi_database_20260612_020000.db')
#   dst = sqlite3.connect('gi_database.db')
#   src.backup(dst); dst.close(); src.close()"
# -----------------------------------------------------------------------------

set -euo pipefail

# Locate the project dir relative to this script (it lives at host_setup/scripts/)
SCRIPT_DIR="${0:A:h}"
PROJECT_DIR="${SCRIPT_DIR:h:h}"
DB_FILE="$PROJECT_DIR/gi_database.db"
UPLOADS_DIR="$PROJECT_DIR/uploads"

ICLOUD_BACKUP_DIR="$HOME/Library/Mobile Documents/com~apple~CloudDocs/GI_Hub_Backups"
RETENTION_DAYS=14

STAMP=$(date +%Y%m%d_%H%M%S)
LOG_TAG="[gi-backup $(date '+%F %T')]"

mkdir -p "$ICLOUD_BACKUP_DIR/db" "$ICLOUD_BACKUP_DIR/uploads_latest"

echo "$LOG_TAG ---- starting backup ----"

# ── 1. SQLite online backup ──────────────────────────────────────────────────
if [[ ! -f "$DB_FILE" ]]; then
    echo "$LOG_TAG ERROR: $DB_FILE not found" >&2
    exit 1
fi

DB_OUT="$ICLOUD_BACKUP_DIR/db/gi_database_${STAMP}.db"
if command -v sqlite3 >/dev/null 2>&1; then
    sqlite3 "$DB_FILE" ".backup '$DB_OUT'"
    echo "$LOG_TAG SQLite snapshot → $DB_OUT ($(du -h "$DB_OUT" | cut -f1))"
else
    echo "$LOG_TAG sqlite3 not on PATH, falling back to cp (less safe)"
    cp "$DB_FILE" "$DB_OUT"
fi

# ── 2. Attachments mirror (incremental rsync) ────────────────────────────────
if [[ -d "$UPLOADS_DIR" ]]; then
    rsync -a --delete \
        --exclude '.DS_Store' \
        "$UPLOADS_DIR/" \
        "$ICLOUD_BACKUP_DIR/uploads_latest/"
    UPLOAD_COUNT=$(find "$ICLOUD_BACKUP_DIR/uploads_latest" -type f | wc -l | tr -d ' ')
    echo "$LOG_TAG uploads mirror updated ($UPLOAD_COUNT files)"
fi

# ── 3. Prune old DB snapshots ────────────────────────────────────────────────
find "$ICLOUD_BACKUP_DIR/db" \
    -name 'gi_database_*.db' \
    -type f \
    -mtime +"$RETENTION_DAYS" \
    -delete
KEPT=$(find "$ICLOUD_BACKUP_DIR/db" -name 'gi_database_*.db' -type f | wc -l | tr -d ' ')
echo "$LOG_TAG prune complete, kept $KEPT snapshot(s)"

# ── 4. Optional extra destination (e.g. external SSD or rclone-mounted cloud) ─
if [[ -n "${GI_BACKUP_EXTRA:-}" ]] && [[ -d "$GI_BACKUP_EXTRA" ]]; then
    cp "$DB_OUT" "$GI_BACKUP_EXTRA/"
    rsync -a --delete "$UPLOADS_DIR/" "$GI_BACKUP_EXTRA/uploads_latest/"
    echo "$LOG_TAG extra destination updated: $GI_BACKUP_EXTRA"
fi

echo "$LOG_TAG ---- backup OK ----"
