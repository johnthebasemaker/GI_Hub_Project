"""feedback_triage — safe change-management fields on bug_reports

2026-07-18 Admin Bug Tracking Engine: reports become actionable work items
without risking stability — severity + title at submission; rollback_notes,
safety_constraints and triage_notes at admin triage. The reports then export
as self-contained implementation prompts (GET /admin/feedback/{id}/prompt)
for a coding-agent session, which is how "not directly connected to the
project" gets bridged.

Revision ID: c7d4e8f19a25
Revises: b3f2a9c47d18
Create Date: 2026-07-18 16:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c7d4e8f19a25'
down_revision: Union[str, None] = 'b3f2a9c47d18'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLS = ('title', 'severity', 'rollback_notes', 'safety_constraints',
         'triage_notes')


def upgrade() -> None:
    for col in _COLS:
        op.add_column('bug_reports', sa.Column(col, sa.Text(), nullable=True))


def downgrade() -> None:
    for col in reversed(_COLS):
        op.drop_column('bug_reports', col)
