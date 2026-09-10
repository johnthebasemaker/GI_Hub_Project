"""slice 10b — execution Shift, the daily-job claim, and the training registry

Revision ID: e7f2a4c916b8
Revises: d5b8c3f92a41
Create Date: 2026-09-03 09:00:00

Three unrelated-looking additions in one revision, because they ship in one
slice and a database that has half of them is a state nobody should have to
reason about.

────────────────────────────────────────────────────────────────────────────
1. `sme_execution_entry.Shift`

Track 2 asks for a chase alert about material "staged for DAY-SHIFT work", and
there was no way to say which shift an entry belongs to. Inferring it from the
clock was considered and rejected: an entry is filed when somebody gets to a
desk, not when the work happened, and a night crew filing at 06:40 would be
counted as day shift on the strength of a timestamp nobody looked at.

NULLABLE ON PURPOSE. Every one of the existing entries predates the column and
there is no honest value to backfill — 'Day' would be a guess printed into the
record. The probe treats NULL as "not known to be day shift" and skips it, so
the feature starts empty and fills as people use it, exactly like `WBS_Number`
did in Phase 9a.

────────────────────────────────────────────────────────────────────────────
2. `daily_job_runs` — and a bug this revision exists to close

⚠️ THE DAILY LOOPS HAVE BEEN FIRING FOUR TIMES A DAY IN PRODUCTION.

`report_center.scheduler_loop` claims its work with an atomic `last_run`
UPDATE, and its docstring explains why. The three DAILY loops — the 07:00
morning briefing, the 16:00 evening digest and the Friday 17:00 executive
report — do not. Each simply sleeps until its hour and dispatches, in every
worker. `deploy/Dockerfile.api` runs `uvicorn --workers 4`, so every one of
those messages has been sent four times to every recipient.

It is invisible in development (one worker, one copy) and invisible in the
tests (the loops are disabled by GI_SCHEDULER=0), which is how it survived
three phases. This table is the claim those loops were missing: one row per
job key, and the UPDATE that moves `last_run` past the due time returns a row
to exactly one caller.

────────────────────────────────────────────────────────────────────────────
3. The training registry (Track 5)

`training_modules` / `training_assets` / `training_compliance`.

⚠️ `module_version` IS PART OF THE COMPLIANCE UNIQUE KEY, and that is the whole
design. Keyed on `(username, module_id)` alone, re-recording a tutorial because
the workflow changed would leave everyone certified against a video they have
never seen — which is worse than having no record, because it looks like
evidence. Bumping `training_modules.version` invalidates every acknowledgement
of the previous one by construction rather than by a script somebody has to
remember to run.

⚠️ AND VIDEOS ARE URIs, NOT BLOBS. `training_assets.storage_uri` points at
disk or object storage. A three-language tutorial set is plausibly 200-600 MB;
in a `LargeBinary` column that lands in every nightly `pg_dump` forever, cannot
serve HTTP Range requests (so the viewer cannot seek, and a training video you
cannot scrub is one nobody re-watches), and competes for the same shared
buffers as the OLTP workload.
"""
from alembic import op
import sqlalchemy as sa

