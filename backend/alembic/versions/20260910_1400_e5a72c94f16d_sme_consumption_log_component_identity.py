"""sme_consumption_log: component identity, the ledger link, and the HOD decision

Revision ID: e5a72c94f16d
Revises: d4f61b8e35ca
Create Date: 2026-09-10 14:00:00

Phase 13e/13f/13g. `sme_consumption_log` stops being a workbook side-note and
becomes the attribution record for LIVE Surface Shield consumption.

⚠️ `SAP_Code` IS RULE 1, AND IT IS THE COLUMN THAT MATTERED MOST.

The component key is `(Material_Code, SAP_Code)` and never `Material_Code`
alone. This table carried only the material code, which was survivable while
its rows came from a workbook that named nothing finer — and stops being
survivable the moment those rows attribute ERP `consumption` rows, which are
keyed on SAP.

Pooling by material code alone was measured to *invert* a shortfall across the
four Cumicrete PU components (`GI-8005765` at SAPs 1041 / 1041-1 / -2 / -3):
components A and B reported fully covered while both were 10 short, and D
reported 10 short while holding three times what it needed. `Material_Name +
UOM` is not a discriminator either — all four PU rows share a name, and the UOM
disagrees on 25 of 32 pairs.

Backfilled to `''`, not guessed. A workbook row states a material and a Tank
No. and no SAP; inventing one would attribute a quantity to a component that
may never have been drawn. `''` is the same sentinel `sme_recipe` and
`sme_inventory_seed` already use for "not classified", and Postgres treats
NULLs as distinct, so a NULL in a key stops the key constraining.

⚠️ `Consumption_ID` IS WHAT MAKES THE SWEEP A QUERY RATHER THAN A TRIGGER.

Ruling Q13-6: the queue must catch historical rows, rows landed by a bulk
Excel/Postgres sync, and rows from an ordinary store-keeper issue — sorted
oldest first. So it is `consumption` LEFT JOINed to this column, filtered to
the misses. A trigger would fire only on rows created after it existed, which
is exactly the set the operator asked the feature to reach BEYOND: 1,674 live
consumption rows already carry no attribution at all.

NULL on every workbook-sourced row, which is the honest reading — those state a
Tank No. and a quantity and are not traceable to one ledger line.

⚠️ `Bench_For_1_SQM` IS SNAPSHOTTED AND NEVER RE-JOINED.

`sme_recipe.For_1_SQM` is editable by an HOD. A variance report that re-derived
its benchmark would silently rewrite history the first time somebody corrected
a rate — last quarter's 12 % overrun becoming 4 % with no edit to the row and
nothing to point at. Same reasoning, and the same shape, as
`sme_execution_entry.Bench_*`.

⚠️ `Priority_Flag` IS PRESENTATION, AND STORING IT IS THE POINT.

Ruling Q13-8: EVERY Surface Shield consumption goes to the HOD regardless of
variance. The ±10 % tolerance does not gate anything — it decides whether a row
is rendered as **High Priority** at the top of the queue. Computed and STORED
at submission, beside the tolerance it was measured against, so that moving the
tolerance re-sorts the queue and reopens nothing already decided. Computed at
read time, a row approved at 12 % would silently change character the day
somebody tuned the number.

────────────────────────────────────────────────────────────────────────────
DDL ONLY. No `data_upgrade()` is required and the cutover contract verifier
agrees: the two NOT-NULL columns take their existing-row values from a server
default, which PostgreSQL applies without a table rewrite and without an UPDATE
statement anybody has to replay.

Every column and index is declared in `backend/models.py` in the SAME commit —
`tools/migration/cutover_migrate.py` builds production with
`metadata.create_all`, so anything living only here is absent from every
production box (rule 15's second half; `ux_asset_transfer_open` is the scar).
"""
from alembic import op
import sqlalchemy as sa

revision = 'e5a72c94f16d'
down_revision = 'd4f61b8e35ca'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── rule 1: the component key ───────────────────────────────────────────
    op.add_column('sme_consumption_log',
                  sa.Column('SAP_Code', sa.Text(), nullable=False,
                            server_default=sa.text("''")))

    # ── the ledger row this attributes (13e) ────────────────────────────────
    op.add_column('sme_consumption_log',
                  sa.Column('Consumption_ID', sa.Integer(), nullable=True))
    # The sweep asks this of every candidate row: is it already attributed?
    # Without the index that is a sequential scan of the whole log per page of
    # the queue (rule 11 — the access path is named before the index is added).
    op.create_index("ix_sme_cons_log_consumption", "sme_consumption_log",
                    ["Consumption_ID"], unique=False)
    op.create_index("ix_sme_cons_log_site_status", "sme_consumption_log",
                    ["Site_ID", "status"], unique=False)

    # ── the snapshotted benchmark and the stored priority (13f) ─────────────
    op.add_column('sme_consumption_log',
                  sa.Column('Bench_For_1_SQM', sa.Float(), nullable=True))
    op.add_column('sme_consumption_log',
                  sa.Column('Priority_Flag', sa.Text(), nullable=True))
    op.add_column('sme_consumption_log',
                  sa.Column('Variance_Tolerance_Pct', sa.Float(), nullable=True))

    # ── the HOD decision (13g) ──────────────────────────────────────────────
    op.add_column('sme_consumption_log',
                  sa.Column('hod_username', sa.Text(), nullable=True))
    op.add_column('sme_consumption_log',
                  sa.Column('hod_decided_at', sa.DateTime(), nullable=True))
    op.add_column('sme_consumption_log',
                  sa.Column('HOD_Edit_Justification', sa.Text(), nullable=True))
    op.add_column('sme_consumption_log',
                  sa.Column('hod_edited', sa.Boolean(), nullable=False,
                            server_default=sa.text('false')))
    op.add_column('sme_consumption_log',
                  sa.Column('Original_SQM_Completed', sa.Float(), nullable=True))


def downgrade() -> None:
    for col in ('Original_SQM_Completed', 'hod_edited', 'HOD_Edit_Justification',
                'hod_decided_at', 'hod_username', 'Variance_Tolerance_Pct',
                'Priority_Flag', 'Bench_For_1_SQM'):
        op.drop_column('sme_consumption_log', col)
    op.drop_index("ix_sme_cons_log_site_status", table_name="sme_consumption_log")
    op.drop_index("ix_sme_cons_log_consumption", table_name="sme_consumption_log")
    op.drop_column('sme_consumption_log', 'Consumption_ID')
    op.drop_column('sme_consumption_log', 'SAP_Code')
