"""Legacy-column preservation (UAT Phase 5 cutover audit)

The cutover dry-run showed the models contract silently DROPPING live legacy
data: inventory.Sl_No (293 values), consumption.status/WBS, and the Technician
fields. It also exposed a case bug — consumption/receipts declare "WBS"
(uppercase) in SQLite while models used lowercase 'wbs', so the copier treated
them as different columns. This revision aligns Postgres targets that were
created before the fix.

Revision ID: b8d2f4a61c07
Revises: a1e8c4d20f9b
Create Date: 2026-07-10 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b8d2f4a61c07'
down_revision: Union[str, None] = 'a1e8c4d20f9b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Case fix: the DB column must be "WBS" exactly (legacy SQLite casing).
    op.alter_column('consumption', 'wbs', new_column_name='WBS')
    op.alter_column('receipts', 'wbs', new_column_name='WBS')
    # Legacy fields preserved at cutover (not used by v2 code).
    op.add_column('inventory', sa.Column('Sl_No', sa.Text(), nullable=True))
    op.add_column('consumption', sa.Column('status', sa.Text(), nullable=True))
    op.add_column('consumption', sa.Column('Technician', sa.Text(), nullable=True))
    op.add_column('pending_issues', sa.Column('Technician', sa.Text(), nullable=True))
    op.add_column('rejected_issues_archive', sa.Column('Technician', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('rejected_issues_archive', 'Technician')
    op.drop_column('pending_issues', 'Technician')
    op.drop_column('consumption', 'Technician')
    op.drop_column('consumption', 'status')
    op.drop_column('inventory', 'Sl_No')
    op.alter_column('receipts', 'WBS', new_column_name='wbs')
    op.alter_column('consumption', 'WBS', new_column_name='wbs')
