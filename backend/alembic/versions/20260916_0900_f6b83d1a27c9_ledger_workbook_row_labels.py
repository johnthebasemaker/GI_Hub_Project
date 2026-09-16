"""ledger workbook-row labels, attribution fingerprints, and revisions

Revision ID: f6b83d1a27c9
Revises: e5a72c94f16d
Create Date: 2026-09-16 09:00:00

The Excel sync becomes a true upsert without ever putting a uniqueness rule on
a MOVEMENT.

────────────────────────────────────────────────────────────────────────────
⚠️ WHY NOT A UNIQUE KEY ON (Date, SAP, Tank No.) — MEASURED, NOT ASSUMED

The natural key that looks right is wrong on this ledger. Against the real
CNCEC Consumption Log (4,394 rows):

    (Date, SAP, Tank No.)       605 keys shared by 2,785 rows
    (Date, SAP, Tank No., Qty)  529 keys shared by 2,402 rows
    Surface Shield rows only    36 keys shared by 169 of 555 rows

Two drums of one material issued to one tank on one day are two real
movements. A unique constraint on those columns would have merged ~2,180 of
them into their neighbours and understated consumption by every one. The
existing module docstring already forbade exactly that; this migration keeps
the prohibition and gets the upsert another way.

────────────────────────────────────────────────────────────────────────────
WHAT IS INDEXED INSTEAD: A PER-ROW PROVENANCE LABEL

    Source_Ref = 'XLSX:<site>:<kind>:<date>:<sap>:<ref-hash>:<n>'

`<n>` is allocated once, when the sync first owns a row, and never renumbered.
Two genuine same-day movements carry `:1` and `:2`, so both survive. The
partial unique index `(Site_ID, Source_Ref) WHERE Source_Ref LIKE 'XLSX:%'`
lets the sync write with `INSERT … ON CONFLICT DO UPDATE`:

  * re-running a workbook converges to zero writes;
  * an edited quantity updates the SAME row in place — its `id` survives, and
    so does every Phase 13 attribution keyed on `Consumption_ID`;
  * nothing the app writes is covered: NULL, `SME_EXEC:`, `SMR:` all fall
    outside the predicate.

`receipts` and `returns` gain the column; `consumption` already had it.

────────────────────────────────────────────────────────────────────────────
`sme_consumption_log.Source_Fingerprint` and `sme_consumption_revision`

An attribution records what the ledger row looked like when it was filed. When
the sync edits that row, the fingerprints diverge and the row returns to the
Phase 13 queue marked EDITED. If the HOD had already approved it, the field's
new answer waits in `sme_consumption_revision` while the approved figures keep
counting; approval then updates the original attribution in place.

────────────────────────────────────────────────────────────────────────────
DDL ONLY — no `data_upgrade()` is needed. Existing ledger rows keep a NULL
label and are ADOPTED by the next sync run (the planner stamps them by id, so
nothing is re-inserted). Existing attributions keep a NULL fingerprint and are
read by falling back to `Actual_Qty`. Every object here is declared in
`backend/models.py` in the same commit (rule 15's second half).
"""
from alembic import op
import sqlalchemy as sa

revision = 'f6b83d1a27c9'
down_revision = 'e5a72c94f16d'
branch_labels = None
depends_on = None

_XLSX = sa.text("\"Source_Ref\" LIKE 'XLSX:%'")


def upgrade() -> None:
    op.add_column('receipts', sa.Column('Source_Ref', sa.Text(), nullable=True))
    op.add_column('returns', sa.Column('Source_Ref', sa.Text(), nullable=True))
    for table in ('receipts', 'consumption', 'returns'):
        op.create_index(f"ux_{table}_xlsx_ref", table, ["Site_ID", "Source_Ref"],
                        unique=True, postgresql_where=_XLSX)

    op.add_column('sme_consumption_log',
                  sa.Column('Source_Fingerprint', sa.Text(), nullable=True))

    op.create_table(
        'sme_consumption_revision',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('Log_ID', sa.Integer(),
                  sa.ForeignKey('sme_consumption_log.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('Consumption_ID', sa.Integer(), nullable=False),
        sa.Column('Site_ID', sa.Text(), nullable=False),
        sa.Column('status', sa.Text(), nullable=False,
                  server_default=sa.text("'staged'")),
        sa.Column('Prev_Lining_System_Code', sa.Text()),
        sa.Column('Prev_Equipment_Tag_No', sa.Text()),
        sa.Column('Prev_SQM_Completed', sa.Float()),
        sa.Column('Prev_Actual_Qty', sa.Float()),
        sa.Column('Prev_Variance_Pct', sa.Float()),
        sa.Column('Prev_Source_Fingerprint', sa.Text()),
        sa.Column('Lining_System_Code', sa.Text(), nullable=False),
        sa.Column('Equipment_Tag_No', sa.Text(), nullable=False),
        sa.Column('SQM_Completed', sa.Float(), nullable=False),
        sa.Column('Actual_Qty', sa.Float(), nullable=False),
        sa.Column('Expected_Qty', sa.Float()),
        sa.Column('Variance_Pct', sa.Float()),
        sa.Column('Bench_For_1_SQM', sa.Float()),
        sa.Column('Priority_Flag', sa.Text()),
        sa.Column('Variance_Tolerance_Pct', sa.Float()),
        sa.Column('Source_Fingerprint', sa.Text(), nullable=False),
        sa.Column('notes', sa.Text()),
        sa.Column('submitted_by', sa.Text()),
        sa.Column('submitted_at', sa.DateTime(),
                  server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('hod_username', sa.Text()),
        sa.Column('hod_decided_at', sa.DateTime()),
        sa.Column('HOD_Edit_Justification', sa.Text()),
        sa.Column('hod_edited', sa.Boolean(), nullable=False,
                  server_default=sa.text('false')),
        sa.Column('rejected_reason', sa.Text()),
    )
    op.create_index("ix_sme_cons_rev_log", "sme_consumption_revision",
                    ["Log_ID", "status"], unique=False)
    op.create_index("ux_sme_cons_rev_staged", "sme_consumption_revision",
                    ["Log_ID"], unique=True,
                    postgresql_where=sa.text("status = 'staged'"))


def downgrade() -> None:
    op.drop_index("ux_sme_cons_rev_staged", table_name="sme_consumption_revision")
    op.drop_index("ix_sme_cons_rev_log", table_name="sme_consumption_revision")
    op.drop_table('sme_consumption_revision')
    op.drop_column('sme_consumption_log', 'Source_Fingerprint')
    for table in ('receipts', 'consumption', 'returns'):
        op.drop_index(f"ux_{table}_xlsx_ref", table_name=table)
    op.drop_column('returns', 'Source_Ref')
    op.drop_column('receipts', 'Source_Ref')
