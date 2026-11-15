"""Link a session to the routine class it holds: the entry and the date.

Revision ID: 0022_session_occurrence
Revises: 0021_routine
Create Date: 2026-11-15

"""
import sqlalchemy as sa
from alembic import op

revision = '0022_session_occurrence'
down_revision = '0021_routine'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('class_session') as batch_op:
        batch_op.add_column(sa.Column('timetable_entry_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('occurrence_date', sa.Date(), nullable=True))
        batch_op.create_foreign_key('fk_class_session_timetable_entry', 'timetable_entry', ['timetable_entry_id'],
                                    ['id'], ondelete='SET NULL')
        batch_op.create_index('uq_class_session_occurrence', ['timetable_entry_id', 'occurrence_date'], unique=True)


def downgrade():
    with op.batch_alter_table('class_session') as batch_op:
        batch_op.drop_index('uq_class_session_occurrence')
        batch_op.drop_constraint('fk_class_session_timetable_entry', type_='foreignkey')
        batch_op.drop_column('occurrence_date')
        batch_op.drop_column('timetable_entry_id')
