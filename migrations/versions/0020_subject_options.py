"""Subject options: counts towards attendance, a default group, co-teachers; teacher short names.

Revision ID: 0020_subject_options
Revises: 0019_class_groups
Create Date: 2026-11-15

"""
import sqlalchemy as sa
from alembic import op

revision = '0020_subject_options'
down_revision = '0019_class_groups'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('subject') as batch_op:
        batch_op.add_column(sa.Column('counts_towards_attendance', sa.Boolean(), nullable=False,
                                      server_default=sa.true()))
        batch_op.add_column(sa.Column('group_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_subject_group', 'class_group', ['group_id'], ['id'], ondelete='SET NULL')
    with op.batch_alter_table('teacher') as batch_op:
        batch_op.add_column(sa.Column('short_name', sa.String(10), nullable=True))
    op.create_table(
        'subject_teacher',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('subject_id', sa.Integer(), nullable=False),
        sa.Column('teacher_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['subject_id'], ['subject.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['teacher_id'], ['teacher.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('uq_subject_teacher', 'subject_teacher', ['subject_id', 'teacher_id'], unique=True)
    op.create_index('ix_subject_teacher_teacher_id', 'subject_teacher', ['teacher_id'])


def downgrade():
    op.drop_index('ix_subject_teacher_teacher_id', table_name='subject_teacher')
    op.drop_index('uq_subject_teacher', table_name='subject_teacher')
    op.drop_table('subject_teacher')
    with op.batch_alter_table('teacher') as batch_op:
        batch_op.drop_column('short_name')
    with op.batch_alter_table('subject') as batch_op:
        batch_op.drop_constraint('fk_subject_group', type_='foreignkey')
        batch_op.drop_column('group_id')
        batch_op.drop_column('counts_towards_attendance')
