"""phone_otp one-time codes for self-service phone changes (Phase 7c)

NEW-STACK ONLY table (same contract as whatsapp_outbox / email_outbox / ai_jobs):
dual_ci resets leave it empty — it holds short-lived, hashed OTP codes for
phone-number verification, not migrated business data.

Revision ID: a1e8c4d20f9b
Revises: f7d4a20b88c3
Create Date: 2026-07-09 16:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1e8c4d20f9b'
down_revision: Union[str, None] = 'f7d4a20b88c3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'phone_otp',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('username', sa.Text(), nullable=False),
        sa.Column('new_number', sa.Text(), nullable=False),
        sa.Column('code_hash', sa.Text(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('consumed_at', sa.DateTime(), nullable=True),
        sa.Column('attempts', sa.Integer(), server_default=sa.text('0'), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_phone_otp_username'), 'phone_otp', ['username'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_phone_otp_username'), table_name='phone_otp')
    op.drop_table('phone_otp')
