"""Phase 14a — the pack → base factor (Unit Size) and the units an attribution used

Revision ID: c41d7e9a2b58
Revises: f6b83d1a27c9
Create Date: 2026-09-26 13:00:00

A Surface Shield SAP is COUNTED in packs (Can / Bag / ROL) and MEASURED in base
units (KG / M2 / EA). Nothing converted between the two, and three latent
defects followed from it (PROPOSED_PHASE14_PLAN.md §0): the Phase 13 variance
compared a can with a kilogram (D1), and the QR form's KG figure was posted
into a ledger kept in cans (D2).

The ledger is NOT converted — operator ruling "convert at READ, store packs".
This migration only adds where the factor and its provenance live:

  inventory.Unit_Size / Base_UOM   the factor, from the Inventory sheet
  sme_consumption_log /
  sme_consumption_revision
    .Pack_Qty / .Unit_Size_Used    what an attribution converted, snapshotted
  sme_execution_entry.Qty_Unit     which unit the paper's QTY column used

Pure DDL — no rows are written, so there is no `data_upgrade` (rule 15).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'c41d7e9a2b58'
down_revision = 'f6b83d1a27c9'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('inventory', sa.Column('Unit_Size', sa.Float(), nullable=True))
    op.add_column('inventory', sa.Column('Base_UOM', sa.Text(), nullable=True))
    op.add_column('sme_consumption_log', sa.Column('Pack_Qty', sa.Float(), nullable=True))
    op.add_column('sme_consumption_log',
                  sa.Column('Unit_Size_Used', sa.Float(), nullable=True))
    op.add_column('sme_execution_entry', sa.Column('Qty_Unit', sa.Text(), nullable=True))
    op.add_column('sme_consumption_revision', sa.Column('Pack_Qty', sa.Float(), nullable=True))
    op.add_column('sme_consumption_revision',
                  sa.Column('Unit_Size_Used', sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column('sme_consumption_revision', 'Unit_Size_Used')
    op.drop_column('sme_consumption_revision', 'Pack_Qty')
    op.drop_column('sme_execution_entry', 'Qty_Unit')
    op.drop_column('sme_consumption_log', 'Unit_Size_Used')
    op.drop_column('sme_consumption_log', 'Pack_Qty')
    op.drop_column('inventory', 'Base_UOM')
    op.drop_column('inventory', 'Unit_Size')
