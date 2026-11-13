"""Two-step sign-in with an authenticator app (TOTP) and recovery codes.

Revision ID: 0014_two_factor
Revises: 0013_audit_chain_and_lock
Create Date: 2026-11-14

"""
import sqlalchemy as sa
from alembic import op

revision = '0014_two_factor'
down_revision = '0013_audit_chain_and_lock'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('user_account') as batch:
        batch.add_column(sa.Column('totp_secret', sa.String(64), nullable=True))
        batch.add_column(sa.Column('totp_pending_secret', sa.String(64), nullable=True))
        batch.add_column(sa.Column('totp_enabled_at', sa.DateTime(), nullable=True))
        batch.add_column(sa.Column('totp_last_step', sa.Integer(), nullable=True))
        batch.add_column(sa.Column('recovery_codes', sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table('user_account') as batch:
        batch.drop_column('recovery_codes')
        batch.drop_column('totp_last_step')
        batch.drop_column('totp_enabled_at')
        batch.drop_column('totp_pending_secret')
        batch.drop_column('totp_secret')
