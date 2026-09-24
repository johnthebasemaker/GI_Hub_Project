#!/usr/bin/env python3
"""
tools/practice_db.py — build, reset and verify the Practice sandbox (rule 17).

    .venv/bin/python tools/practice_db.py wall      # role + CONNECT wall (idempotent)
    .venv/bin/python tools/practice_db.py build     # seed := synthetic fixture + overlay; then reset
    .venv/bin/python tools/practice_db.py reset     # sandbox := clone(seed)   (~1 s)
    .venv/bin/python tools/practice_db.py verify    # prove the walls hold; exit 1 if not

Connection settings (defaults are the local :5433 trust-auth mirror):

    PRACTICE_ADMIN_URL     a cluster-admin URL; its DATABASE is ignored
                           (default postgresql://postgres@127.0.0.1:5433/postgres)
    PRACTICE_DATABASE_URL  the URL the Practice API itself uses
                           (default postgresql://gi_training@127.0.0.1:5433/gihub_training)
    PRACTICE_DB_PASSWORD   gi_training's password (production; unset = trust auth)
    LIVE_DB                the Live database to wall off (default gihub)

────────────────────────────────────────────────────────────────────────────
THE THREE DATABASES

  gihub                 Live. `gi_training` has NO CONNECT on it — the wall.
  gihub_seed_training   the template. Synthetic fixture + overlay. Owned by
                        gi_training. Rebuilt from scratch by `build`, which is
                        why Practice never runs migrations-behind the way the
                        local mirror does: `cutover_migrate` builds at head.
  gihub_training        the sandbox the Practice API serves. `reset` drops it
                        and clones the seed — the same thing the Practice
                        admin's button does (practice.clone_from_seed).

⚠️ THE WALL IS PRIVILEGE, NOT CONFIGURATION. The Practice process could hold
Live's URL through any number of mistakes; rule 17's boot check catches the
ones it can see. The CONNECT revoke catches the rest: Postgres checks it AFTER
authentication, so it holds even on the local mirror's `trust` auth, where any
client may CLAIM any role. `verify` proves it by trying.

⚠️ THE REVOKE IS WIPED by any reload that recreates `gihub` (`dual_ci`,
`cutover_migrate --wipe` on a new database). Re-run `wall` afterwards — the
same ritual `create_ai_readonly_role.sql` already needs, and `verify` says so.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as _dt
import os
import pathlib
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from urllib.parse import urlsplit, urlunsplit

_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

os.environ.setdefault("GI_DOTENV", "0")   # a CLI never needs the Live secrets

PRACTICE_ROLE = "gi_training"
AI_RO_ROLE = "gi_ai_ro"
DEFAULT_ADMIN_URL = "postgresql://postgres@127.0.0.1:5433/postgres"
DEFAULT_PRACTICE_URL = "postgresql://gi_training@127.0.0.1:5433/gihub_training"


# ── URL plumbing ────────────────────────────────────────────────────────────
def _plain(url: str) -> str:
    for d in ("postgresql+asyncpg://", "postgresql+psycopg2://", "postgres://"):
        if url.startswith(d):
            return "postgresql://" + url[len(d):]
    return url


def with_db(url: str, db: str) -> str:
    p = urlsplit(_plain(url))
    return urlunsplit((p.scheme, p.netloc, "/" + db, p.query, ""))


def db_of(url: str) -> str:
    return urlsplit(_plain(url)).path.lstrip("/")


def admin_url() -> str:
    return os.environ.get("PRACTICE_ADMIN_URL", "").strip() or DEFAULT_ADMIN_URL


def practice_url() -> str:
    return os.environ.get("PRACTICE_DATABASE_URL", "").strip() or DEFAULT_PRACTICE_URL


def live_db() -> str:
    return os.environ.get("LIVE_DB", "").strip() or "gihub"


def names() -> tuple[str, str]:
    """(sandbox, seed), refused unless the sandbox is a Practice name."""
    from backend.api.practice import reset_targets
    return reset_targets(db_of(practice_url()))


@contextmanager
def connect(url: str):
    """psycopg2 in AUTOCOMMIT, yielding a cursor (CREATE/DROP DATABASE cannot
    run inside a transaction block — see testdb._connect for the trap)."""
    import psycopg2
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
    conn = psycopg2.connect(_plain(url))
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    try:
        with conn.cursor() as cur:
            yield cur
    finally:
        conn.close()


def _ident(name: str) -> str:
    if not name.replace("_", "").isalnum():
        raise SystemExit(f"❌ {name!r} is not a plain identifier")
    return f'"{name}"'


# ── the wall ────────────────────────────────────────────────────────────────
def wall_sql(live: str, role: str = PRACTICE_ROLE, password: str | None = None,
             ai_ro_exists: bool = True) -> list[str]:
    """The statements that build the wall. A function so suite TR-02 executes
    the SHIPPING SQL against a throwaway, not a paraphrase of it."""
    pw = f" PASSWORD '{password}'" if password else ""
    out = [
        f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') "
        f"THEN CREATE ROLE {_ident(role)} LOGIN; END IF; END $$",
        # CREATEDB is what the template reset needs. NOSUPERUSER/NOCREATEROLE
        # are stated, not assumed: a superuser bypasses every CONNECT check.
        f"ALTER ROLE {_ident(role)} WITH LOGIN CREATEDB NOSUPERUSER NOCREATEROLE"
        f" NOREPLICATION NOBYPASSRLS{pw}",
        f"REVOKE CONNECT ON DATABASE {_ident(live)} FROM PUBLIC",
        f"REVOKE CONNECT ON DATABASE {_ident(live)} FROM {_ident(role)}",
    ]
    if ai_ro_exists:
        # The NL→SQL role reached Live through PUBLIC's default grant as well as
        # its own explicit one; keep the explicit one so the revoke above
        # changes nothing for it.
        out.append(f"GRANT CONNECT ON DATABASE {_ident(live)} TO {_ident(AI_RO_ROLE)}")
    return out


def cmd_wall() -> int:
    live = live_db()
    with connect(admin_url()) as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (live,))
        if not cur.fetchone():
            print(f"❌ Live database {live!r} not found on this cluster")
            return 1
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (AI_RO_ROLE,))
        ai = bool(cur.fetchone())
        for stmt in wall_sql(live, password=os.environ.get("PRACTICE_DB_PASSWORD") or None,
                             ai_ro_exists=ai):
            cur.execute(stmt)
    print(f"✅ wall: role {PRACTICE_ROLE} (LOGIN CREATEDB, no superuser) · "
          f"CONNECT on {live!r} revoked from PUBLIC and {PRACTICE_ROLE}")
    return 0


# ── build ───────────────────────────────────────────────────────────────────
def _run(argv: list[str], env: dict | None = None, label: str = "") -> None:
    proc = subprocess.run(argv, cwd=str(_ROOT), env=env, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stdout.write(proc.stdout[-3000:])
        sys.stderr.write(proc.stderr[-3000:])
        raise SystemExit(f"❌ {label or argv[1]} failed (exit {proc.returncode})")
    tail = [ln for ln in proc.stdout.splitlines() if ln.strip()][-6:]
    for ln in tail:
        print("   " + ln)


def cmd_build(today: _dt.date) -> int:
    sandbox, seed = names()
    py = sys.executable
    with tempfile.TemporaryDirectory(prefix="practice-") as tmp:
        fixture = pathlib.Path(tmp) / "practice_fixture.db"
        print(f"▶ 1/5 synthetic fixture (make_tutorial_db, anchored {today})")
        _run([py, "tools/make_tutorial_db.py", "--out", str(fixture),
              "--today", today.isoformat()], label="make_tutorial_db")
        real = _ROOT / "gi_database.db"
        if real.exists():
            # Read-only, opt-in, a membership question — P12-0's own check.
            _run([py, "tools/make_tutorial_db.py", "--collision-check", str(real)],
                 label="collision check")

        print(f"▶ 2/5 fresh seed database {seed!r} owned by {PRACTICE_ROLE}")
        with connect(admin_url()) as cur:
            cur.execute(f"DROP DATABASE IF EXISTS {_ident(seed)} WITH (FORCE)")
            cur.execute(f"CREATE DATABASE {_ident(seed)} OWNER {_ident(PRACTICE_ROLE)}")

        print("▶ 3/5 cutover_migrate — the SAME loader the tutorials and E2E use (P12-4)")
        seed_as_practice = with_db(practice_url(), seed).replace(
            "postgresql://", "postgresql+psycopg2://", 1)
        env = dict(os.environ, GI_DOTENV="0")
        env.pop("DATABASE_URL", None)
        _run([py, "tools/migration/cutover_migrate.py", "--wipe",
              "--source", str(fixture), "--target", seed_as_practice],
             env=env, label="cutover_migrate")

    print("▶ 4/5 NL→SQL read-only role grants on the seed")
    sql = (_ROOT / "backend" / "scripts" / "create_ai_readonly_role.sql").read_text()
    with connect(with_db(admin_url(), seed)) as cur:
        cur.execute(sql.replace("ON DATABASE gihub ", f"ON DATABASE {_ident(seed)} "))

    print("▶ 5/5 overlay — practice accounts, seeded queues, Practice settings")
    env = {k: v for k, v in os.environ.items()}
    # An EPHEMERAL signing key: the overlay mints tokens only to drive the app
    # in-process, and inside the production image the inherited JWT_SECRET is
    # LIVE's — which a Practice process must never hold, even briefly.
    import secrets as _secrets
    env.update(JWT_SECRET=_secrets.token_hex(32))
    env.update(GI_INSTANCE="training", GI_DOTENV="0", GI_SCHEDULER="0",
               DATABASE_URL=with_db(practice_url(), seed).replace(
                   "postgresql://", "postgresql+asyncpg://", 1))
    for k in ("WHATSAPP_TOKEN", "WHATSAPP_PHONE_NUMBER_ID", "SMTP_HOST", "SMTP_SERVER",
              "SMTP_USER", "SMTP_PASS", "GI_AI_VISION_API_KEY", "GI_AI_RO_URL"):
        env.pop(k, None)
    proc = subprocess.run([py, "tools/practice_overlay.py"], cwd=str(_ROOT), env=env,
                          text=True)
    if proc.returncode != 0:
        raise SystemExit(f"❌ overlay failed (exit {proc.returncode})")
    return cmd_reset()


# ── reset ───────────────────────────────────────────────────────────────────
def cmd_reset() -> int:
    """The CLI twin of the Practice admin's button — same clone function, run
    as the Practice role (so Postgres refuses it anything it does not own)."""
    from backend.api.practice import _maintenance_dsn, clone_from_seed
    sandbox, seed = names()
    asyncio.run(clone_from_seed(_maintenance_dsn(practice_url()), sandbox))
    stamp = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    with connect(practice_url()) as cur:
        for k, v in (("practice_reset_at", stamp), ("practice_reset_by", "practice_db.py")):
            cur.execute("INSERT INTO app_settings (key, value) VALUES (%s, %s) "
                        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (k, v))
    print(f"✅ reset: {sandbox!r} cloned from {seed!r} at {stamp}")
    return 0


# ── verify ──────────────────────────────────────────────────────────────────
def cmd_verify() -> int:
    import psycopg2
    sandbox, seed = names()
    live = live_db()
    fails: list[str] = []

    def ok(label: str, cond: bool, detail: str = "") -> None:
        print(f"  {'✅' if cond else '❌'} {label}" + (f" — {detail}" if detail and not cond else ""))
        if not cond:
            fails.append(label)

    try:
        with connect(with_db(practice_url(), live)) as cur:
            cur.execute("SELECT 1")
        ok(f"{PRACTICE_ROLE} is REFUSED by Live {live!r}", False,
           "it connected — run `practice_db.py wall` (a reload wipes the revoke)")
    except psycopg2.OperationalError as e:
        ok(f"{PRACTICE_ROLE} is REFUSED by Live {live!r}",
           "permission denied" in str(e), str(e).strip()[:160])

    try:
        with connect(practice_url()) as cur:
            cur.execute("SELECT current_user, current_database()")
            who, db = cur.fetchone()
            ok("the Practice URL opens the sandbox as the Practice role",
               who == PRACTICE_ROLE and db == sandbox, f"{who}@{db}")
            cur.execute("SELECT rolsuper, rolcreatedb FROM pg_roles WHERE rolname = current_user")
            sup, cdb = cur.fetchone()
            ok(f"{PRACTICE_ROLE} is not a superuser (a superuser ignores CONNECT)", not sup)
            ok(f"{PRACTICE_ROLE} has CREATEDB (the reset needs it)", bool(cdb))
            from backend.api.auth import ROLE_META
            cur.execute("SELECT role FROM users WHERE username LIKE 'practice.%%'")
            roles = {r[0] for r in cur.fetchall()}
            ok("every role in ROLE_META has a practice account",
               set(ROLE_META) <= roles, f"missing {sorted(set(ROLE_META) - roles)}")
            cur.execute("SELECT count(*) FROM users WHERE username IN "
                        "('admin','hod','supervisor','worker','Logistics')")
            ok("the published tutorial logins are gone", cur.fetchone()[0] == 0)
            cur.execute("SELECT count(*) FROM pending_issues WHERE status = 'pending_hod'")
            ok("the HOD approval queue has work in it", cur.fetchone()[0] > 0)
            cur.execute("SELECT value FROM app_settings WHERE key = 'mfa_required_roles'")
            r = cur.fetchone()
            ok("2FA mandate is off (ruling Q4)", r is not None and r[0] == "")
            cur.execute("SELECT count(*) FROM employees WHERE \"Phone_Number\" <> %s",
                        ("+000000000000",))
            ok("no employee carries a routable phone number", cur.fetchone()[0] == 0)
            cur.execute("SELECT value FROM app_settings WHERE key = 'practice_dataset_version'")
            r = cur.fetchone()
            ok("the dataset version is stamped", bool(r and r[0]), "")
    except psycopg2.OperationalError as e:
        ok("the Practice URL opens the sandbox", False, str(e).strip()[:160])

    with connect(admin_url()) as cur:
        cur.execute("SELECT datname, pg_get_userbyid(datdba) FROM pg_database "
                    "WHERE datname IN (%s, %s)", (sandbox, seed))
        owners = dict(cur.fetchall())
    ok(f"{PRACTICE_ROLE} owns the sandbox and the seed — and therefore nothing else it can drop",
       owners.get(sandbox) == PRACTICE_ROLE and owners.get(seed) == PRACTICE_ROLE, str(owners))

    print(f"{'✅' if not fails else '❌'} practice verify: {len(fails)} problem(s)")
    return 1 if fails else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("command", choices=("wall", "build", "reset", "verify"))
    ap.add_argument("--today", default=None,
                    help="anchor for the synthetic dates (default: today). The "
                         "tutorial RENDERS keep their pinned anchor; only the "
                         "sandbox moves with the clock.")
    a = ap.parse_args(argv)
    if a.command == "wall":
        return cmd_wall()
    if a.command == "build":
        today = _dt.date.fromisoformat(a.today) if a.today else _dt.date.today()
        return cmd_build(today)
    if a.command == "reset":
        return cmd_reset()
    return cmd_verify()


if __name__ == "__main__":
    sys.exit(main())
