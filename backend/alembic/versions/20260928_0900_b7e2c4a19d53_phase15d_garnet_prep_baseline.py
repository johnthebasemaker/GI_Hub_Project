"""phase15d — Garnet surface-prep baseline (Old vs New surface)

Revision ID: b7e2c4a19d53
Revises: a3d5e7f91c24
Create Date: 2026-09-28 09:00:00

`sme_prep_baseline` holds the Garnet benchmark in KG per m², for an OLD or a
NEW surface, per prep code: ESC1 = blasting concrete, ESC2 = blasting steel /
vessel (rulings Q15-5, Q15-9). A code with a row here is SURFACE PREP, never a
lining system (services/prep.py). The four rows are seeded EMPTY: until the HOD
saves a figure, New falls back to the workbook's For_1_SQM and Old has no
benchmark.

`Surface_State` (OLD | NEW) is asked per job (ruling Q15-7) and kept on the job
and on each member row, beside the benchmark that was snapshotted from it.
`models.SmePrepBaseline` and the two new columns carry the same shape (rule 15).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'b7e2c4a19d53'
down_revision = 'a3d5e7f91c24'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'sme_prep_baseline',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('Prep_Code', sa.Text(), nullable=False),
        sa.Column('Surface_State', sa.Text(), nullable=False),
        sa.Column('Material_Group', sa.Text(), nullable=False,
                  server_default=sa.text("'GARNET'")),
        sa.Column('Substrate_Class', sa.Text(), nullable=False),
        sa.Column('KG_Per_SQM', sa.Float()),
        sa.Column('updated_by', sa.Text()),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.UniqueConstraint('Prep_Code', 'Surface_State', 'Material_Group',
                            name='uq_sme_prep_baseline'),
    )
    op.add_column('sme_attribution_group', sa.Column('Surface_State', sa.Text()))
    op.add_column('sme_consumption_log', sa.Column('Surface_State', sa.Text()))
    data_upgrade(op.get_bind())


def data_upgrade(conn) -> None:
    """The four (prep code, surface state) rows, EMPTY — the HOD sets the
    figures on the Garnet baseline page. Seeded rather than left to the page
    because the rows are what MAKE ESC1/ESC2 surface prep: without them the
    Garnet recipe lines would turn blasting into a lining system.

    Idempotent (unique key + ON CONFLICT DO NOTHING), so a cutover's replay of
    this step over a models-built schema does not duplicate them.
    """
    conn.execute(sa.text(
        'INSERT INTO sme_prep_baseline ("Prep_Code", "Surface_State", "Material_Group", '
        '"Substrate_Class") VALUES '
        "('ESC1', 'OLD', 'GARNET', 'CONCRETE'), ('ESC1', 'NEW', 'GARNET', 'CONCRETE'), "
        "('ESC2', 'OLD', 'GARNET', 'STEEL_VESSEL'), ('ESC2', 'NEW', 'GARNET', 'STEEL_VESSEL') "
        'ON CONFLICT ("Prep_Code", "Surface_State", "Material_Group") DO NOTHING'))


def downgrade() -> None:
    op.drop_column('sme_consumption_log', 'Surface_State')
    op.drop_column('sme_attribution_group', 'Surface_State')
    op.drop_table('sme_prep_baseline')
