"""
scripts/clean_inventory_sites.py — Site_ID canonicalisation
============================================================
Migrates the legacy "HQ" Site_ID value to the canonical "CNCEC" site
across every table that carries a Site_ID column, then removes "HQ"
from the system_settings Site catalogue so it can no longer be picked
in dropdowns.

WHY THIS EXISTS
---------------
"HQ" was the default placeholder value seeded during early Excel→SQL
migration. It was never a physically distinct site — just the testing
default. CNCEC is the operationally active site that absorbed all of
that history.

Per user decision (Phase 6 cleanup round): rename HQ → CNCEC across
every table including legacy seeded users.

SAFETY
------
- Default mode is DRY-RUN. Pass `--apply` to commit changes.
- Timestamped backup of gi_database.db is taken BEFORE any UPDATE.
- Whole migration wraps in a single transaction — partial failure rolls
  back cleanly.
- Verifies zero "HQ" rows remain across all tables post-commit.

USAGE
-----
    # Dry run (default) — shows what would change, no writes.
    python scripts/clean_inventory_sites.py

    # Apply for real, with backup.
    python scripts/clean_inventory_sites.py --apply
"""

from __future__ import annotations

import argparse
import datetime
import shutil
import sqlite3
import sys
from pathlib import Path

# Repo root resolution — script lives in scripts/, DB lives at repo root.
SCRIPT_PATH = Path(__file__).resolve()
REPO_ROOT   = SCRIPT_PATH.parent.parent
DB_PATH     = REPO_ROOT / "gi_database.db"

# ---------------------------------------------------------------------------
# THE mapping. Single-key dict because HQ is the only legacy value to
# canonicalise per the audit. Add more entries here if future cleanup
# rounds find more.
# ---------------------------------------------------------------------------
LEGACY_TO_OFFICIAL: dict[str, str] = {
    "HQ": "CNCEC",
}

# After the canonical rename, drop the legacy value from the Site catalogue
# so it stops appearing in dropdowns. Kept separate from LEGACY_TO_OFFICIAL
# because system_settings.value is namespaced by category — we only want
# to delete the "Site" rows, not, e.g., a Work_Type that happens to be
# named the same.
LEGACY_SETTINGS_TO_DROP: list[tuple[str, str]] = [
    ("Site", "HQ"),
]


def find_site_id_tables(conn: sqlite3.Connection) -> list[str]:
    """Return every table in the DB that carries a Site_ID column."""
    out = []
    for (name,) in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ):
        cols = {row[1] for row in conn.execute(f'PRAGMA table_info("{name}")')}
        if "Site_ID" in cols:
            out.append(name)
    return out


def count_legacy(conn: sqlite3.Connection, tables: list[str]) -> dict[str, dict[str, int]]:
    """For each table × legacy value, return {table: {legacy: count}}."""
    out: dict[str, dict[str, int]] = {}
    for t in tables:
        out[t] = {}
        for legacy in LEGACY_TO_OFFICIAL:
            n = conn.execute(
                f'SELECT COUNT(*) FROM "{t}" WHERE Site_ID = ?', (legacy,)
            ).fetchone()[0]
            out[t][legacy] = n
    return out


def take_backup() -> Path:
    """Copy gi_database.db to gi_database.YYYYMMDD-HHMMSS.bak alongside it."""
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = DB_PATH.with_suffix(f".{stamp}.bak")
    shutil.copy2(DB_PATH, dest)
    return dest


