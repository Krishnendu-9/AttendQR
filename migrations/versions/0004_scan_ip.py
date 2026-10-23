"""Record the IP address each QR scan came from.

Revision ID: 0004_scan_ip
Revises: 0003_manual_attendance
Create Date: 2026-10-23

"""
import sqlalchemy as sa
from alembic import op

revision = '0004_scan_ip'
down_revision = '0003_manual_attendance'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('attendance') as batch:
        batch.add_column(sa.Column('scan_ip', sa.String(45), nullable=True))


def downgrade():
    with op.batch_alter_table('attendance') as batch:
        batch.drop_column('scan_ip')
