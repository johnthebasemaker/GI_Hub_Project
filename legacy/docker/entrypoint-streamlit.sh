#!/bin/sh
# ============================================================================
# entrypoint-streamlit.sh — persist mutable state into the gi-data volume
# WITHOUT touching any application code.
#
# The app hardcodes DB_FILE = "gi_database.db" (relative to CWD = /app) and
# UPLOADS_ROOT = "uploads". We symlink both into /app/data (the named volume)
# so init_db() and attachment mirrors write straight into the persistent volume.
# SQLite resolves the symlink and creates its -wal/-shm siblings beside the
# real file inside the volume. The Mac never runs this script.
# ============================================================================
set -e

DATA_DIR="${GI_DATA_DIR:-/app/data}"

mkdir -p "$DATA_DIR/uploads" "$DATA_DIR/material_photos"

# SQLite DB → volume (target created on first init_db run; dangling link is fine)
if [ ! -L /app/gi_database.db ]; then
    rm -f /app/gi_database.db
    ln -s "$DATA_DIR/gi_database.db" /app/gi_database.db
fi

# Uploads disk-mirror → volume (BLOBs in SQLite remain authoritative)
if [ ! -L /app/uploads ]; then
    rm -rf /app/uploads
    ln -s "$DATA_DIR/uploads" /app/uploads
fi

exec "$@"
