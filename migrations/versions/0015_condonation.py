"""Condonations: students in the condonation band allowed to sit the exams, with a reason.

Revision ID: 0015_condonation
Revises: 0014_two_factor
Create Date: 2026-11-14

"""
import sqlalchemy as sa
from alembic import op

revision = '0015_condonation'
down_revision = '0014_two_factor'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'condonation',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('student_id', sa.Integer(), nullable=False),
        sa.Column('semester_id', sa.Integer(), nullable=False),
        sa.Column('reason', sa.String(200), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('granted_by_user_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['student_id'], ['student.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['semester_id'], ['semester.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['granted_by_user_id'], ['user_account.id'], ondelete='SET NULL',
                                name='fk_condonation_granted_by_user'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('uq_condonation_student_semester', 'condonation', ['student_id', 'semester_id'], unique=True)


def downgrade():
    op.drop_index('uq_condonation_student_semester', table_name='condonation')
    op.drop_table('condonation')
