"""Phase 14d — feature announcements and their read receipts

Revision ID: f2c9a7d41b36
Revises: e8b4c16d2f03
Create Date: 2026-09-26 19:00:00

Announcements are authored as YAML in `docs/announcements/` and loaded by a
sync; these tables hold what was loaded, its publication state and who has
seen it. Pure DDL; `models.FeatureAnnouncement` / `FeatureAnnouncementRead`
carry the same shape (rule 15).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'f2c9a7d41b36'
down_revision = 'e8b4c16d2f03'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'feature_announcements',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('key', sa.Text(), nullable=False, unique=True),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('routes', sa.Text(), nullable=False),
        sa.Column('audience_roles', sa.Text(), nullable=False),
        sa.Column('sites', sa.Text()),
        sa.Column('tutorial_module', sa.Text()),
        sa.Column('manual_section', sa.Text()),
        sa.Column('rerender', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('source_path', sa.Text()),
        sa.Column('source_sha256', sa.Text()),
        sa.Column('status', sa.Text(), nullable=False, server_default=sa.text("'draft'")),
        sa.Column('publish_at', sa.DateTime()),
        sa.Column('published_at', sa.DateTime()),
        sa.Column('published_by', sa.Text()),
        sa.Column('retracted_at', sa.DateTime()),
        sa.Column('retracted_by', sa.Text()),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
    )
    op.create_index('ix_feature_announcements_status', 'feature_announcements', ['status'])
    op.create_table(
        'feature_announcement_reads',
        sa.Column('announcement_id', sa.Integer(), primary_key=True),
        sa.Column('username', sa.Text(), primary_key=True),
        sa.Column('read_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
    )


def downgrade() -> None:
    op.drop_table('feature_announcement_reads')
    op.drop_index('ix_feature_announcements_status', 'feature_announcements')
    op.drop_table('feature_announcements')
