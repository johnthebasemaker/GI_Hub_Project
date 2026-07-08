#!/bin/sh
# ============================================================================
# deploy/backup/backup-pg.sh — nightly pg_dump of the v2 Postgres system of
# record. This is the NEW-STACK counterpart to the v1 SQLite backup
# (docker/backup/backup.sh); the v1 job only dumps SQLite and cannot protect
# Postgres, so `pg-data-prod` needs its own backup before v2 goes live.
#
# Runs inside the `backup` service (postgres:16-alpine → pg_dump matches the db
# server version), fired by crond. Writes:
#   - gihub-<STAMP>.dump   custom-format dump (-Fc), same format + directory as
#                          the console's manual /admin/backup endpoint, so both
#                          land together and share the 14-day retention sweep.
#   - .last_success / .last_failure   ISO-8601 markers, same convention as the
#                          v1 backup so the Admin "Service Health" card reads
#                          them unchanged (GI_BACKUPS_DIR).
#
# pg_dump reads the target from the environment set on the service:
#   POSTGRES_USER, POSTGRES_DB, PGPASSWORD, PGHOST (default `db`), PGPORT.
# ============================================================================
set -eu

BACKUP_DIR="${GI_BACKUP_DIR:-/backups}"
PGHOST="${PGHOST:-db}"
PGPORT="${PGPORT:-5432}"
STAMP="$(date +%Y%m%d-%H%M%S)"

mkdir -p "$BACKUP_DIR"

# Custom-format dump (-Fc): compressed, restorable with pg_restore, matches the
# console endpoint's naming (gihub-<stamp>.dump) so retention covers both.
if pg_dump -h "$PGHOST" -p "$PGPORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
        -Fc -f "$BACKUP_DIR/gihub-${STAMP}.dump"; then
    # 14-day retention (mirrors the v1 SQLite backup). Covers manual dumps too.
    find "$BACKUP_DIR" -name 'gihub-*.dump' -mtime +14 -delete 2>/dev/null || true
    date -u +%Y-%m-%dT%H:%M:%SZ > "$BACKUP_DIR/.last_success"
    echo "[pg-backup] ok → gihub-${STAMP}.dump"
else
    date -u +%Y-%m-%dT%H:%M:%SZ > "$BACKUP_DIR/.last_failure"
    echo "[pg-backup] FAILED" >&2
    exit 1
fi
