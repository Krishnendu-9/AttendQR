"""Baseline schema, matching databases created before migrations were introduced.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-10-12

"""
from alembic import op
import sqlalchemy as sa

revision = '0001_baseline'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'department',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(100), nullable=False, unique=True),
        sa.Column('branch', sa.String(20), nullable=True),
    )
    op.create_table(
        'semester',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(50), nullable=False),
        sa.Column('department_id', sa.Integer(), sa.ForeignKey('department.id', ondelete='CASCADE'), nullable=False),
    )
    op.create_table(
        'teacher',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('email', sa.String(120), nullable=False, unique=True),
        sa.Column('department_id', sa.Integer(), sa.ForeignKey('department.id', ondelete='CASCADE'), nullable=False),
    )
    op.create_table(
        'subject',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('paper_code', sa.String(50), nullable=True),
        sa.Column('department_id', sa.Integer(), sa.ForeignKey('department.id', ondelete='CASCADE'), nullable=False),
        sa.Column('semester_id', sa.Integer(), sa.ForeignKey('semester.id', ondelete='CASCADE'), nullable=False),
        sa.Column('teacher_id', sa.Integer(), sa.ForeignKey('teacher.id', ondelete='SET NULL'), nullable=True),
    )
    op.create_table(
        'student',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('roll_no', sa.String(20), nullable=False),
        sa.Column('email', sa.String(120), nullable=False, unique=True),
        sa.Column('department_id', sa.Integer(), sa.ForeignKey('department.id', ondelete='CASCADE'), nullable=False),
        sa.Column('semester_id', sa.Integer(), sa.ForeignKey('semester.id', ondelete='CASCADE'), nullable=False),
        sa.Column('device_id', sa.String(100), nullable=True),
        sa.UniqueConstraint('roll_no', 'department_id', 'semester_id', name='unique_roll_dept_sem'),
    )
    op.create_table(
        'class_session',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('subject_id', sa.Integer(), sa.ForeignKey('subject.id', ondelete='CASCADE'), nullable=False),
        sa.Column('teacher_id', sa.Integer(), sa.ForeignKey('teacher.id', ondelete='SET NULL'), nullable=True),
        sa.Column('special_title', sa.String(200), nullable=True),
        sa.Column('token', sa.String(100), nullable=False, unique=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_table(
        'attendance',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('student_id', sa.Integer(), sa.ForeignKey('student.id', ondelete='CASCADE'), nullable=False),
        sa.Column('session_id', sa.Integer(), sa.ForeignKey('class_session.id', ondelete='CASCADE'), nullable=False),
        sa.Column('timestamp', sa.DateTime(), nullable=True),
    )
    op.create_index('uq_attendance_student_session', 'attendance', ['student_id', 'session_id'], unique=True)
    op.create_table(
        'admin',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('username', sa.String(80), nullable=False, unique=True),
        sa.Column('password', sa.String(200), nullable=False),
        sa.Column('role', sa.String(20), nullable=False, server_default='super_admin'),
        sa.Column('department_id', sa.Integer(), sa.ForeignKey('department.id', ondelete='SET NULL'), nullable=True),
        sa.Column('teacher_id', sa.Integer(), sa.ForeignKey('teacher.id', ondelete='SET NULL'), nullable=True),
        sa.Column('student_id', sa.Integer(), sa.ForeignKey('student.id', ondelete='SET NULL'), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('must_change_password', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('failed_login_attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('locked_until', sa.DateTime(), nullable=True),
    )
    op.create_table(
        'activity_log',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('actor_admin_id', sa.Integer(), sa.ForeignKey('admin.id', ondelete='SET NULL'), nullable=True),
        sa.Column('actor_username', sa.String(120), nullable=False),
        sa.Column('department_id', sa.Integer(), sa.ForeignKey('department.id', ondelete='SET NULL'), nullable=True),
        sa.Column('action', sa.String(80), nullable=False),
        sa.Column('target_type', sa.String(40), nullable=True),
        sa.Column('target_id', sa.Integer(), nullable=True),
        sa.Column('description', sa.String(255), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )


def downgrade():
    for table in ('activity_log', 'admin', 'attendance', 'class_session', 'student',
                  'subject', 'teacher', 'semester', 'department'):
        op.drop_table(table)
