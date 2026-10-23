"""Record how each attendance entry was made: QR scan or manual staff entry.

Revision ID: 0003_manual_attendance
Revises: 0002_security_hardening
Create Date: 2026-10-13

"""
import sqlalchemy as sa
from alembic import op

revision = '0003_manual_attendance'
down_revision = '0002_security_hardening'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('attendance') as batch:
        batch.add_column(sa.Column('source', sa.String(10), nullable=False, server_default='scan'))
        batch.add_column(sa.Column('marked_by_admin_id', sa.Integer(), nullable=True))
        batch.add_column(sa.Column('note', sa.String(200), nullable=True))
        batch.create_foreign_key(
            'fk_attendance_marked_by_admin', 'admin', ['marked_by_admin_id'], ['id'], ondelete='SET NULL',
        )


def downgrade():
    with op.batch_alter_table('attendance') as batch:
        batch.drop_constraint('fk_attendance_marked_by_admin', type_='foreignkey')
        batch.drop_column('note')
        batch.drop_column('marked_by_admin_id')
        batch.drop_column('source')
