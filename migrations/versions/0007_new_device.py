"""Flag attendance entries whose scan registered the student's phone.

Revision ID: 0007_new_device
Revises: 0006_query_indexes
Create Date: 2026-11-07

"""
import sqlalchemy as sa
from alembic import op

revision = '0007_new_device'
down_revision = '0006_query_indexes'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('attendance') as batch:
        batch.add_column(sa.Column('new_device', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    with op.batch_alter_table('attendance') as batch:
        batch.drop_column('new_device')
