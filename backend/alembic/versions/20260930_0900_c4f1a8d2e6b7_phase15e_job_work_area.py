"""phase15e — the part of the equipment a job covered (Work_Area)

Revision ID: c4f1a8d2e6b7
Revises: b7e2c4a19d53
Create Date: 2026-09-30 09:00:00

The store keeper already writes it in the Consumption Log's Remarks — "Floor -
13.37 SQM Done", "Bottom B/L - 15.36 SQM Done", "South Side Dyke Wall - 10.9
SQM Done". The job card pre-fills the area, the part (`Work_Area`) and the
remark from that note; on submit the part is kept as its own field (so a report
can total an equipment's progress per part) and the remark, as typed, in the
existing `notes` column. Kept on the job and on each member row, like
`Surface_State`. Schema only — no rows to seed, so no `data_upgrade`.
`models.py` carries the same two columns (rule 15).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'c4f1a8d2e6b7'
down_revision = 'b7e2c4a19d53'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('sme_attribution_group', sa.Column('Work_Area', sa.Text()))
    op.add_column('sme_consumption_log', sa.Column('Work_Area', sa.Text()))


def downgrade() -> None:
    op.drop_column('sme_consumption_log', 'Work_Area')
    op.drop_column('sme_attribution_group', 'Work_Area')
