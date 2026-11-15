"""Encrypt stored two-step secrets, and drop class_session.token.

Two-step secrets were stored as they were, so a copy of the database could generate
codes. They are now encrypted with Fernet under a key derived from SECRET_KEY (see
services/two_factor.py; the derivation is repeated here so this migration never depends
on later application code). The columns grow to hold the encrypted form.

class_session.token was a random identifier from the first version, when the QR
code carried it. QR codes now carry signed, expiring tokens and nothing reads it.

Revision ID: 0016_encrypt_totp_secrets
Revises: 0015_condonation
Create Date: 2026-11-14

"""
import base64
import hashlib
import hmac
import uuid

import sqlalchemy as sa
from alembic import op
from cryptography.fernet import Fernet, InvalidToken
from flask import current_app

revision = '0016_encrypt_totp_secrets'
down_revision = '0015_condonation'
branch_labels = None
depends_on = None

SECRET_KEY_PURPOSE = b'attendqr-totp-secrets'

user_account = sa.table(
    'user_account',
    sa.column('id', sa.Integer),
    sa.column('totp_secret', sa.String),
    sa.column('totp_pending_secret', sa.String),
)
class_session = sa.table('class_session', sa.column('id', sa.Integer), sa.column('token', sa.String))


def _fernet():
    key = hmac.new(str(current_app.config['SECRET_KEY']).encode(), SECRET_KEY_PURPOSE, hashlib.sha256).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def _rewrite_secrets(transform):
    connection = op.get_bind()
    rows = connection.execute(
        sa.select(user_account.c.id, user_account.c.totp_secret, user_account.c.totp_pending_secret).where(
            sa.or_(user_account.c.totp_secret.isnot(None), user_account.c.totp_pending_secret.isnot(None))
        )
    ).fetchall()
    for user_id, secret, pending in rows:
        connection.execute(
            user_account.update().where(user_account.c.id == user_id).values(
                totp_secret=transform(secret) if secret else None,
                totp_pending_secret=transform(pending) if pending else None,
            )
        )


def upgrade():
    with op.batch_alter_table('user_account') as batch:
        batch.alter_column('totp_secret', existing_type=sa.String(64), type_=sa.String(255), existing_nullable=True)
        batch.alter_column('totp_pending_secret', existing_type=sa.String(64), type_=sa.String(255),
                           existing_nullable=True)
    fernet = _fernet()
    _rewrite_secrets(lambda value: fernet.encrypt(value.encode()).decode('ascii'))

    with op.batch_alter_table('class_session') as batch:
        batch.drop_column('token')


def downgrade():
    with op.batch_alter_table('class_session') as batch:
        batch.add_column(sa.Column('token', sa.String(100), nullable=True))
    connection = op.get_bind()
    for (session_id,) in connection.execute(sa.select(class_session.c.id)).fetchall():
        connection.execute(class_session.update().where(class_session.c.id == session_id).values(token=uuid.uuid4().hex))
    with op.batch_alter_table('class_session') as batch:
        batch.alter_column('token', existing_type=sa.String(100), nullable=False)
        batch.create_unique_constraint('class_session_token_key', ['token'])

    fernet = _fernet()

    def decrypt(value):
        try:
            return fernet.decrypt(value.encode()).decode()
        except InvalidToken:
            return None  # unreadable under this SECRET_KEY; that account must set up two-step sign-in again

    _rewrite_secrets(decrypt)
    with op.batch_alter_table('user_account') as batch:
        batch.alter_column('totp_secret', existing_type=sa.String(255), type_=sa.String(64), existing_nullable=True)
        batch.alter_column('totp_pending_secret', existing_type=sa.String(255), type_=sa.String(64),
                           existing_nullable=True)
