"""phase22e — who PREPARED a consumption paper, apart from who submitted it

Revision ID: d7fa4c8e2b19
Revises: c6e9a3b7d4f8
Create Date: 2026-10-08 18:00:00

Phase 22e, rulings Q22-14..16. The workbook's *Prepared by* is the person on
shift who wrote the paper (Night → Kalied, Day → Johnson at CNCEC). The app
kept it in `Issued_By` — which on an app-staged issue is the submitter's LOGIN,
the column the HOD's approve/reject notices are addressed by. So it gets its
own column:

  pending_issues."Prepared_By", consumption."Prepared_By"

Back-filled for the rows the Excel sync owns (`Source_Ref` XLSX:…), where
`Issued_By` already IS the workbook's Prepared by — so the next pull, which now
maps *Prepared by* to both, finds nothing to change. Additive; `models.py`
carries the same (rule 15).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'd7fa4c8e2b19'
down_revision = 'c6e9a3b7d4f8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('pending_issues', sa.Column('Prepared_By', sa.Text()))
    op.add_column('consumption', sa.Column('Prepared_By', sa.Text()))
    data_upgrade(op.get_bind())


def data_upgrade(conn) -> None:
    """The synced rows' Prepared by is already in `Issued_By` — copy it, so the
    next pull (which maps *Prepared by* to both) finds nothing to change.
    Idempotent: only empty cells; `cutover_migrate.py` replays it."""
    conn.execute(sa.text('UPDATE consumption SET "Prepared_By" = "Issued_By" '
                         "WHERE \"Source_Ref\" LIKE 'XLSX:%' AND \"Prepared_By\" IS NULL"))


def downgrade() -> None:
    op.drop_column('consumption', 'Prepared_By')
    op.drop_column('pending_issues', 'Prepared_By')
