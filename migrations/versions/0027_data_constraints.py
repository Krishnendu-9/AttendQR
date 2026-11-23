"""Constraints the app already keeps, now kept by the database too, and one stored case for logins.

- CHECK constraints for the fixed choices (roles, attendance sources, request and check
  statuses, routine kinds and week patterns), weekdays from 0 to 6, and dates and times that
  end after they start. The app validates all of these first; the constraints stop a script
  or a hand-made change from storing what the app would misread.
- Group names are unique within a semester ignoring case, as the app already checks.
- Login names and student and teacher emails are stored lower case, and roll numbers upper
  case, as the app has always saved them. A value from an older version that differs is
  changed unless that would clash with another row, so lookups can compare stored values
  directly and use their unique indexes.

The upgrade stops with a list of what to fix if existing rows break a constraint, rather
than store a database that the next version would refuse.

Revision ID: 0027_data_constraints
Revises: 0026_activity_log_text
Create Date: 2026-11-21

"""
import sqlalchemy as sa
from alembic import op

revision = '0027_data_constraints'
down_revision = '0026_activity_log_text'
branch_labels = None
depends_on = None


def _one_of(column, values):
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


# table: [(constraint name, condition)]
CHECKS = {
    'user_account': [('ck_user_account_role', _one_of('role', ('principal', 'hod', 'teacher', 'student')))],
    'attendance': [('ck_attendance_source', _one_of('source', ('scan', 'manual')))],
    'leave_request': [
        ('ck_leave_request_status', _one_of('status', ('pending', 'approved', 'rejected'))),
        ('ck_leave_request_dates', 'end_date >= start_date'),
    ],
    'spot_check': [('ck_spot_check_result', _one_of('result', ('pending', 'present', 'absent')))],
    'bell_period': [('ck_bell_period_times', 'end_time > start_time')],
    'bell_schedule_day': [('ck_bell_schedule_day_weekday', 'weekday BETWEEN 0 AND 6')],
    'holiday': [('ck_holiday_dates', 'end_date >= start_date')],
    'timetable': [('ck_timetable_status', _one_of('status', ('draft', 'published')))],
    'timetable_entry': [
        ('ck_timetable_entry_weekday', 'weekday BETWEEN 0 AND 6'),
        ('ck_timetable_entry_times', 'end_time > start_time'),
        ('ck_timetable_entry_week_pattern', _one_of('week_pattern', ('every', 'odd', 'even'))),
    ],
    'routine_change': [('ck_routine_change_kind', _one_of('kind', ('cancel', 'move', 'substitute', 'extra')))],
}

# (table, column, SQL function, extra columns the value is unique together with)
STORED_CASE = [
    ('user_account', 'username', 'LOWER', ()),
    ('student', 'email', 'LOWER', ()),
    ('teacher', 'email', 'LOWER', ()),
    ('student', 'roll_no', 'UPPER', ('department_id', 'semester_id')),
]


def _normalise_case(connection):
    for table, column, function, scope in STORED_CASE:
        # Only where no other row has the same value in that case: two accounts "A@x" and "a@x" are
        # left as they are rather than merged or refused, as the unique index would otherwise do.
        same_scope = ''.join(f" AND other.{col} = {table}.{col}" for col in scope)
        # The names come from STORED_CASE above, never from input.
        clash = (f"SELECT 1 FROM {table} other WHERE {function}(other.{column}) = {function}({table}.{column}) "  # noqa: S608
                 f"AND other.id <> {table}.id{same_scope}")
        update = f"UPDATE {table} SET {column} = {function}({column}) WHERE {column} <> {function}({column})"  # noqa: S608
        connection.execute(sa.text(f"{update} AND NOT EXISTS ({clash})"))


def _violations(connection):
    problems = []
    for table, checks in CHECKS.items():
        for name, condition in checks:
            query = f"SELECT COUNT(*) FROM {table} WHERE NOT ({condition})"  # noqa: S608 - from CHECKS above
            count = connection.execute(sa.text(query)).scalar()
            if count:
                problems.append(f"{count} row(s) in {table} break {name} ({condition})")
    duplicates = connection.execute(sa.text(
        "SELECT COUNT(*) FROM (SELECT semester_id, LOWER(name) AS key FROM class_group "
        "GROUP BY semester_id, LOWER(name) HAVING COUNT(*) > 1) AS clashes"
    )).scalar()
    if duplicates:
        problems.append(f"{duplicates} group name(s) are used twice in one semester (ignoring case)")
    return problems


def upgrade():
    connection = op.get_bind()
    _normalise_case(connection)
    problems = _violations(connection)
    if problems:
        raise RuntimeError("Cannot add the data constraints until these rows are corrected: " + '; '.join(problems) + '.')

    for table, checks in CHECKS.items():
        with op.batch_alter_table(table) as batch_op:
            for name, condition in checks:
                batch_op.create_check_constraint(name, condition)
    op.create_index('uq_class_group_semester_name', 'class_group', ['semester_id', sa.text('lower(name)')], unique=True)


def downgrade():
    op.drop_index('uq_class_group_semester_name', table_name='class_group')
    for table, checks in CHECKS.items():
        with op.batch_alter_table(table) as batch_op:
            for name, _ in checks:
                batch_op.drop_constraint(name, type_='check')
    # The stored case stays: every version of the app saves values that way.
