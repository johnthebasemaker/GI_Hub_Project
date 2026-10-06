"""phase21d — the consumption-paper names a store keeper taught the matcher

Revision ID: e2a8c4f6b1d9
Revises: d1f7a3c9e2b4
Create Date: 2026-10-06 15:00:00

Phase 21d, rulings Q21-3/5. When the store keeper accepts or picks the item a
handwritten name means ("Dust Mash" → DUST Mask), the pair is remembered PER
SITE and matched green next time. HOD / Admin delete a wrong one.

  ocr_aliases  Site_ID · written_key (corrected, lower-case, punctuation
               collapsed — `ai/consumption_match.written_key`) · an example of
               the written form · SAP_Code · confirmations · who/when

UNIQUE (Site_ID, written_key): one meaning per written form per site; a
different SAP confirmed later REPLACES it (and resets the count). Additive;
`models.py` carries the same (rule 15).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'e2a8c4f6b1d9'
down_revision = 'd1f7a3c9e2b4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'ocr_aliases',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('Site_ID', sa.Text(), nullable=False),
        sa.Column('written_key', sa.Text(), nullable=False),
        sa.Column('written_example', sa.Text()),
        sa.Column('SAP_Code', sa.Text(), nullable=False),
        sa.Column('confirmations', sa.Integer(), nullable=False, server_default=sa.text('1')),
        sa.Column('created_by', sa.Text()),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_by', sa.Text()),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.UniqueConstraint('Site_ID', 'written_key', name='ux_ocr_aliases_site_key'),
    )


def downgrade() -> None:
    op.drop_table('ocr_aliases')
