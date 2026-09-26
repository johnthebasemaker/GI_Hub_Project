"""stock_excel_checks — why GI Hub's stock differs from the Excel workbook

Revision ID: a3d5e7f91c24
Revises: f2c9a7d41b36
Create Date: 2026-09-26 21:00:00

One row per check run (services/stock_excel.py): the SAPs whose GI Hub stock
differs from the workbook's Current Stock, each with its causes. Pure DDL;
`models.StockExcelCheck` carries the same shape (rule 15).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'a3d5e7f91c24'
down_revision = 'f2c9a7d41b36'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'stock_excel_checks',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('Site_ID', sa.Text(), nullable=False),
        sa.Column('workbook', sa.Text()),
        sa.Column('checked_by', sa.Text()),
        sa.Column('checked_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('total_saps', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('matched', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('items', sa.Text(), nullable=False, server_default=sa.text("'[]'")),
    )
    op.create_index('ix_stock_excel_checks_site_at', 'stock_excel_checks',
                    ['Site_ID', 'checked_at'])


def downgrade() -> None:
    op.drop_index('ix_stock_excel_checks_site_at', 'stock_excel_checks')
    op.drop_table('stock_excel_checks')