revision = 'e7f2a4c916b8'
down_revision = 'd5b8c3f92a41'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. the shift ────────────────────────────────────────────────────────
    op.add_column('sme_execution_entry', sa.Column('Shift', sa.Text(),
                                                   nullable=True))
    # The day-shift probe filters on (Site_ID, Work_Date, Shift); without this
    # it is a sequential scan of every execution entry ever filed, every morning.
    op.create_index("ix_exec_entry_shift", "sme_execution_entry",
                    ["Site_ID", "Work_Date", "Shift"], unique=False)

    # ── 2. the daily claim ──────────────────────────────────────────────────
    op.create_table(
        'daily_job_runs',
        sa.Column('job_key', sa.Text(), primary_key=True),
        sa.Column('last_run', sa.DateTime(), nullable=True),
        sa.Column('last_worker', sa.Text(), nullable=True),
        sa.Column('last_result', sa.Text(), nullable=True),
    )

    # ── 3. the training registry ────────────────────────────────────────────
    op.create_table(
        'training_modules',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('module_key', sa.Text(), nullable=False, unique=True),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('description', sa.Text()),
        # Bumping this invalidates every acknowledgement of the old version.
        sa.Column('version', sa.Integer(), nullable=False,
                  server_default=sa.text('1')),
        sa.Column('required_roles', sa.Text()),      # CSV
        # Which feature this module gates, or NULL for informational modules.
        sa.Column('gates_feature', sa.Text()),
        sa.Column('active', sa.Integer(), nullable=False,
                  server_default=sa.text('1')),
        sa.Column('created_by', sa.Text()),
        sa.Column('created_at', sa.DateTime(),
                  server_default=sa.text('CURRENT_TIMESTAMP')),
    )
    op.create_table(
        'training_assets',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('module_id', sa.Integer(), nullable=False),
        # ta · ar · en · ta-Latn (Tanglish — Tamil written in Latin script,
        # which is what people actually speak on site and what the avatar
        # videos are recorded in). BCP-47, so a future language is a value
        # rather than a migration.
        sa.Column('language', sa.Text(), nullable=False),
        sa.Column('storage_uri', sa.Text(), nullable=False),
        sa.Column('captions_uri', sa.Text()),
        sa.Column('duration_s', sa.Integer()),
        sa.Column('created_at', sa.DateTime(),
                  server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.UniqueConstraint('module_id', 'language',
                            name='uq_training_asset_lang'),
    )
    op.create_table(
        'training_compliance',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('username', sa.Text(), nullable=False),
        sa.Column('module_id', sa.Integer(), nullable=False),
        # ⚠️ IN THE UNIQUE KEY. See the module docstring.
        sa.Column('module_version', sa.Integer(), nullable=False),
        sa.Column('language', sa.Text()),
        sa.Column('watched_seconds', sa.Integer(), nullable=False,
                  server_default=sa.text('0')),
        sa.Column('completed_at', sa.DateTime()),
        sa.Column('acknowledged_at', sa.DateTime()),
        # The soft gate's record. A "Watch Later" is not a failure to comply,
        # it is a DEFERRAL — and one an HOD can see and count.
        sa.Column('deferred_at', sa.DateTime()),
        sa.Column('deferrals', sa.Integer(), nullable=False,
                  server_default=sa.text('0')),
        sa.Column('updated_at', sa.DateTime(),
                  server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.UniqueConstraint('username', 'module_id', 'module_version',
                            name='uq_training_compliance_user_module_version'),
    )
    op.create_index("ix_training_compliance_user", "training_compliance",
                    ["username"], unique=False)

    # ⚠️ ADDED 2026-09-10 (Phase 13c). This call was MISSING, so the module row
    # below existed only on databases built by `cutover_migrate.py` — which
    # replays every migration's data step — and on no box that reached this
    # revision through an ordinary `alembic upgrade`. This machine's own mirror
    # was one of those, discovered while seeding the Phase 12 modules beside it.
    #
    # It hid because the gate is SOFT: `training.gate()` finds no module,
    # reports no interstitial, and the upload proceeds. A missing gate and a
    # working gate look identical from outside.
    #
    # `verify_data_migration_contract()` could not see it either — it greps
    # `upgrade()` for DML, and until this line there was none here to find.
    # ⚠️ Adding the call does NOT repair a box that has already run this
    # revision; alembic never re-runs one. Migration `d4f61b8e35ca` re-seeds
    # the row idempotently for exactly that reason.
    data_upgrade(op.get_bind())


def data_upgrade(conn) -> None:
    """Seed the one module slice 10b ships with.

    ⚠️ SEEDED RATHER THAN LEFT TO AN ADMIN SCREEN, because the OCR gate reads
    `gates_feature = 'ocr_upload'` and a missing row means an ungated upload —
    which is the correct failure direction, but it also means the feature would
    silently do nothing until somebody created the row by hand.

    No `training_assets` row is seeded: the videos are produced externally and
    an asset row pointing at a URI that does not exist yet would render a broken
    player. The page says "not published yet" until an admin adds one, which is
    the truthful state.

    Idempotent — the `module_key` is unique and the insert is a no-op on
    conflict, so `cutover_migrate`'s replay does not duplicate it.
    """
    conn.execute(sa.text(
        "INSERT INTO training_modules "
        "  (module_key, title, description, version, required_roles, "
        "   gates_feature, active, created_by) "
        "VALUES "
        "  ('ocr_workflow_v1', "
        "   'OCR Workflow & Paper Form Alignment', "
        "   'How the printed consumption form is filled, photographed and "
        "read — and what to do when a number comes back blank.', "
        "   1, 'supervisor,store_keeper', 'ocr_upload', 1, 'slice-10b') "
        "ON CONFLICT (module_key) DO NOTHING"))


def downgrade() -> None:
    op.drop_index("ix_training_compliance_user", table_name="training_compliance")
    op.drop_table('training_compliance')
    op.drop_table('training_assets')
    op.drop_table('training_modules')
    op.drop_table('daily_job_runs')
    op.drop_index("ix_exec_entry_shift", table_name="sme_execution_entry")
    op.drop_column('sme_execution_entry', 'Shift')
