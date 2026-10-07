"""phase22b — DN links: the Return Log's DN number, WD numbers

Revision ID: a4c7e1d9b3f2
Revises: f3b9d2e7a4c1
Create Date: 2026-10-08 09:00:00

Phase 22b, rulings Q22-7..9.

  returns."DN_No"   the Return Log's `DN. No.`, imported at last (Q22-9), so a
                    return opens its return DN (RDN# 024 …) from Drive
  receipt_wd        "WD" = Without Delivery Note (Q22-8): every such delivery
                    gets an automatic unique number WD-<site>-0001, allocated
                    once and never renumbered; the lines of one delivery share
                    it (group_key = site | day | DN text | vehicle)

Additive; `models.py` carries the same (rule 15). The returns column is
backfilled from the workbook by `tools/backfill_return_dn.py` (the rows at the
last sync), so the next pull does not see 20 "edits".
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'a4c7e1d9b3f2'
down_revision = 'f3b9d2e7a4c1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('returns', sa.Column('DN_No', sa.Text()))
    op.create_table(
        'receipt_wd',
        sa.Column('receipt_id', sa.Integer(), primary_key=True),
        sa.Column('Site_ID', sa.Text(), nullable=False),
        sa.Column('wd_no', sa.Text(), nullable=False),
        sa.Column('group_key', sa.Text(), nullable=False),
        sa.Column('assigned_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
    )
    op.create_index('ix_receipt_wd_no', 'receipt_wd', ['wd_no'])


def downgrade() -> None:
    op.drop_index('ix_receipt_wd_no', table_name='receipt_wd')
    op.drop_table('receipt_wd')
    op.drop_column('returns', 'DN_No')
