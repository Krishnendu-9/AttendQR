"""Groups within a class (lab batches, electives), and sessions for one group.

Revision ID: 0019_class_groups
Revises: 0018_eligibility_at_lock
Create Date: 2026-11-15

"""
import sqlalchemy as sa
from alembic import op

revision = '0019_class_groups'
down_revision = '0018_eligibility_at_lock'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'class_group',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('department_id', sa.Integer(), nullable=False),
        sa.Column('semester_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(60), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['department_id'], ['department.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['semester_id'], ['semester.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_class_group_semester_id', 'class_group', ['semester_id'])
    op.create_table(
        'class_group_member',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('group_id', sa.Integer(), nullable=False),
        sa.Column('student_id', sa.Integer(), nullable=False),
        sa.Column('joined_at', sa.DateTime(), nullable=False),
        sa.Column('left_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['group_id'], ['class_group.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['student_id'], ['student.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_class_group_member_group_student', 'class_group_member', ['group_id', 'student_id'])
    op.create_index('ix_class_group_member_student_id', 'class_group_member', ['student_id'])
    with op.batch_alter_table('class_session') as batch_op:
        batch_op.add_column(sa.Column('group_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_class_session_group', 'class_group', ['group_id'], ['id'])
        batch_op.create_index('ix_class_session_group_id', ['group_id'])


def downgrade():
    with op.batch_alter_table('class_session') as batch_op:
        batch_op.drop_index('ix_class_session_group_id')
        batch_op.drop_constraint('fk_class_session_group', type_='foreignkey')
        batch_op.drop_column('group_id')
    op.drop_index('ix_class_group_member_student_id', table_name='class_group_member')
    op.drop_index('ix_class_group_member_group_student', table_name='class_group_member')
    op.drop_table('class_group_member')
    op.drop_index('ix_class_group_semester_id', table_name='class_group')
    op.drop_table('class_group')
