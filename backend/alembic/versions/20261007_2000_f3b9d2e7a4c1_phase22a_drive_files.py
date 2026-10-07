"""phase22a — Drive subfolder index and the run history

Revision ID: f3b9d2e7a4c1
Revises: e2a8c4f6b1d9
Create Date: 2026-10-07 20:00:00

Phase 22a, rulings Q22-1..6. The Drive sync now also reads the DN, MTC and
Pending Material Follow-up subfolders of the one backup folder. Index, don't
import (plan §1.2): one row per Drive file, plus a read-only copy on disk.

  drive_files      drive_id (unique) · kind (dn | mtc | pending) · folder ·
                   name · mime · size · md5 · modified_time · cache_path ·
                   parsed_key / parsed_date (filled by 22b–22d) · link_status ·
                   first/last seen · removed_at (gone from Drive: kept, marked)
  drive_sync_runs  one row per run (schedule, Pull button, Admin card, CLI):
                   when, who, ok, whether the ERP side was auto-committed, the
                   report as JSON — the history the Admin card shows and the
                   top-bar "Last updated from Drive" reads

Additive; `models.py` carries the same (rule 15).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'f3b9d2e7a4c1'
down_revision = 'e2a8c4f6b1d9'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'drive_files',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('drive_id', sa.Text(), nullable=False),
        sa.Column('kind', sa.Text(), nullable=False),
        sa.Column('folder', sa.Text()),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('mime', sa.Text()),
        sa.Column('size', sa.BigInteger()),
        sa.Column('md5', sa.Text()),
        sa.Column('modified_time', sa.Text()),
        sa.Column('cache_path', sa.Text()),
        sa.Column('parsed_key', sa.Text()),
        sa.Column('parsed_date', sa.Date()),
        sa.Column('link_status', sa.Text()),
        sa.Column('first_seen', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('last_seen', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('removed_at', sa.DateTime()),
        sa.UniqueConstraint('drive_id', name='ux_drive_files_drive_id'),
    )
    op.create_index('ix_drive_files_kind_key', 'drive_files', ['kind', 'parsed_key'])
    op.create_table(
        'drive_sync_runs',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('started_at', sa.DateTime(), nullable=False),
        sa.Column('finished_at', sa.DateTime()),
        sa.Column('trigger', sa.Text(), nullable=False),
        sa.Column('by_user', sa.Text()),
        sa.Column('kind', sa.Text(), nullable=False),
        sa.Column('ok', sa.Boolean()),
        sa.Column('files_changed', sa.Integer(), server_default=sa.text('0')),
        sa.Column('erp_auto_committed', sa.Boolean(), server_default=sa.text('false')),
        sa.Column('report', sa.Text()),
    )
    op.create_index('ix_drive_sync_runs_started', 'drive_sync_runs', ['started_at'])


def downgrade() -> None:
    op.drop_index('ix_drive_sync_runs_started', table_name='drive_sync_runs')
    op.drop_table('drive_sync_runs')
    op.drop_index('ix_drive_files_kind_key', table_name='drive_files')
    op.drop_table('drive_files')
