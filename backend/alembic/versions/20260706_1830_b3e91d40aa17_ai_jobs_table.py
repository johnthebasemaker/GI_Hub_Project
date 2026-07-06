"""ai_jobs async job queue (vision OCR)

NEW-STACK ONLY table (same contract as auth_sessions): dual_ci resets leave
it empty — jobs are transient work items, not migrated business data.

Revision ID: b3e91d40aa17
Revises: fd225ce87708
Create Date: 2026-07-06 18:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3e91d40aa17'
down_revision: Union[str, None] = 'fd225ce87708'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('ai_jobs',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('kind', sa.Text(), nullable=False),
    sa.Column('status', sa.Text(), server_default=sa.text("'queued'"), nullable=False),
    sa.Column('actor', sa.Text(), nullable=False),
    sa.Column('Site_ID', sa.Text(), nullable=True),
    sa.Column('payload_json', sa.Text(), nullable=True),
    sa.Column('result_json', sa.Text(), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=True),
    sa.Column('started_at', sa.DateTime(), nullable=True),
    sa.Column('finished_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ai_jobs_actor'), 'ai_jobs', ['actor'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_ai_jobs_actor'), table_name='ai_jobs')
    op.drop_table('ai_jobs')
