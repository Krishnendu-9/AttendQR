"""Make each QR scan grant usable for one attendance only.

Revision ID: 0005_scan_grant
Revises: 0004_scan_ip
Create Date: 2026-11-07

"""
import sqlalchemy as sa
from alembic import op

revision = '0005_scan_grant'
down_revision = '0004_scan_ip'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('attendance') as batch:
        batch.add_column(sa.Column('scan_grant', sa.String(32), nullable=True))
    op.create_index('uq_attendance_scan_grant', 'attendance', ['scan_grant'], unique=True)


def downgrade():
    op.drop_index('uq_attendance_scan_grant', table_name='attendance')
    with op.batch_alter_table('attendance') as batch:
        batch.drop_column('scan_grant')
