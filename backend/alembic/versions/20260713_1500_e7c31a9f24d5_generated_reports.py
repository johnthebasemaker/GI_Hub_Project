"""generated_reports — Phase 8-3 auto-generated report artifacts

One row per rendered weekly Executive Summary PDF, with a sha256 token hash
for the secure expiring download link that goes out via WhatsApp. New-stack
only (no SQLite counterpart).

Revision ID: e7c31a9f24d5
Revises: d6b0e72f51a8
Create Date: 2026-07-13 15:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e7c31a9f24d5'
down_revision: Union[str, None] = 'd6b0e72f51a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'generated_reports',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('kind', sa.Text(), nullable=False),
        sa.Column('Site_ID', sa.Text(), nullable=True),
        sa.Column('date_from', sa.Text(), nullable=False),
        sa.Column('date_to', sa.Text(), nullable=False),
        sa.Column('filename', sa.Text(), nullable=False),
        sa.Column('content', sa.LargeBinary(), nullable=False),
        sa.Column('token_hash', sa.Text(), nullable=False, unique=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('generated_reports')
