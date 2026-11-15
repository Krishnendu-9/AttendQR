"""Changes to the routine for one date: cancel, move, substitute, extra class.

Revision ID: 0023_routine_changes
Revises: 0022_session_occurrence
Create Date: 2026-11-15

"""
import sqlalchemy as sa
from alembic import op

revision = '0023_routine_changes'
down_revision = '0022_session_occurrence'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'routine_change',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(12), nullable=False),
        sa.Column('timetable_entry_id', sa.Integer(), nullable=True),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('new_date', sa.Date(), nullable=True),
        sa.Column('new_start_time', sa.Time(), nullable=True),
        sa.Column('new_end_time', sa.Time(), nullable=True),
        sa.Column('substitute_teacher_id', sa.Integer(), nullable=True),
        sa.Column('department_id', sa.Integer(), nullable=False),
        sa.Column('semester_id', sa.Integer(), nullable=False),
        sa.Column('subject_id', sa.Integer(), nullable=True),
        sa.Column('title', sa.String(100), nullable=True),
        sa.Column('group_id', sa.Integer(), nullable=True),
        sa.Column('room', sa.String(40), nullable=True),
        sa.Column('reason', sa.String(200), nullable=False),
        sa.Column('session_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('created_by_user_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['timetable_entry_id'], ['timetable_entry.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['substitute_teacher_id'], ['teacher.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['department_id'], ['department.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['semester_id'], ['semester.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['subject_id'], ['subject.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['group_id'], ['class_group.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['session_id'], ['class_session.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['user_account.id'], ondelete='SET NULL',
                                name='fk_routine_change_created_by_user'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_routine_change_entry_date', 'routine_change', ['timetable_entry_id', 'date'])
    op.create_index('ix_routine_change_semester_date', 'routine_change', ['semester_id', 'date'])


def downgrade():
    op.drop_index('ix_routine_change_semester_date', table_name='routine_change')
    op.drop_index('ix_routine_change_entry_date', table_name='routine_change')
    op.drop_table('routine_change')
