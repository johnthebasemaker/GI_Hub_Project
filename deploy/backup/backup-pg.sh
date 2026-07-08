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
#
# OFF-BOX to S3 (Phase I-A): set these on the service (deploy/.env) to also push
# each dump to AWS S3. Left unset → local-only (the S3 step is skipped cleanly).
#   AWS_S3_BUCKET       target bucket (REQUIRED to enable S3)
#   AWS_S3_PREFIX       key prefix (default: gihub)
#   AWS_DEFAULT_REGION  e.g. eu-central-1
#   AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY   IAM creds (put/list only)
#   AWS_SSE             server-side encryption (default: AES256)
# S3 *retention* is a bucket lifecycle policy (e.g. 30d→Glacier, 90d→expire) —
# NOT scripted here, so the archive can outlive the 14-day local copy.
# ============================================================================
set -eu

BACKUP_DIR="${GI_BACKUP_DIR:-/backups}"
PGHOST="${PGHOST:-db}"
PGPORT="${PGPORT:-5432}"
STAMP="$(date +%Y%m%d-%H%M%S)"
DUMP="$BACKUP_DIR/gihub-${STAMP}.dump"

mkdir -p "$BACKUP_DIR"

# Push a dump off-box to S3. Never fails the backup (the local dump already
# succeeded); records .last_s3_success / .last_s3_failure markers instead.
push_to_s3() {
    [ -n "${AWS_S3_BUCKET:-}" ] || return 0          # S3 not configured → skip
    if ! command -v aws >/dev/null 2>&1; then
        echo "[pg-backup] AWS_S3_BUCKET set but aws CLI missing — skipping S3" >&2
        date -u +%Y-%m-%dT%H:%M:%SZ > "$BACKUP_DIR/.last_s3_failure"
        return 0
    fi
    dest="s3://${AWS_S3_BUCKET}/${AWS_S3_PREFIX:-gihub}/$(basename "$DUMP")"
    if aws s3 cp "$DUMP" "$dest" --sse "${AWS_SSE:-AES256}" --only-show-errors; then
        date -u +%Y-%m-%dT%H:%M:%SZ > "$BACKUP_DIR/.last_s3_success"
        echo "[pg-backup] off-box → ${dest}"
    else
        date -u +%Y-%m-%dT%H:%M:%SZ > "$BACKUP_DIR/.last_s3_failure"
        echo "[pg-backup] S3 upload FAILED (local dump kept)" >&2
    fi
}

# Custom-format dump (-Fc): compressed, restorable with pg_restore, matches the
# console endpoint's naming (gihub-<stamp>.dump) so retention covers both.
if pg_dump -h "$PGHOST" -p "$PGPORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
        -Fc -f "$DUMP"; then
    # 14-day LOCAL retention (mirrors the v1 SQLite backup). Covers manual dumps.
    find "$BACKUP_DIR" -name 'gihub-*.dump' -mtime +14 -delete 2>/dev/null || true
    date -u +%Y-%m-%dT%H:%M:%SZ > "$BACKUP_DIR/.last_success"
    echo "[pg-backup] ok → gihub-${STAMP}.dump"
    push_to_s3
else
    date -u +%Y-%m-%dT%H:%M:%SZ > "$BACKUP_DIR/.last_failure"
    echo "[pg-backup] FAILED" >&2
    exit 1
fi
