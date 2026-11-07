"""Indexes for the queries every page and report runs.

Revision ID: 0006_query_indexes
Revises: 0005_scan_grant
Create Date: 2026-11-07

"""
from alembic import op

revision = '0006_query_indexes'
down_revision = '0005_scan_grant'
branch_labels = None
depends_on = None

INDEXES = (
    ('ix_attendance_session_id', 'attendance', ['session_id']),
    ('ix_class_session_subject_id', 'class_session', ['subject_id']),
    ('ix_class_session_teacher_id', 'class_session', ['teacher_id']),
    ('ix_class_session_created_at', 'class_session', ['created_at']),
    ('ix_student_department_semester', 'student', ['department_id', 'semester_id']),
    ('ix_subject_department_semester', 'subject', ['department_id', 'semester_id']),
    ('ix_activity_log_created_at', 'activity_log', ['created_at']),
    ('ix_activity_log_actor_admin_id', 'activity_log', ['actor_admin_id']),
)


def upgrade():
    for name, table, columns in INDEXES:
        op.create_index(name, table, columns)


def downgrade():
    for name, table, _ in reversed(INDEXES):
        op.drop_index(name, table_name=table)
