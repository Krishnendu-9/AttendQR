"""Store timestamps in UTC instead of India Standard Time.

Earlier versions stored naive IST (UTC+05:30) datetimes. From now on every
timestamp is naive UTC, and only display uses the college's time zone (TIMEZONE).
IST has had no daylight saving since 1945, so a fixed 5:30 shift is exact.

Revision ID: 0010_utc_timestamps
Revises: 0009_user_account
Create Date: 2026-11-13

"""
from datetime import timedelta

import sqlalchemy as sa
from alembic import op

revision = '0010_utc_timestamps'
down_revision = '0009_user_account'
branch_labels = None
depends_on = None

IST_OFFSET = timedelta(hours=5, minutes=30)

TIMESTAMP_COLUMNS = {
    'class_session': ('created_at', 'expires_at'),
    'attendance': ('timestamp',),
    'activity_log': ('created_at',),
    'login_throttle': ('window_started_at', 'locked_until'),
    'student_class_history': ('left_at',),
}


def _shift(delta):
    bind = op.get_bind()
    for table_name, columns in TIMESTAMP_COLUMNS.items():
        table = sa.table(table_name, sa.column('id', sa.Integer()), *(sa.column(c, sa.DateTime()) for c in columns))
        rows = bind.execute(sa.select(table.c.id, *(table.c[c] for c in columns))).fetchall()
        for row in rows:
            values = {c: row._mapping[c] + delta for c in columns if row._mapping[c] is not None}
            if values:
                bind.execute(sa.update(table).where(table.c.id == row.id).values(**values))


def upgrade():
    _shift(-IST_OFFSET)


def downgrade():
    _shift(IST_OFFSET)
