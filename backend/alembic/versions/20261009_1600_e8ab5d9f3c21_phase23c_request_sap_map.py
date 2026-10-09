"""phase23c — the "needs a SAP code" mapper: request names learned per site

Revision ID: e8ab5d9f3c21
Revises: d7fa4c8e2b19
Create Date: 2026-10-09 16:00:00

Phase 23c, rulings Q23-4/5.

  request_sap_map   one decision per (site, written key) for a request line the
                    workbook gave no SAP code: `item` (→ SAP_Code in the item
                    master), `catalogue` (→ a GI material code GI Hub does not
                    stock yet: it links by itself the day that code appears in
                    the workbook — no SAP number is invented, Q23-5) or
                    `not_stock` (a service or one-off: out of pending and
                    reorder). The key is the line's GI code when it has one
                    (`code:GI-7000087`), else its normalised description
                    (`desc:garden trowel hand showel`). Applied when the
                    requests are READ, so a mapping works at once and the
                    workbook is never touched.

Additive; `models.py` carries the same (rule 15). No data change.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'e8ab5d9f3c21'
down_revision = 'd7fa4c8e2b19'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'request_sap_map',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('Site_ID', sa.Text(), nullable=False),
        sa.Column('written_key', sa.Text(), nullable=False),
        sa.Column('written_example', sa.Text()),
        sa.Column('decision', sa.Text(), nullable=False),
        sa.Column('SAP_Code', sa.Text()),
        sa.Column('Material_Code', sa.Text()),
        sa.Column('created_by', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_by', sa.Text()),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.UniqueConstraint('Site_ID', 'written_key', name='ux_request_sap_map_key'),
        sa.CheckConstraint("decision IN ('item', 'catalogue', 'not_stock')",
                           name='ck_request_sap_map_decision'),
    )


def downgrade() -> None:
    op.drop_table('request_sap_map')
