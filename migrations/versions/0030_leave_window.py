"""Approved leave keeps the classes it excused when the time zone changes.

Leave is asked for in calendar days. Which classes it excuses depends on when those days begin
and end, which depends on the college's time zone, read each time a percentage was worked
out. Changing the zone on the Settings page therefore moved approved leave by hours, and could
change past percentages. Approval now fixes the window as stored (UTC) times. Leave approved
before this migration gets the window it has today, in the zone set now.

Revision ID: 0030_leave_window
Revises: 0029_student_left
Create Date: 2026-11-21

"""
import json
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import sqlalchemy as sa
from alembic import op
from flask import current_app

revision = '0030_leave_window'
down_revision = '0029_student_left'
branch_labels = None
depends_on = None


def _zone(connection):
    """The zone the app uses now: the Settings page's choice, else TIMEZONE (as services/settings.py does)."""
    stored = connection.execute(sa.text("SELECT value FROM institution_setting WHERE key = 'timezone'")).scalar()
    name = json.loads(stored) if stored else current_app.config.get('TIMEZONE') or 'Asia/Kolkata'
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo('Asia/Kolkata')


def _day_start_utc(day, zone):
    return datetime.combine(day, time.min).replace(tzinfo=zone).astimezone(UTC).replace(tzinfo=None)


def upgrade():
    op.add_column('leave_request', sa.Column('starts_at', sa.DateTime(), nullable=True))
    op.add_column('leave_request', sa.Column('ends_at', sa.DateTime(), nullable=True))
    connection = op.get_bind()
    zone = _zone(connection)
    approved = connection.execute(sa.text(
        "SELECT id, start_date, end_date FROM leave_request WHERE status = 'approved'"
    )).all()
    # Typed, so each database gets its own datetime format (SQLite stores text).
    fill = sa.text("UPDATE leave_request SET starts_at = :starts, ends_at = :ends WHERE id = :id").bindparams(
        sa.bindparam('starts', type_=sa.DateTime()), sa.bindparam('ends', type_=sa.DateTime()),
    )
    for leave_id, start, end in approved:
        start = start if not isinstance(start, str) else datetime.strptime(start, '%Y-%m-%d').date()
        end = end if not isinstance(end, str) else datetime.strptime(end, '%Y-%m-%d').date()
        connection.execute(fill, {'starts': _day_start_utc(start, zone),
                                  'ends': _day_start_utc(end + timedelta(days=1), zone), 'id': leave_id})


def downgrade():
    with op.batch_alter_table('leave_request') as batch_op:
        batch_op.drop_column('ends_at')
        batch_op.drop_column('starts_at')
