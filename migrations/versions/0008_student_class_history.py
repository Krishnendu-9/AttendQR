"""Remember the classes a student was moved out of, so old reports keep them.

Revision ID: 0008_student_class_history
Revises: 0007_new_device
Create Date: 2026-11-10

"""
import sqlalchemy as sa
from alembic import op

revision = '0008_student_class_history'
down_revision = '0007_new_device'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'student_class_history',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('student_id', sa.Integer(), nullable=False),
        sa.Column('department_id', sa.Integer(), nullable=False),
        sa.Column('semester_id', sa.Integer(), nullable=False),
        sa.Column('left_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['department_id'], ['department.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['semester_id'], ['semester.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['student_id'], ['student.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_student_class_history_class', 'student_class_history', ['department_id', 'semester_id'])
    op.create_index('ix_student_class_history_student_id', 'student_class_history', ['student_id'])


def downgrade():
    op.drop_index('ix_student_class_history_student_id', table_name='student_class_history')
    op.drop_index('ix_student_class_history_class', table_name='student_class_history')
    op.drop_table('student_class_history')
