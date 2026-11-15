"""Record when a student joined their class, so sessions held before then are not theirs.

student.class_joined_at is set when a student is added or moved into a class, and
student_class_history.joined_at keeps it for a class they later left. NULL means "from the
start": every student and history row from before this migration keeps counting exactly
as before.

Revision ID: 0024_class_joined_at
Revises: 0023_routine_changes
Create Date: 2026-11-15

"""
import sqlalchemy as sa
from alembic import op

revision = '0024_class_joined_at'
down_revision = '0023_routine_changes'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('student') as batch_op:
        batch_op.add_column(sa.Column('class_joined_at', sa.DateTime(), nullable=True))
    with op.batch_alter_table('student_class_history') as batch_op:
        batch_op.add_column(sa.Column('joined_at', sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table('student_class_history') as batch_op:
        batch_op.drop_column('joined_at')
    with op.batch_alter_table('student') as batch_op:
        batch_op.drop_column('class_joined_at')
