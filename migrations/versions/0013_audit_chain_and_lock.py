"""Tamper-evident activity log, and locking a semester's attendance.

Revision ID: 0013_audit_chain_and_lock
Revises: 0012_spot_check
Create Date: 2026-11-14

"""
import sqlalchemy as sa
from alembic import op

revision = '0013_audit_chain_and_lock'
down_revision = '0012_spot_check'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('activity_log') as batch:
        batch.add_column(sa.Column('chain_seq', sa.Integer(), nullable=True))
        batch.add_column(sa.Column('entry_hash', sa.String(64), nullable=True))
    op.create_index('uq_activity_log_chain_seq', 'activity_log', ['chain_seq'], unique=True)
    # Existing entries are sealed, in order, the first time the app seals the log.
    audit_chain = op.create_table(
        'audit_chain',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('last_seq', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_hash', sa.String(64), nullable=False, server_default=''),
        sa.PrimaryKeyConstraint('id'),
    )
    op.bulk_insert(audit_chain, [{'id': 1, 'last_seq': 0, 'last_hash': ''}])

    with op.batch_alter_table('semester') as batch:
        batch.add_column(sa.Column('attendance_locked_at', sa.DateTime(), nullable=True))
        batch.add_column(sa.Column('attendance_locked_by_user_id', sa.Integer(), nullable=True))
    with op.batch_alter_table('semester') as batch:
        batch.create_foreign_key('fk_semester_locked_by_user', 'user_account', ['attendance_locked_by_user_id'],
                                 ['id'], ondelete='SET NULL')


def downgrade():
    with op.batch_alter_table('semester') as batch:
        batch.drop_constraint('fk_semester_locked_by_user', type_='foreignkey')
    with op.batch_alter_table('semester') as batch:
        batch.drop_column('attendance_locked_by_user_id')
        batch.drop_column('attendance_locked_at')
    op.drop_table('audit_chain')
    op.drop_index('uq_activity_log_chain_seq', table_name='activity_log')
    with op.batch_alter_table('activity_log') as batch:
        batch.drop_column('entry_hash')
        batch.drop_column('chain_seq')
