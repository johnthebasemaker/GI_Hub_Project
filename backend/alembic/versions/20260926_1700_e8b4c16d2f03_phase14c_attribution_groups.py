"""Phase 14c — attribution GROUPS: one system code and one SQM per job

Revision ID: e8b4c16d2f03
Revises: d7a3f05c1e92
Create Date: 2026-09-26 17:00:00

The Phase 13 queue asked for a system code, an equipment tag and an SQM on
EVERY material, and credited each material's SQM to the vessel on approval —
so a four-component job of 13.37 m² credited 53.48 m² (defect D3). A group is
the job: (date, equipment) → one system code, one SQM, credited once.

Pure DDL; `models.SmeAttributionGroup` carries the same indexes (rule 15).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'e8b4c16d2f03'
down_revision = 'd7a3f05c1e92'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'sme_attribution_group',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('Site_ID', sa.Text(), nullable=False),
        sa.Column('Work_Date', sa.Text(), nullable=False),
        sa.Column('Equipment_Tag_No', sa.Text(), nullable=False),
        sa.Column('Lining_System_Code', sa.Text(), nullable=False),
        sa.Column('SQM_Completed', sa.Float(), nullable=False),
        sa.Column('status', sa.Text(), nullable=False, server_default=sa.text("'staged'")),
        sa.Column('notes', sa.Text()),
        sa.Column('submitted_by', sa.Text()),
        sa.Column('submitted_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('hod_username', sa.Text()),
        sa.Column('hod_decided_at', sa.DateTime()),
        sa.Column('rejected_reason', sa.Text()),
        sa.Column('Done_SQM_Credited', sa.Float()),
    )
    op.create_index('ix_sme_attr_group_site_status', 'sme_attribution_group',
                    ['Site_ID', 'status'])
    op.create_index('ix_sme_attr_group_tag', 'sme_attribution_group',
                    ['Site_ID', 'Equipment_Tag_No', 'Lining_System_Code'])
    op.add_column('sme_consumption_log', sa.Column('group_id', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('sme_consumption_log', 'group_id')
    op.drop_index('ix_sme_attr_group_tag', 'sme_attribution_group')
    op.drop_index('ix_sme_attr_group_site_status', 'sme_attribution_group')
    op.drop_table('sme_attribution_group')
