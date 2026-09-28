"""Is this database at the code's migration head? (Phase 15a)

Rule 17 made Practice a second PROCESS on a second database, which means two
databases to migrate. On a server `deploy-v2.sh` rebuilds the Practice seed at
head on every deploy; on a dev box nothing did, and on 2026-09-27 both Practice
databases were found six migrations behind — every read of a Phase 14 column
500'd while service_tests (on their own, current DB) stayed green.

So the process now asks at boot:

  · a PRACTICE process whose database is not at head REFUSES TO START, with the
    one command that fixes it. A sandbox is disposable and a stale one only
    ever produces 500s, so failing loudly costs nothing.
  · a LIVE process only WARNS. Its database is the operator's data, and
    migrating it is a decision for a person, never for a boot hook.

`schema_problem` is pure so suite TR drives every branch without a server.
"""
from __future__ import annotations

import pathlib
from functools import lru_cache
from typing import Iterable, Optional

_ALEMBIC_DIR = pathlib.Path(__file__).resolve().parent.parent / "alembic"

FIX_PRACTICE = ".venv/bin/python tools/practice_db.py migrate"
FIX_LIVE = "cd backend && ../.venv/bin/alembic upgrade head"


@lru_cache(maxsize=1)
def code_heads() -> tuple[str, ...]:
    """The revision(s) the code expects. Empty when the scripts are not
    shipped with this process — then nothing can be said, and nothing is."""
    if not _ALEMBIC_DIR.is_dir():
        return ()
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    cfg = Config()
    cfg.set_main_option("script_location", str(_ALEMBIC_DIR))
    return tuple(sorted(ScriptDirectory.from_config(cfg).get_heads()))


def schema_problem(db_revs: Optional[Iterable[str]], heads: Iterable[str]) -> Optional[str]:
    """None when the database is at head, else one sentence saying how not.

    `db_revs` is None when the database has no `alembic_version` table at all.
    """
    want = sorted(set(heads))
    if not want:
        return None
    if db_revs is None:
        return "the database has no alembic_version table — it was never migrated or stamped"
    have = sorted(set(db_revs))
    if have == want:
        return None
    return (f"the database is at {', '.join(have) or '(no revision)'} but the code "
            f"expects {', '.join(want)}")


async def db_revisions(session) -> Optional[list[str]]:
    from sqlalchemy import text
    exists = (await session.execute(text(
        "SELECT to_regclass('public.alembic_version') IS NOT NULL"))).scalar()
    if not exists:
        return None
    return [r[0] for r in (await session.execute(
        text("SELECT version_num FROM alembic_version"))).all()]


async def check(session) -> Optional[str]:
    return schema_problem(await db_revisions(session), code_heads())
