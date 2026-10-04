"""phase19 — a loan can come back in parts, and is chased until it is all back

Revision ID: c4e9b2a7f613
Revises: a7d3e1f5c829
Create Date: 2026-10-04 12:00:00

Phase 19c, ruling Q19-3. A loan of 5 was all-or-nothing: 3 back and 2 still
out had no record. Now:

  returnable_items.qty_returned      how much is back so far (0 for every
                                     existing loan; a returned loan is
                                     backfilled to its qty so "open qty" is 0)
  returnable_items.last_reminded_at  when the daily chaser last reminded the
                                     borrower (LOCAL naive time)
  returnable_items.hod_escalated_at  when the HOD was told it is > 3 days
                                     overdue — once per loan
  returnable_returns                 one row per part handed back: how many,
                                     in what condition, by whom, when. Never
                                     updated or deleted by the app (no undo,
                                     ruling Q19-3).

Additive, so existing rows and callers are untouched. `models.py` carries the
same (rule 15).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'c4e9b2a7f613'
down_revision = 'a7d3e1f5c829'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('returnable_items', sa.Column(
        'qty_returned', sa.Float(), nullable=False, server_default=sa.text('0')))
    op.add_column('returnable_items', sa.Column('last_reminded_at', sa.DateTime()))
    op.add_column('returnable_items', sa.Column('hod_escalated_at', sa.DateTime()))
    data_upgrade(op.get_bind())
    op.create_table(
        'returnable_returns',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('loan_id', sa.Integer(),
                  sa.ForeignKey('returnable_items.id', ondelete='CASCADE'), nullable=False),
        sa.Column('qty', sa.Float(), nullable=False),
        sa.Column('condition', sa.Text(), nullable=False),
        sa.Column('note', sa.Text()),
        sa.Column('returned_by', sa.Text()),
        sa.Column('returned_time', sa.DateTime()),
        sa.Column('Site_ID', sa.Text()),
    )
    op.create_index('ix_returnable_returns_loan', 'returnable_returns', ['loan_id'])


def data_upgrade(conn) -> None:
    """DATA step — see cutover_migrate.run_data_migrations.

    A loan already returned in full has nothing left out. Guarded on
    qty_returned still being 0, so a re-run never overwrites a real count."""
    conn.execute(sa.text(
        "UPDATE returnable_items SET qty_returned = COALESCE(qty, 1) "
        "WHERE status = 'returned' AND COALESCE(qty_returned, 0) = 0"))


def downgrade() -> None:
    op.drop_index('ix_returnable_returns_loan', table_name='returnable_returns')
    op.drop_table('returnable_returns')
    op.drop_column('returnable_items', 'hod_escalated_at')
    op.drop_column('returnable_items', 'last_reminded_at')
    op.drop_column('returnable_items', 'qty_returned')
