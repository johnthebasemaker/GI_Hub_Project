"""phase18 — a tool loan knows WHAT was lent and HOW it came back

Revision ID: a7d3e1f5c829
Revises: e5b2c7a9d4f1
Create Date: 2026-10-03 23:00:00

Phase 18 Track 3. A loan was a free-text name and a status flip: nothing tied it
to a SAP code, a serial or an asset tag, so a scanned tool could not find its
own loan, and a return recorded neither when, by whom, nor in what condition.

  SAP_Code          the inventory item, when the tool was scanned or picked
  Item_Ref          the exact code scanned (serial, asset tag, sticker) — what
                    a return scan is matched against first
  returned_time     LOCAL naive time, like given_time / expected_return_time
  returned_by       the store keeper who received it
  return_condition  ok | damaged | incomplete
  return_note       free text

Schema only, all nullable — existing loans are untouched. `models.py` carries
the same columns (rule 15).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'a7d3e1f5c829'
down_revision = 'e5b2c7a9d4f1'
branch_labels = None
depends_on = None

_COLS = (
    ('SAP_Code', sa.Text()),
    ('Item_Ref', sa.Text()),
    ('returned_time', sa.DateTime()),
    ('returned_by', sa.Text()),
    ('return_condition', sa.Text()),
    ('return_note', sa.Text()),
)


def upgrade() -> None:
    for name, typ in _COLS:
        op.add_column('returnable_items', sa.Column(name, typ))
    op.create_index('ix_returnable_items_site_status', 'returnable_items',
                    ['Site_ID', 'status'])


def downgrade() -> None:
    op.drop_index('ix_returnable_items_site_status', table_name='returnable_items')
    for name, _typ in reversed(_COLS):
        op.drop_column('returnable_items', name)
