"""
migrate_role.py — One-time migration script.
Renames the 'worker' role to 'store_keeper' in the live database.
Run ONCE before restarting the app:  python3 migrate_role.py

SQLite does not support ALTER COLUMN, so we use the standard
rename-copy-drop pattern to update the CHECK constraint as well.
"""
import sqlite3
from config import DB_FILE

conn = sqlite3.connect(DB_FILE)
c = conn.cursor()

# ── 1. Migrate pending_users (no CHECK constraint, simple UPDATE) ────────────
c.execute("UPDATE pending_users SET role = 'store_keeper' WHERE role = 'worker'")

# ── 2. Migrate users table (has CHECK constraint — must rebuild) ─────────────
c.execute("PRAGMA foreign_keys = OFF")

c.execute("ALTER TABLE users RENAME TO _users_migration_old")

c.execute("""
    CREATE TABLE users (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        username      TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        role          TEXT NOT NULL CHECK(role IN ('admin','hod','supervisor','store_keeper')),
        Site_ID       TEXT DEFAULT 'HQ',
        Phone_Number  TEXT,
        created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
    )
""")

# Copy all rows, renaming 'worker' → 'store_keeper' inline
c.execute("""
    INSERT INTO users (id, username, password_hash, role, Site_ID, Phone_Number, created_at)
    SELECT
        id,
        username,
        password_hash,
        CASE WHEN role = 'worker' THEN 'store_keeper' ELSE role END,
        Site_ID,
        Phone_Number,
        created_at
    FROM _users_migration_old
""")

c.execute("DROP TABLE _users_migration_old")
c.execute("PRAGMA foreign_keys = ON")

conn.commit()

c.execute("SELECT COUNT(*) FROM users WHERE role = 'store_keeper'")
count = c.fetchone()[0]
conn.close()

print(f"Migration complete. {count} user(s) now carry role='store_keeper'.")
