"""Institution settings: options the Principal changes in the app instead of the environment.

Revision ID: 0017_institution_settings
Revises: 0016_encrypt_totp_secrets
Create Date: 2026-11-15

"""
import sqlalchemy as sa
from alembic import op

revision = '0017_institution_settings'
down_revision = '0016_encrypt_totp_secrets'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'institution_setting',
        sa.Column('key', sa.String(80), nullable=False),
        sa.Column('value', sa.Text(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('updated_by_user_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['updated_by_user_id'], ['user_account.id'], ondelete='SET NULL',
                                name='fk_institution_setting_updated_by_user'),
        sa.PrimaryKeyConstraint('key'),
    )


def downgrade():
    op.drop_table('institution_setting')
