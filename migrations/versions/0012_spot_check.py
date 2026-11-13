"""Spot checks: a teacher confirms that random students marked present are in the room.

Revision ID: 0012_spot_check
Revises: 0011_leave_request
Create Date: 2026-11-14

"""
import sqlalchemy as sa
from alembic import op

revision = '0012_spot_check'
down_revision = '0011_leave_request'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'spot_check',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('session_id', sa.Integer(), nullable=False),
        sa.Column('student_id', sa.Integer(), nullable=False),
        sa.Column('result', sa.String(10), nullable=False, server_default='pending'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('checked_by_user_id', sa.Integer(), nullable=True),
        sa.Column('resolved_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['session_id'], ['class_session.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['student_id'], ['student.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['checked_by_user_id'], ['user_account.id'], ondelete='SET NULL',
                                name='fk_spot_check_checked_by_user'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('uq_spot_check_session_student', 'spot_check', ['session_id', 'student_id'], unique=True)
    op.create_index('ix_spot_check_student_id', 'spot_check', ['student_id'])


def downgrade():
    op.drop_index('ix_spot_check_student_id', table_name='spot_check')
    op.drop_index('uq_spot_check_session_student', table_name='spot_check')
    op.drop_table('spot_check')
