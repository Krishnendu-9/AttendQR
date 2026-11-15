"""Keep a locked semester's exam eligibility percentages, so later settings changes leave it alone.

Semesters locked before this migration have none stored; they use the current settings
until they are unlocked and locked again.

Revision ID: 0018_eligibility_at_lock
Revises: 0017_institution_settings
Create Date: 2026-11-15

"""
import sqlalchemy as sa
from alembic import op

revision = '0018_eligibility_at_lock'
down_revision = '0017_institution_settings'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('semester') as batch_op:
        batch_op.add_column(sa.Column('locked_eligibility_percent', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('locked_condonation_percent', sa.Integer(), nullable=True))


def downgrade():
    with op.batch_alter_table('semester') as batch_op:
        batch_op.drop_column('locked_condonation_percent')
        batch_op.drop_column('locked_eligibility_percent')
