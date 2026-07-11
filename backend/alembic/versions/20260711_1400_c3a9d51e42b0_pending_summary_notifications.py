"""pending_summary_notifications — evening-digest staging (Phase 6)

NEW-STACK ONLY table (same contract as phone_otp / whatsapp_outbox): dual_ci
resets leave it empty. Holds WhatsApp events deferred by the user's
delivery_preference="evening"; the 16:00 batch aggregator compiles one
gi_evening_summary message per recipient and marks rows processed only after
a successful send.

Revision ID: c3a9d51e42b0
Revises: b8d2f4a61c07
Create Date: 2026-07-11 14:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3a9d51e42b0'
down_revision: Union[str, None] = 'b8d2f4a61c07'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'pending_summary_notifications',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('recipient_user', sa.Text(), nullable=False),
        sa.Column('event_key', sa.Text(), nullable=False),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('body', sa.Text(), nullable=True),
        sa.Column('related_table', sa.Text(), nullable=True),
        sa.Column('related_ref', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=True),
        sa.Column('processed_at', sa.DateTime(), nullable=True),
        sa.Column('digest_outbox_id', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_pending_summary_notifications_recipient_user'),
                    'pending_summary_notifications', ['recipient_user'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_pending_summary_notifications_recipient_user'),
                  table_name='pending_summary_notifications')
    op.drop_table('pending_summary_notifications')
