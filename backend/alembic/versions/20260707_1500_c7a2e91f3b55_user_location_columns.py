"""users.Location + pending_users.Location (T4 role-based site validation)

Unscoped roles (warehouse_user / logistics) register WITHOUT a site; they may
supply a free-text Location instead. Mirrored in legacy database.py self-heal
(SQLite is the system of record until cutover, so dual_ci carries the column).

Revision ID: c7a2e91f3b55
Revises: b3e91d40aa17
Create Date: 2026-07-07 15:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c7a2e91f3b55'
down_revision: Union[str, None] = 'b3e91d40aa17'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('Location', sa.Text(), nullable=True))
    op.add_column('pending_users', sa.Column('Location', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('pending_users', 'Location')
    op.drop_column('users', 'Location')