def main() -> int:
    p = argparse.ArgumentParser(description="Canonicalise legacy Site_ID values.")
    p.add_argument(
        "--apply", action="store_true",
        help="Actually commit changes. Default is dry-run.",
    )
    args = p.parse_args()

    if not DB_PATH.exists():
        print(f"✖ Database not found at {DB_PATH}")
        return 2

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"▶ Mode: {mode}")
    print(f"▶ DB:   {DB_PATH}")
    print(f"▶ Mapping: {LEGACY_TO_OFFICIAL}")
    print()

    conn = sqlite3.connect(str(DB_PATH))
    try:
        tables = find_site_id_tables(conn)
        print(f"▶ Found {len(tables)} tables with Site_ID column")

        # Pre-flight: count every legacy value × table.
        pre_counts = count_legacy(conn, tables)
        total_pre = sum(
            n for legacy_counts in pre_counts.values()
            for n in legacy_counts.values()
        )
        print(f"▶ {total_pre} total legacy rows to migrate")
        print()
        print(f"  {'Table':30s}  {'Legacy':10s}  {'Rows':>6s}")
        print(f"  {'-'*30}  {'-'*10}  {'-'*6}")
        for t in tables:
            for legacy, n in pre_counts[t].items():
                if n > 0:
                    print(f"  {t:30s}  {legacy:10s}  {n:>6d}")
        print()

        if total_pre == 0 and not _legacy_settings_present(conn):
            print("✔ Nothing to migrate. Database is already clean.")
            return 0

        if not args.apply:
            print("▶ Dry-run only. Re-run with --apply to commit changes.")
            return 0

        # ─── APPLY MODE ─────────────────────────────────────────────────
        backup = take_backup()
        print(f"▶ Backup written → {backup.name}")

        # Single transaction across every UPDATE + the system_settings DELETE.
        try:
            conn.execute("BEGIN")
            applied_counts: dict[str, dict[str, int]] = {}
            for t in tables:
                applied_counts[t] = {}
                for legacy, official in LEGACY_TO_OFFICIAL.items():
                    cur = conn.execute(
                        f'UPDATE "{t}" SET Site_ID = ? WHERE Site_ID = ?',
                        (official, legacy),
                    )
                    applied_counts[t][legacy] = cur.rowcount

            # Drop legacy Site catalogue entries.
            settings_deleted = 0
            for category, value in LEGACY_SETTINGS_TO_DROP:
                cur = conn.execute(
                    "DELETE FROM system_settings WHERE category = ? AND value = ?",
                    (category, value),
                )
                settings_deleted += cur.rowcount

            # Sanity check before commit: zero legacy rows must remain.
            post_counts = count_legacy(conn, tables)
            remaining = sum(
                n for d in post_counts.values() for n in d.values()
            )
            if remaining != 0:
                print(f"✖ Sanity check failed: {remaining} legacy rows remain. Rolling back.")
                conn.execute("ROLLBACK")
                return 3

            conn.execute("COMMIT")
        except Exception as e:
            conn.execute("ROLLBACK")
            print(f"✖ Migration failed mid-flight, rolled back: {type(e).__name__}: {e}")
            return 4

        # Report.
        print()
        print(f"  {'Table':30s}  {'Legacy → Official':25s}  {'Rows updated':>14s}")
        print(f"  {'-'*30}  {'-'*25}  {'-'*14}")
        total_applied = 0
        for t in tables:
            for legacy, n in applied_counts[t].items():
                if n > 0:
                    arrow = f"{legacy} → {LEGACY_TO_OFFICIAL[legacy]}"
                    print(f"  {t:30s}  {arrow:25s}  {n:>14d}")
                    total_applied += n
        print()
        print(f"▶ {total_applied} rows updated across {sum(1 for d in applied_counts.values() for n in d.values() if n>0)} tables")
        print(f"▶ {settings_deleted} legacy system_settings rows deleted")
        print(f"▶ Backup available at: {backup}")
        return 0
    finally:
        conn.close()


def _legacy_settings_present(conn: sqlite3.Connection) -> bool:
    for category, value in LEGACY_SETTINGS_TO_DROP:
        n = conn.execute(
            "SELECT COUNT(*) FROM system_settings WHERE category=? AND value=?",
            (category, value),
        ).fetchone()[0]
        if n > 0:
            return True
    return False


if __name__ == "__main__":
    sys.exit(main())
