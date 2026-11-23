"""One session per submitted Generate form.

A double click on Generate, or a form sent again by the browser, created a second session for
the same class. Every student who scanned only one of them was then absent in the other. The
form now carries a one-time token, kept on the session it created under a unique index, so a
second request with it opens the first session instead.

Revision ID: 0028_session_create_token
Revises: 0027_data_constraints
Create Date: 2026-11-21

"""
import sqlalchemy as sa
from alembic import op

revision = '0028_session_create_token'
down_revision = '0027_data_constraints'
branch_labels = None
depends_on = None


def upgrade():
    # A nullable column and an index need no table rebuild, even on SQLite.
    op.add_column('class_session', sa.Column('create_token', sa.String(length=32), nullable=True))
    op.create_index('uq_class_session_create_token', 'class_session', ['create_token'], unique=True)


def downgrade():
    op.drop_index('uq_class_session_create_token', table_name='class_session')
    with op.batch_alter_table('class_session') as batch_op:
        batch_op.drop_column('create_token')
