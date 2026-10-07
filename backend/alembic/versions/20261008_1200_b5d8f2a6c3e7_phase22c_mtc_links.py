"""phase22c — Drive certificates on lots: provenance and the QC queue

Revision ID: b5d8f2a6c3e7
Revises: a4c7e1d9b3f2
Create Date: 2026-10-08 12:00:00

Phase 22c, rulings Q22-10/11.

  mtc_documents.drive_file_id   the Drive file a certificate came from (NULL
                                for one uploaded in the app). An EXACT match —
                                batch and product agree — is filed here, which
                                clears the issue gate like any site upload.
  mtc_assignments               anything less than exact: a batch with no
                                product to check it against, or a file someone
                                assigns by hand (AR bricks by container,
                                CHEMOLINE by order — Q22-10). `proposed` until
                                QC confirms (→ a certificate) or rejects it.

Additive; `models.py` carries the same (rule 15).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'b5d8f2a6c3e7'
down_revision = 'a4c7e1d9b3f2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('mtc_documents', sa.Column('drive_file_id', sa.Integer()))
    op.create_index('ix_mtc_documents_drive_file', 'mtc_documents', ['drive_file_id'])
    op.create_table(
        'mtc_assignments',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('drive_file_id', sa.Integer(), nullable=False),
        sa.Column('SAP_Code', sa.Text(), nullable=False),
        sa.Column('Lot_Number', sa.Text(), nullable=False),
        sa.Column('Site_ID', sa.Text()),
        sa.Column('source', sa.Text(), nullable=False),        # suggested | manual
        sa.Column('status', sa.Text(), nullable=False, server_default=sa.text("'proposed'")),
        sa.Column('batch_text', sa.Text()),
        sa.Column('note', sa.Text()),
        sa.Column('proposed_by', sa.Text()),
        sa.Column('proposed_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('decided_by', sa.Text()),
        sa.Column('decided_at', sa.DateTime()),
    )
    op.create_index('ix_mtc_assignments_status', 'mtc_assignments', ['status'])


def downgrade() -> None:
    op.drop_index('ix_mtc_assignments_status', table_name='mtc_assignments')
    op.drop_table('mtc_assignments')
    op.drop_index('ix_mtc_documents_drive_file', table_name='mtc_documents')
    op.drop_column('mtc_documents', 'drive_file_id')
