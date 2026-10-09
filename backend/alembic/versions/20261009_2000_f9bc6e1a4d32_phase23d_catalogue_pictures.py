"""phase23d — the material catalogue, the site equipment list, and pictures

Revision ID: f9bc6e1a4d32
Revises: e8ab5d9f3c21
Create Date: 2026-10-09 20:00:00

Phase 23d, rulings Q23-5..9.

  material_catalog  every GI material code in Drive's "All MATERIAL CODES-*.xlsx"
                    (≈ 6,000; both sheets merged). NOT the item master — 6,000
                    unstocked codes would swamp stock, reorder and counts. An
                    item-master row links to it by Material_Code.
  site_equipment    the site plant & tools list ("Equipment list Updated as on
                    …xlsx", ruling Q23-9): one row per equipment line, keyed by
                    its section and description (the sheet repeats S.No values).
  item_images       pictures of a material code or an equipment line: one set
                    per code shared by every site (Q23-8), stored on disk
                    (`media/catalog/`, Q23-7), content-addressed by sha256, at
                    most 4 live per item, the primary first. Removing is a
                    soft delete — `removed_at` — so a picture can be restored.

Additive; `models.py` carries the same (rule 15). No data change.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'f9bc6e1a4d32'
down_revision = 'e8ab5d9f3c21'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'material_catalog',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('Material_Code', sa.Text(), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('uom', sa.Text()),
        sa.Column('series', sa.Text()),
        sa.Column('source_file', sa.Text()),
        sa.Column('first_seen', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('last_seen', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('removed_at', sa.DateTime()),
        sa.UniqueConstraint('Material_Code', name='ux_material_catalog_code'),
    )
    op.create_table(
        'site_equipment',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('Site_ID', sa.Text(), nullable=False),
        sa.Column('equipment_key', sa.Text(), nullable=False),
        sa.Column('category', sa.Text()),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('brand', sa.Text()),
        sa.Column('serials', sa.Text()),
        sa.Column('asset_no', sa.Text()),
        sa.Column('uom', sa.Text()),
        sa.Column('qty', sa.Float()),
        sa.Column('sticker_no', sa.Text()),
        sa.Column('sticker_expiry', sa.Text()),
        sa.Column('condition', sa.Text()),
        sa.Column('remarks', sa.Text()),
        sa.Column('source_file', sa.Text()),
        sa.Column('source_row', sa.Integer()),
        sa.Column('first_seen', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('last_seen', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('removed_at', sa.DateTime()),
        sa.UniqueConstraint('Site_ID', 'equipment_key', name='ux_site_equipment_key'),
    )
    op.create_table(
        'item_images',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('kind', sa.Text(), nullable=False),
        sa.Column('item_key', sa.Text(), nullable=False),
        sa.Column('sha256', sa.Text(), nullable=False),
        sa.Column('mime', sa.Text()),
        sa.Column('width', sa.Integer()),
        sa.Column('height', sa.Integer()),
        sa.Column('bytes', sa.Integer()),
        sa.Column('source', sa.Text(), nullable=False),
        sa.Column('drive_file_id', sa.Integer()),
        sa.Column('is_primary', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('caption', sa.Text()),
        sa.Column('uploaded_by', sa.Text(), nullable=False),
        sa.Column('uploaded_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('removed_at', sa.DateTime()),
        sa.Column('removed_by', sa.Text()),
        sa.CheckConstraint("kind IN ('material', 'equipment')", name='ck_item_images_kind'),
        sa.CheckConstraint("source IN ('upload', 'drive', 'family', 'practice')",
                           name='ck_item_images_source'),
    )
    op.create_index('ix_item_images_item', 'item_images', ['kind', 'item_key'])
    op.create_index('ix_item_images_sha', 'item_images', ['sha256'])


def downgrade() -> None:
    op.drop_index('ix_item_images_sha', table_name='item_images')
    op.drop_index('ix_item_images_item', table_name='item_images')
    op.drop_table('item_images')
    op.drop_table('site_equipment')
    op.drop_table('material_catalog')
