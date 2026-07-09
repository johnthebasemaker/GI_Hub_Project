"""email_outbox outbound queue (Phase 7b)

NEW-STACK ONLY table (same contract as whatsapp_outbox / ai_jobs): dual_ci
resets leave it empty — it logs outbound SMTP emails and their delivery status;
not migrated business data.

Revision ID: f7d4a20b88c3
Revises: e5c3f19a77b2
Create Date: 2026-07-09 14:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f7d4a20b88c3'
down_revision: Union[str, None] = 'e5c3f19a77b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'email_outbox',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('to_email', sa.Text(), nullable=True),
        sa.Column('cc', sa.Text(), nullable=True),
        sa.Column('subject', sa.Text(), nullable=True),
        sa.Column('body', sa.Text(), nullable=True),
        sa.Column('status', sa.Text(), server_default=sa.text("'pending'"), nullable=False),
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
    op.create_index(op.f('ix_email_outbox_status'), 'email_outbox', ['status'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_email_outbox_status'), table_name='email_outbox')
    op.drop_table('email_outbox')
