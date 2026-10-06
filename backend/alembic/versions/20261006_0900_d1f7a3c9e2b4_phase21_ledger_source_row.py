"""phase21a — a ledger row remembers the workbook sheet and row it came from

Revision ID: d1f7a3c9e2b4
Revises: c4e9b2a7f613
Create Date: 2026-10-06 09:00:00

Phase 21a (brief Track 4.3). A synced consumption that names a lot that does
not exist, or one already used up, must be shown with the SHEET and ROW the
store keeper has to fix — "Consumption Log, row 5,581" — not just the lot.

  consumption.Source_Sheet / Source_Row   the workbook sheet and its 1-based
  returns.Source_Sheet / Source_Row       Excel row, written by the Excel sync
                                          on every run (a row moves when the
                                          operator inserts rows above it, so
                                          this is "the row at the last sync")

Both are PROVENANCE ONLY — never part of the row label (`Source_Ref`), never a
key, never compared when deciding whether a workbook line changed. App-written
rows leave them NULL. Additive and nullable; `models.py` carries the same
(rule 15). No data step: the next sync fills them.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'd1f7a3c9e2b4'
down_revision = 'c4e9b2a7f613'
branch_labels = None
depends_on = None


def upgrade() -> None:
    for t in ('consumption', 'returns'):
        op.add_column(t, sa.Column('Source_Sheet', sa.Text()))
        op.add_column(t, sa.Column('Source_Row', sa.Integer()))


def downgrade() -> None:
    for t in ('consumption', 'returns'):
        op.drop_column(t, 'Source_Row')
        op.drop_column(t, 'Source_Sheet')
