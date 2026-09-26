"""Phase 14b — QR ⇄ Excel reconciliation buckets and execution-entry links

Revision ID: d7a3f05c1e92
Revises: c41d7e9a2b58
Create Date: 2026-09-26 15:00:00

A QR execution entry and the Excel Consumption Log can both speak for the same
drums. Before Phase 14b the sync merged them only when day, SAP and the Tank
No. text all matched and the quantities lined up — a spelling difference, a
split line, or the Excel-first order deducted the drum twice
(PROPOSED_PHASE14_PLAN.md §1.4).

  consumption_reconciliation   one row per (site, day, tag, SAP) bucket: how
                               the two sources compared (L1: the ledger holds
                               max(QR, Excel), never the sum)
  consumption_exec_link        ledger rows an entry speaks for without having
                               posted them — excluded from the attribution
                               sweep (L3)

Pure DDL (rule 15); the matching `models.py` classes carry the same unique
constraint and indexes.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'd7a3f05c1e92'
down_revision = 'c41d7e9a2b58'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'consumption_reconciliation',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('Site_ID', sa.Text(), nullable=False),
        sa.Column('Work_Date', sa.Text(), nullable=False),
        sa.Column('Equipment_Tag_No', sa.Text(), nullable=False),
        sa.Column('SAP_Code', sa.Text(), nullable=False),
        sa.Column('QR_Qty', sa.Float(), nullable=False, server_default=sa.text('0')),
        sa.Column('Excel_Qty', sa.Float(), nullable=False, server_default=sa.text('0')),
        sa.Column('Ledger_Qty', sa.Float(), nullable=False, server_default=sa.text('0')),
        sa.Column('status', sa.Text(), nullable=False),
        sa.Column('entry_ids', sa.Text()),
        sa.Column('detail', sa.Text()),
        sa.Column('notified_status', sa.Text()),
        sa.Column('acknowledged_by', sa.Text()),
        sa.Column('acknowledged_at', sa.DateTime()),
        sa.Column('acknowledge_note', sa.Text()),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.UniqueConstraint('Site_ID', 'Work_Date', 'Equipment_Tag_No', 'SAP_Code',
                            name='uq_consumption_recon_bucket'),
    )
    op.create_index('ix_consumption_recon_status', 'consumption_reconciliation',
                    ['Site_ID', 'status'])
    op.create_table(
        'consumption_exec_link',
        sa.Column('Consumption_ID', sa.Integer(), primary_key=True),
        sa.Column('Entry_ID', sa.Integer(), nullable=False),
        sa.Column('Line_ID', sa.Integer()),
        sa.Column('via', sa.Text(), nullable=False),
        sa.Column('linked_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
    )
    op.create_index('ix_consumption_exec_link_entry', 'consumption_exec_link', ['Entry_ID'])


def downgrade() -> None:
    op.drop_index('ix_consumption_exec_link_entry', 'consumption_exec_link')
    op.drop_table('consumption_exec_link')
    op.drop_index('ix_consumption_recon_status', 'consumption_reconciliation')
    op.drop_table('consumption_reconciliation')
