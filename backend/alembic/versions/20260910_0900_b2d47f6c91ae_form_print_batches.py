"""sme_consumption_form print batches — Batch_UUID / Batch_Seq / Batch_Size

Revision ID: b2d47f6c91ae
Revises: a1c9e64b3d70
Create Date: 2026-09-10 09:00:00

Phase 13a. The Execution Entry page can now print N blank consumption forms in
one download, and this is what makes that a print RUN rather than N unrelated
rows in the registry.

⚠️ WHY THIS FEATURE EXISTS AT ALL, because the columns look like bookkeeping
and are not. Until now a supervisor who needed fifty sheets downloaded one and
PHOTOCOPIED it. Every copy then carried the SAME QR — the same `Form_UUID` —
and slice 9d maps handwriting to materials POSITIONALLY off exactly that
identity. Fifty identical QRs means the intake cannot tell a re-print from a
re-photograph, cannot refuse a sheet already filed, and cannot say which of
fifty tanks a page of quantities belongs to. Bulk printing mints fifty
identities instead of duplicating one, which is the whole point.

⚠️ AND A BATCH IS NOT A FORM. Fifty forms are still fifty `sme_consumption_form`
rows with fifty `Form_UUID`s. `Batch_UUID` groups them so the operator's real
question — "where did the fifty sheets I printed on Tuesday go?" — has an
answer, and `Batch_Seq` is printed on the paper as "SHEET 7 OF 50" so a human
can sort a pile and see which one is missing.

────────────────────────────────────────────────────────────────────────────
⚠️ `Batch_UUID` IS NULLABLE AND STAYS NULL ON HISTORICAL ROWS.

Those forms were printed one at a time; there is no print run they belonged to.
Back-filling a synthetic batch id would let a report claim a run that never
happened — the same class of error as stamping every pre-migration receipt with
migration day (`receipts.posted_at`, alembic c7a93e5d2b18, deliberately left
NULL for exactly this reason).

`Batch_Seq` and `Batch_Size` DO back-fill, to 1, because "a batch of one" is a
true statement about a single download rather than an invented one.

────────────────────────────────────────────────────────────────────────────
DDL ONLY — no `data_upgrade()` (rule 15's second half).

`verify_data_migration_contract()` requires a data step from any migration whose
`upgrade()` carries DML. This one carries none: the two back-filled columns get
their values from a server default, which PostgreSQL applies to existing rows
without rewriting the table and without an UPDATE statement anybody has to
replay on a cutover-built database.

⚠️ The columns and the index are declared in `backend/models.py` in the SAME
commit. `tools/migration/cutover_migrate.py` builds production with
`metadata.create_all`, so anything living only in an Alembic revision is absent
from every production box — which is how `ux_asset_transfer_open` went missing.
"""
from alembic import op
import sqlalchemy as sa

revision = 'b2d47f6c91ae'
down_revision = 'a1c9e64b3d70'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('sme_consumption_form',
                  sa.Column('Batch_UUID', sa.Text(), nullable=True))
    # NOT NULL with a server default: PostgreSQL 11+ stores the default in the
    # catalogue instead of rewriting every row, so this is cheap even on a
    # registry with a year of prints in it.
    op.add_column('sme_consumption_form',
                  sa.Column('Batch_Seq', sa.Integer(), nullable=False,
                            server_default=sa.text('1')))
    op.add_column('sme_consumption_form',
                  sa.Column('Batch_Size', sa.Integer(), nullable=False,
                            server_default=sa.text('1')))
    # The registry's own question — show me that print run, in sheet order.
    # Rule 11: this is the only access path the batch adds, and without it
    # "which sheets of batch X are still open" is a scan of the whole table.
    op.create_index("ix_consumption_form_batch", "sme_consumption_form",
                    ["Batch_UUID", "Batch_Seq"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_consumption_form_batch",
                  table_name="sme_consumption_form")
    op.drop_column('sme_consumption_form', 'Batch_Size')
    op.drop_column('sme_consumption_form', 'Batch_Seq')
    op.drop_column('sme_consumption_form', 'Batch_UUID')
