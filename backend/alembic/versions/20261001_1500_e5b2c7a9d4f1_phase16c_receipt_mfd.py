"""phase16c — a pending receipt carries its MFD to the lot

Revision ID: e5b2c7a9d4f1
Revises: d8a3f6c1b2e9
Create Date: 2026-10-01 15:00:00

The Receive form now asks for the manufacture date of a lot-tracked material
(Phase 16c). It travels on the staged row to the HOD's approval and lands on the
lot (`lots.MFD_Date`), where a missing expiry is derived from it and the item's
shelf life. Schema only. `models.py` carries the same column (rule 15).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'e5b2c7a9d4f1'
down_revision = 'd8a3f6c1b2e9'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('pending_receipts', sa.Column('MFD_Date', sa.Text()))


def downgrade() -> None:
    op.drop_column('pending_receipts', 'MFD_Date')
