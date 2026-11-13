"""Leave requests: students ask to be excused, the HOD or Principal decides.

Revision ID: 0011_leave_request
Revises: 0010_utc_timestamps
Create Date: 2026-11-13

"""
import sqlalchemy as sa
from alembic import op

revision = '0011_leave_request'
down_revision = '0010_utc_timestamps'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'leave_request',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('student_id', sa.Integer(), nullable=False),
        sa.Column('start_date', sa.Date(), nullable=False),
        sa.Column('end_date', sa.Date(), nullable=False),
        sa.Column('kind', sa.String(20), nullable=False),
        sa.Column('reason', sa.String(300), nullable=False),
        sa.Column('status', sa.String(10), nullable=False, server_default='pending'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('decided_by_user_id', sa.Integer(), nullable=True),
        sa.Column('decided_at', sa.DateTime(), nullable=True),
        sa.Column('decision_note', sa.String(200), nullable=True),
        sa.ForeignKeyConstraint(['student_id'], ['student.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['decided_by_user_id'], ['user_account.id'], ondelete='SET NULL',
                                name='fk_leave_request_decided_by_user'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_leave_request_student_status', 'leave_request', ['student_id', 'status'])


def downgrade():
    op.drop_index('ix_leave_request_student_status', table_name='leave_request')
    op.drop_table('leave_request')
