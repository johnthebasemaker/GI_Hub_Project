"""phase16 — FEFO lot management: lots describe themselves; returns carry a lot

Revision ID: d8a3f6c1b2e9
Revises: c4f1a8d2e6b7
Create Date: 2026-10-01 09:00:00

Phase 16 (PROPOSED_PHASE16_PLAN.md, rulings Q16-1…Q16-16 approved 2026-10-01).

  · `lots` gains what the Lot Register workbook knows about a lot — its MFD, its
    batch reference (`Order No.`), the DN it arrived on — and WHERE each date
    came from (`Expiry_Source` file / derived / app; `Source` receipt / lotfile /
    app). The lot file only ever DESCRIBES a lot; stock still comes from the
    ledger alone.
  · `returns` gains `Lot_Number` (a returned can gives back to its lot) and
    `Serial_No` (an equipment return keeps its asset tag — the Return Log's
    `Serial No.` used to be dropped).
  · `inventory` gains `Lot_Tracked` (NULL = automatic: a Surface Shield is
    lot-tracked) and `Shelf_Life_Months` (derives a missing expiry from the MFD,
    ruling Q16-5).
  · `lot_units` — the CHEMOLINE roll register: a roll is a UNIT inside its batch
    (ruling Q16-3), keyed (SAP_Code, Unit_No).

`models.py` carries the same shape (rule 15).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'd8a3f6c1b2e9'
down_revision = 'c4f1a8d2e6b7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    for col in (sa.Column('MFD_Date', sa.Text()),
                sa.Column('Expiry_Source', sa.Text()),
                sa.Column('Batch_Ref', sa.Text()),
                sa.Column('DN_No', sa.Text()),
                sa.Column('Source', sa.Text()),
                sa.Column('updated_at', sa.DateTime())):
        op.add_column('lots', col)
    op.add_column('returns', sa.Column('Lot_Number', sa.Text()))
    op.add_column('returns', sa.Column('Serial_No', sa.Text()))
    op.add_column('inventory', sa.Column('Lot_Tracked', sa.Boolean()))
    op.add_column('inventory', sa.Column('Shelf_Life_Months', sa.Integer()))
    op.create_table(
        'lot_units',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('Unit_No', sa.Text(), nullable=False),
        sa.Column('Lot_Number', sa.Text(), nullable=False),
        sa.Column('SAP_Code', sa.Text(), nullable=False),
        sa.Column('Site_ID', sa.Text(), nullable=False),
        sa.Column('Received_Date', sa.Text()),
        sa.Column('SQM', sa.Text()),
        sa.Column('Pallet', sa.Text()),
        sa.Column('Location', sa.Text()),
        sa.Column('Source', sa.Text()),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.UniqueConstraint('SAP_Code', 'Unit_No', name='uq_lot_units_sap_unit'),
    )
    op.create_index('ix_lot_units_lot', 'lot_units', ['SAP_Code', 'Lot_Number'])
    op.create_index('ix_returns_lot', 'returns', ['SAP_Code', 'Lot_Number'])
    data_upgrade(op.get_bind())


def data_upgrade(conn) -> None:
    """Bricks are NOT lot-tracked (ruling Q16-4): they carry no batch and never
    expire, so FEFO has nothing to order. Marked explicitly so the automatic
    rule (every Surface Shield is lot-tracked) does not catch them.

    Idempotent: touches only rows still on the automatic setting (NULL), so an
    admin's later choice is never overwritten by a cutover replay.
    """
    conn.execute(sa.text(
        'UPDATE inventory SET "Lot_Tracked" = FALSE '
        'WHERE "Lot_Tracked" IS NULL AND "Category" = \'Surface Shields\' '
        'AND UPPER(COALESCE("Equipment_Description", \'\')) LIKE \'%BRICK%\''))


def downgrade() -> None:
    op.drop_index('ix_returns_lot', table_name='returns')
    op.drop_index('ix_lot_units_lot', table_name='lot_units')
    op.drop_table('lot_units')
    op.drop_column('inventory', 'Shelf_Life_Months')
    op.drop_column('inventory', 'Lot_Tracked')
    op.drop_column('returns', 'Serial_No')
    op.drop_column('returns', 'Lot_Number')
    for c in ('updated_at', 'Source', 'DN_No', 'Batch_Ref', 'Expiry_Source', 'MFD_Date'):
        op.drop_column('lots', c)
