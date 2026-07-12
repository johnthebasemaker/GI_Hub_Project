"""phone_otp.stage — dual-OTP phone-change workflow (UAT refinement)

Adds a stage marker to the one-time-code table: 'old' = the code that
authorizes the change from the currently registered device, 'new' = the code
that proves the NEW number can actually receive WhatsApp before it is
committed (typo lock-out guard). Existing rows default to 'new' (the only
behaviour that existed before this migration).

Revision ID: d6b0e72f51a8
Revises: c3a9d51e42b0
Create Date: 2026-07-12 10:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd6b0e72f51a8'
down_revision: Union[str, None] = 'c3a9d51e42b0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('phone_otp',
                  sa.Column('stage', sa.Text(), nullable=False,
                            server_default=sa.text("'new'")))


def downgrade() -> None:
    op.drop_column('phone_otp', 'stage')
