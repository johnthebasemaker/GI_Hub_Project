"""sla_dismissals — admin 'Clear' actions on the Overdue Actions SLA view (T2)

NEW-STACK ONLY table (same contract as auth_sessions / ai_jobs): dual_ci
resets leave it empty — dismissals are operational state, not migrated
business data.

Revision ID: d4f1a27c8e90
Revises: c7a2e91f3b55
Create Date: 2026-07-07 18:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4f1a27c8e90'
down_revision: Union[str, None] = 'c7a2e91f3b55'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('sla_dismissals',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('kind', sa.Text(), nullable=False),
    sa.Column('ref_id', sa.Text(), nullable=False),
    sa.Column('cleared_by', sa.Text(), nullable=False),
    sa.Column('cleared_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('kind', 'ref_id', name='uq_sla_dismissals_kind_ref'),
    )


def downgrade() -> None:
    op.drop_table('sla_dismissals')
