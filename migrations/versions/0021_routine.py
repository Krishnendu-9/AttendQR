"""The class routine: bell schedules, holidays, and versioned timetables with their entries.

Revision ID: 0021_routine
Revises: 0020_subject_options
Create Date: 2026-11-15

"""
import sqlalchemy as sa
from alembic import op

revision = '0021_routine'
down_revision = '0020_subject_options'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'bell_schedule',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(60), nullable=False),
        sa.Column('department_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['department_id'], ['department.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'bell_period',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('schedule_id', sa.Integer(), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('label', sa.String(20), nullable=False),
        sa.Column('start_time', sa.Time(), nullable=False),
        sa.Column('end_time', sa.Time(), nullable=False),
        sa.Column('is_break', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.ForeignKeyConstraint(['schedule_id'], ['bell_schedule.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_bell_period_schedule_id', 'bell_period', ['schedule_id'])
    op.create_table(
        'bell_schedule_day',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('schedule_id', sa.Integer(), nullable=False),
        sa.Column('weekday', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['schedule_id'], ['bell_schedule.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_bell_schedule_day_schedule_id', 'bell_schedule_day', ['schedule_id'])
    op.create_table(
        'holiday',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('start_date', sa.Date(), nullable=False),
        sa.Column('end_date', sa.Date(), nullable=False),
        sa.Column('title', sa.String(100), nullable=False),
        sa.Column('department_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['department_id'], ['department.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'timetable',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('department_id', sa.Integer(), nullable=False),
        sa.Column('semester_id', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(10), nullable=False, server_default='draft'),
        sa.Column('effective_from', sa.Date(), nullable=True),
        sa.Column('effective_to', sa.Date(), nullable=True),
        sa.Column('note', sa.String(200), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('created_by_user_id', sa.Integer(), nullable=True),
        sa.Column('published_at', sa.DateTime(), nullable=True),
        sa.Column('published_by_user_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['department_id'], ['department.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['semester_id'], ['semester.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['user_account.id'], ondelete='SET NULL',
                                name='fk_timetable_created_by_user'),
        sa.ForeignKeyConstraint(['published_by_user_id'], ['user_account.id'], ondelete='SET NULL',
                                name='fk_timetable_published_by_user'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_timetable_semester_id', 'timetable', ['semester_id'])
    op.create_table(
        'timetable_entry',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('timetable_id', sa.Integer(), nullable=False),
        sa.Column('weekday', sa.Integer(), nullable=False),
        sa.Column('start_time', sa.Time(), nullable=False),
        sa.Column('end_time', sa.Time(), nullable=False),
        sa.Column('subject_id', sa.Integer(), nullable=True),
        sa.Column('title', sa.String(100), nullable=True),
        sa.Column('group_id', sa.Integer(), nullable=True),
        sa.Column('room', sa.String(40), nullable=True),
        sa.Column('week_pattern', sa.String(5), nullable=False, server_default='every'),
        sa.Column('valid_from', sa.Date(), nullable=True),
        sa.Column('valid_to', sa.Date(), nullable=True),
        sa.Column('note', sa.String(200), nullable=True),
        sa.ForeignKeyConstraint(['timetable_id'], ['timetable.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['subject_id'], ['subject.id'], name='fk_timetable_entry_subject'),
        sa.ForeignKeyConstraint(['group_id'], ['class_group.id'], name='fk_timetable_entry_group'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_timetable_entry_timetable_id', 'timetable_entry', ['timetable_id'])
    op.create_index('ix_timetable_entry_subject_id', 'timetable_entry', ['subject_id'])
    op.create_index('ix_timetable_entry_group_id', 'timetable_entry', ['group_id'])
    op.create_table(
        'timetable_entry_teacher',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('entry_id', sa.Integer(), nullable=False),
        sa.Column('teacher_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['entry_id'], ['timetable_entry.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['teacher_id'], ['teacher.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('uq_timetable_entry_teacher', 'timetable_entry_teacher', ['entry_id', 'teacher_id'], unique=True)
    op.create_index('ix_timetable_entry_teacher_teacher_id', 'timetable_entry_teacher', ['teacher_id'])


def downgrade():
    op.drop_index('ix_timetable_entry_teacher_teacher_id', table_name='timetable_entry_teacher')
    op.drop_index('uq_timetable_entry_teacher', table_name='timetable_entry_teacher')
    op.drop_table('timetable_entry_teacher')
    op.drop_index('ix_timetable_entry_group_id', table_name='timetable_entry')
    op.drop_index('ix_timetable_entry_subject_id', table_name='timetable_entry')
    op.drop_index('ix_timetable_entry_timetable_id', table_name='timetable_entry')
    op.drop_table('timetable_entry')
    op.drop_index('ix_timetable_semester_id', table_name='timetable')
    op.drop_table('timetable')
    op.drop_table('holiday')
    op.drop_index('ix_bell_schedule_day_schedule_id', table_name='bell_schedule_day')
    op.drop_table('bell_schedule_day')
    op.drop_index('ix_bell_period_schedule_id', table_name='bell_period')
    op.drop_table('bell_period')
    op.drop_table('bell_schedule')
