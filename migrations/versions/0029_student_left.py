"""Students who left the college: when, and why.

A student who leaves (graduates, drops out, transfers) used to stay a current student or be
deleted, which erased their records. Marking them as left ends their time in their class at
that moment, like moving out of it, and keeps every record. Existing students are all current.

Revision ID: 0029_student_left
Revises: 0028_session_create_token
Create Date: 2026-11-21

"""
import sqlalchemy as sa
from alembic import op

revision = '0029_student_left'
down_revision = '0028_session_create_token'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('student', sa.Column('left_at', sa.DateTime(), nullable=True))
    op.add_column('student', sa.Column('left_reason', sa.String(length=200), nullable=True))


def downgrade():
    # Students marked as left become current again: the older app has no other way to show them.
    with op.batch_alter_table('student') as batch_op:
        batch_op.drop_column('left_reason')
        batch_op.drop_column('left_at')
