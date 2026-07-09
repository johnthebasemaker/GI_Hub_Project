"""whatsapp_outbox outbound queue (Phase 7)

NEW-STACK ONLY table (same contract as auth_sessions / ai_jobs / sla_dismissals):
dual_ci resets leave it empty — it logs outbound WhatsApp Cloud API messages and
their delivery status; not migrated business data.

Revision ID: e5c3f19a77b2
Revises: d4f1a27c8e90
Create Date: 2026-07-09 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e5c3f19a77b2'
down_revision: Union[str, None] = 'd4f1a27c8e90'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'whatsapp_outbox',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('to_number', sa.Text(), nullable=True),
        sa.Column('message_type', sa.Text(), server_default=sa.text("'text'"), nullable=True),
        sa.Column('body', sa.Text(), nullable=True),
        sa.Column('payload_json', sa.Text(), nullable=True),
        sa.Column('status', sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column('meta_message_id', sa.Text(), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('event_key', sa.Text(), nullable=True),
        sa.Column('related_table', sa.Text(), nullable=True),
        sa.Column('related_ref', sa.Text(), nullable=True),
        sa.Column('attempts', sa.Integer(), server_default=sa.text('0'), nullable=True),
        sa.Column('created_by', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=True),
        sa.Column('sent_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_whatsapp_outbox_status'), 'whatsapp_outbox', ['status'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_whatsapp_outbox_status'), table_name='whatsapp_outbox')
    op.drop_table('whatsapp_outbox')
