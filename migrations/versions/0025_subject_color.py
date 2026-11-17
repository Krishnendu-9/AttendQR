"""A colour for each subject in the routine. NULL (every existing subject) means automatic.

Revision ID: 0025_subject_color
Revises: 0024_class_joined_at
Create Date: 2026-11-16

"""
import sqlalchemy as sa
from alembic import op

revision = '0025_subject_color'
down_revision = '0024_class_joined_at'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('subject') as batch_op:
        batch_op.add_column(sa.Column('color', sa.String(length=12), nullable=True))


def downgrade():
    with op.batch_alter_table('subject') as batch_op:
        batch_op.drop_column('color')
