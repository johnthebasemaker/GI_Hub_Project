"""phase22d — requests without (or with) a PR, from the Pending Material Follow-up folder

Revision ID: c6e9a3b7d4f8
Revises: b5d8f2a6c3e7
Create Date: 2026-10-08 15:00:00

Phase 22d, rulings Q22-12/13.

  material_requests        one per request workbook in Drive (drive_file_id
                           unique); `is_summary` marks the roll-up, which is a
                           CHECK, never a source (Q22-12)
  material_request_lines   one per workbook row: SAP (resolved from the SAP
                           column or the Material Code), requested qty, the
                           workbook's own received / pending, PR or "Without
                           PR", type, sheet + row. GI Hub's received figure is
                           computed from the Receipt Log at read time (FIFO).

Re-written from the workbook on every pull (a changed file replaces its lines).
Additive; `models.py` carries the same (rule 15).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'c6e9a3b7d4f8'
down_revision = 'b5d8f2a6c3e7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'material_requests',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('drive_file_id', sa.Integer(), nullable=False),
        sa.Column('file_name', sa.Text(), nullable=False),
        sa.Column('request_date', sa.Date()),
        sa.Column('layout', sa.Text()),
        sa.Column('is_summary', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('Site_ID', sa.Text()),
        sa.Column('synced_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.UniqueConstraint('drive_file_id', name='ux_material_requests_file'),
    )
    op.create_table(
        'material_request_lines',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('request_id', sa.Integer(), nullable=False),
        sa.Column('sheet', sa.Text()),
        sa.Column('row_no', sa.Integer()),
        sa.Column('SAP_Code', sa.Text()),
        sa.Column('Material_Code', sa.Text()),
        sa.Column('description', sa.Text()),
        sa.Column('uom', sa.Text()),
        sa.Column('requested_qty', sa.Float(), nullable=False),
        sa.Column('wb_received', sa.Float()),
        sa.Column('wb_pending', sa.Float()),
        sa.Column('pr_ref', sa.Text()),
        sa.Column('without_pr', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('item_type', sa.Text()),
        sa.Column('remarks', sa.Text()),
        sa.Column('request_date', sa.Date()),
    )
    op.create_index('ix_material_request_lines_req', 'material_request_lines', ['request_id'])
    op.create_index('ix_material_request_lines_sap', 'material_request_lines', ['SAP_Code'])


def downgrade() -> None:
    op.drop_index('ix_material_request_lines_sap', table_name='material_request_lines')
    op.drop_index('ix_material_request_lines_req', table_name='material_request_lines')
    op.drop_table('material_request_lines')
    op.drop_table('material_requests')
