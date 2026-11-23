"""Upgrading databases created before migrations existed."""
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory

from attendqr import create_app

MIGRATIONS = Path(__file__).resolve().parents[1] / 'migrations'


def head_revision():
    config = Config()
    config.set_main_option('script_location', str(MIGRATIONS))
    return ScriptDirectory.from_config(config).get_current_head()

# Schema produced by the pre-migration app (create_all followed by its ALTER TABLE helpers).
LEGACY_SCHEMA = """
CREATE TABLE department (id INTEGER NOT NULL, name VARCHAR(100) NOT NULL, PRIMARY KEY (id), UNIQUE (name));
CREATE TABLE semester (id INTEGER NOT NULL, name VARCHAR(50) NOT NULL, department_id INTEGER NOT NULL,
    PRIMARY KEY (id), FOREIGN KEY(department_id) REFERENCES department (id) ON DELETE CASCADE);
CREATE TABLE teacher (id INTEGER NOT NULL, name VARCHAR(100) NOT NULL, email VARCHAR(120) NOT NULL,
    department_id INTEGER NOT NULL, PRIMARY KEY (id), UNIQUE (email),
    FOREIGN KEY(department_id) REFERENCES department (id) ON DELETE CASCADE);
CREATE TABLE subject (id INTEGER NOT NULL, name VARCHAR(100) NOT NULL, department_id INTEGER NOT NULL,
    semester_id INTEGER NOT NULL, PRIMARY KEY (id),
    FOREIGN KEY(department_id) REFERENCES department (id) ON DELETE CASCADE,
    FOREIGN KEY(semester_id) REFERENCES semester (id) ON DELETE CASCADE);
CREATE TABLE student (id INTEGER NOT NULL, name VARCHAR(100) NOT NULL, roll_no VARCHAR(20) NOT NULL,
    email VARCHAR(120) NOT NULL, department_id INTEGER NOT NULL, semester_id INTEGER NOT NULL, device_id VARCHAR(100),
    PRIMARY KEY (id), CONSTRAINT unique_roll_dept_sem UNIQUE (roll_no, department_id, semester_id), UNIQUE (email),
    FOREIGN KEY(department_id) REFERENCES department (id) ON DELETE CASCADE,
    FOREIGN KEY(semester_id) REFERENCES semester (id) ON DELETE CASCADE);
CREATE TABLE class_session (id INTEGER NOT NULL, subject_id INTEGER NOT NULL, teacher_id INTEGER,
    token VARCHAR(100) NOT NULL, created_at DATETIME, expires_at DATETIME NOT NULL, PRIMARY KEY (id),
    FOREIGN KEY(subject_id) REFERENCES subject (id) ON DELETE CASCADE,
    FOREIGN KEY(teacher_id) REFERENCES teacher (id) ON DELETE SET NULL, UNIQUE (token));
CREATE TABLE attendance (id INTEGER NOT NULL, student_id INTEGER NOT NULL, session_id INTEGER NOT NULL,
    timestamp DATETIME, PRIMARY KEY (id),
    CONSTRAINT unique_student_session_attendance UNIQUE (student_id, session_id),
    FOREIGN KEY(student_id) REFERENCES student (id) ON DELETE CASCADE,
    FOREIGN KEY(session_id) REFERENCES class_session (id) ON DELETE CASCADE);
CREATE TABLE admin (id INTEGER NOT NULL, username VARCHAR(80) NOT NULL, password VARCHAR(200) NOT NULL,
    PRIMARY KEY (id), UNIQUE (username));
CREATE TABLE activity_log (id INTEGER NOT NULL, actor_admin_id INTEGER, actor_username VARCHAR(120) NOT NULL,
    department_id INTEGER, action VARCHAR(80) NOT NULL, target_type VARCHAR(40), target_id INTEGER,
    description VARCHAR(255) NOT NULL, created_at DATETIME NOT NULL, PRIMARY KEY (id),
    FOREIGN KEY(actor_admin_id) REFERENCES admin (id) ON DELETE SET NULL,
    FOREIGN KEY(department_id) REFERENCES department (id) ON DELETE SET NULL);

INSERT INTO department VALUES (1, 'Master of Computer Application(MCA)');
INSERT INTO semester VALUES (1, 'Semester 1', 1);
INSERT INTO teacher VALUES (1, 'Asha', 'asha@college.example', 1);
INSERT INTO subject VALUES (1, 'DBMS', 1, 1);
INSERT INTO student VALUES (1, 'A', 'R1', 'a@example.com', 1, 1, 'shared-phone');
INSERT INTO student VALUES (2, 'B', 'R2', 'b@example.com', 1, 1, 'shared-phone');
INSERT INTO student VALUES (3, 'C', 'R3', 'c@example.com', 1, 1, 'own-phone');
INSERT INTO class_session VALUES (1, 1, 1, 'tok', '2026-01-01 10:00:00', '2026-01-01 10:30:00');
INSERT INTO attendance VALUES (1, 1, 1, '2026-01-01 10:05:00');
INSERT INTO admin VALUES (1, 'admin', 'x');
INSERT INTO admin VALUES (2, 'principal2@gmail.com', 'x');
INSERT INTO activity_log VALUES (1, 1, 'admin', 1, 'x', NULL, NULL, 'kept', '2026-01-01 10:00:00');
INSERT INTO activity_log VALUES (2, 2, 'principal2@gmail.com', 1, 'x', NULL, NULL, 'kept', '2026-01-01 10:00:00');
"""


def test_legacy_database_is_upgraded_without_losing_data(tmp_path, initial_password_file):
    path = tmp_path / 'legacy.db'
    # sqlite3's own `with` only commits; closing() closes the file as well.
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(LEGACY_SCHEMA)

    app = create_app({
        'TESTING': True,
        'SECRET_KEY': 'legacy-test-secret-key',
        'SQLALCHEMY_DATABASE_URI': f"sqlite:///{path.as_posix()}",
        'DEFAULT_PRINCIPAL_EMAIL': 'principal@example.com',
    })
    with app.app_context():
        from attendqr.extensions import db
        db.engine.dispose()

    conn = sqlite3.connect(path)
    try:
        assert conn.execute('SELECT version_num FROM alembic_version').fetchone()[0] == head_revision()

        # Table rebuilds must not trigger ON DELETE SET NULL on child rows.
        assert conn.execute('SELECT actor_user_id FROM activity_log ORDER BY id').fetchall() == [(1,), (2,)]
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []

        account_columns = {row[1] for row in conn.execute('PRAGMA table_info(user_account)')}
        assert {'role', 'is_active', 'must_change_password', 'session_token'} <= account_columns
        assert not {'failed_login_attempts', 'locked_until'} & account_columns
        assert conn.execute('SELECT COUNT(*) FROM user_account WHERE session_token IS NULL').fetchone()[0] == 0

        # The legacy "admin" login is renamed and must pick a new password.
        assert conn.execute("SELECT username, must_change_password FROM user_account WHERE id = 1").fetchone() == ('principal@example.com', 1)
        # ...starting from a generated password, not a published one.
        assert initial_password_file.exists()

        # Legacy staff accounts were Principals, stored as 'super_admin' before 0009.
        assert dict(conn.execute("SELECT id, role FROM user_account WHERE role != 'student'")) == {1: 'principal', 2: 'principal'}
        assert 'admin' not in {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}

        # A phone shared by two students is cleared; a phone used by one student is kept.
        assert dict(conn.execute('SELECT id, device_id FROM student')) == {1: None, 2: None, 3: 'own-phone'}

        # Exactly one uniqueness rule on attendance, and the data is intact.
        attendance_sql = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'attendance'").fetchone()[0]
        assert 'unique_student_session_attendance' not in attendance_sql
        indexes = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='attendance'")}
        assert 'uq_attendance_student_session' in indexes
        assert conn.execute('SELECT COUNT(*) FROM attendance').fetchone()[0] == 1
        # Existing attendance was recorded by QR scan.
        assert conn.execute('SELECT source, marked_by_user_id FROM attendance').fetchone() == ('scan', None)

        # Startup data fixes ran once: department name normalised and branch inferred, student logins created.
        assert conn.execute('SELECT name, branch FROM department').fetchone() == ('Master of Computer Application (MCA)', 'MCA')
        assert conn.execute("SELECT COUNT(*) FROM user_account WHERE role = 'student'").fetchone()[0] == 3
        assert conn.execute('SELECT is_active FROM class_session').fetchone()[0] == 1
    finally:
        conn.close()


def test_upgrading_twice_is_a_no_op(tmp_path):
    uri = f"sqlite:///{(tmp_path / 'twice.db').as_posix()}"
    for _ in range(2):
        app = create_app({'TESTING': True, 'SECRET_KEY': 'twice-secret', 'SQLALCHEMY_DATABASE_URI': uri})
        with app.app_context():
            from attendqr.extensions import db
            from attendqr.models import User
            assert User.query.count() == 1
            db.session.remove()  # hand the connection back first, or dispose() orphans it unclosed
            db.engine.dispose()


@pytest.mark.sqlite_only
def test_sqlite_uses_write_ahead_logging_and_waits_for_locks(app):
    from sqlalchemy import text

    from attendqr.extensions import SQLITE_BUSY_TIMEOUT_MS, db

    with db.engine.connect() as connection:
        assert connection.execute(text('PRAGMA journal_mode')).scalar() == 'wal'
        assert connection.execute(text('PRAGMA busy_timeout')).scalar() == SQLITE_BUSY_TIMEOUT_MS
        assert connection.execute(text('PRAGMA foreign_keys')).scalar() == 1


def _foreign_keys(connection, table):
    """{column: (referenced table, on delete)} as the database itself reports them."""
    from sqlalchemy import inspect

    return {
        fk['constrained_columns'][0]: (fk['referred_table'], (fk['options'].get('ondelete') or '').upper())
        for fk in inspect(connection).get_foreign_keys(table)
    }


def test_account_foreign_keys_point_at_user_account(app):
    from attendqr.extensions import db

    with db.engine.connect() as connection:
        assert _foreign_keys(connection, 'attendance')['marked_by_user_id'] == ('user_account', 'SET NULL')
        assert _foreign_keys(connection, 'activity_log')['actor_user_id'] == ('user_account', 'SET NULL')


def test_user_account_migration_round_trip_keeps_data(app, data):
    """0009 down and up again: roles, references and activity targets survive both ways."""
    from flask_migrate import downgrade, upgrade

    from attendqr.extensions import db
    from attendqr.models import ActivityLog, Attendance, User
    from conftest import add_session

    teacher = User.query.filter_by(username=data.teacher).one()
    teacher_id = teacher.id
    class_session = add_session(data)
    db.session.add(Attendance(student_id=data.student_id, session_id=class_session.id,
                              source='manual', marked_by_user_id=teacher.id, note='n'))
    db.session.add(ActivityLog(actor_user_id=teacher.id, actor_username=teacher.username, action='x',
                               target_type='user', target_id=teacher.id, description='d', created_at=class_session.created_at))
    db.session.commit()
    db.session.remove()

    downgrade(revision='0008_student_class_history')
    with db.engine.connect() as connection:
        assert connection.exec_driver_sql(
            "SELECT role FROM admin WHERE username = 'principal@example.com'").scalar() == 'super_admin'
        assert connection.exec_driver_sql('SELECT marked_by_admin_id FROM attendance').scalar() == teacher_id
        assert connection.exec_driver_sql("SELECT target_type FROM activity_log WHERE action = 'x'").scalar() == 'admin'
        assert _foreign_keys(connection, 'attendance')['marked_by_admin_id'] == ('admin', 'SET NULL')

    upgrade()
    with db.engine.connect() as connection:
        assert connection.exec_driver_sql(
            "SELECT role FROM user_account WHERE username = 'principal@example.com'").scalar() == 'principal'
        assert connection.exec_driver_sql('SELECT marked_by_user_id FROM attendance').scalar() == teacher_id
        assert connection.exec_driver_sql("SELECT target_type FROM activity_log WHERE action = 'x'").scalar() == 'user'
        assert _foreign_keys(connection, 'attendance')['marked_by_user_id'] == ('user_account', 'SET NULL')


def test_utc_migration_shifts_stored_ist_times(app, data):
    """0010 turns stored IST into UTC and back, and leaves empty values alone."""
    from datetime import datetime

    from flask_migrate import downgrade, upgrade

    from attendqr.extensions import db
    from attendqr.models import ClassSession

    class_session = ClassSession(subject_id=data.subject_id, created_at=datetime(2026, 1, 1, 4, 30),
                                 expires_at=datetime(2026, 1, 1, 5, 0))
    db.session.add(class_session)
    db.session.commit()
    session_id = class_session.id
    db.session.remove()

    def stored():
        with db.engine.connect() as connection:
            row = connection.execute(sa.text("SELECT created_at, expires_at FROM class_session WHERE id = :id"),
                                     {'id': session_id}).fetchone()
        return [str(value)[:16] for value in row]

    downgrade(revision='0009_user_account')
    assert stored() == ['2026-01-01 10:00', '2026-01-01 10:30']  # as earlier versions stored it (IST)
    upgrade()
    assert stored() == ['2026-01-01 04:30', '2026-01-01 05:00']


def test_two_step_secret_migration_encrypts_and_restores(app, data):
    """0016 encrypts secrets stored as they were, and drops class_session.token; down restores both."""
    from flask_migrate import downgrade, upgrade

    from attendqr.extensions import db
    from attendqr.models import User
    from attendqr.services import two_factor
    from conftest import add_session

    add_session(data)
    db.session.commit()
    db.session.remove()
    downgrade(revision='0015_condonation')
    with db.engine.begin() as connection:
        assert connection.exec_driver_sql('SELECT token FROM class_session').scalar()  # refilled on the way down
        connection.exec_driver_sql(
            "UPDATE user_account SET totp_secret = 'JBSWY3DPEHPK3PXP' WHERE username = 'principal@example.com'")

    upgrade()
    with db.engine.connect() as connection:
        stored = connection.exec_driver_sql(
            "SELECT totp_secret FROM user_account WHERE username = 'principal@example.com'").scalar()
        columns = {column['name'] for column in sa.inspect(connection).get_columns('class_session')}
    assert stored != 'JBSWY3DPEHPK3PXP' and two_factor.decrypt_secret(stored) == 'JBSWY3DPEHPK3PXP'
    assert 'token' not in columns
    assert two_factor.decrypt_secret(User.query.filter_by(username='principal@example.com').one().totp_secret)
    db.session.remove()  # end the read transaction; on PostgreSQL it would block the ALTER TABLE below

    downgrade(revision='0015_condonation')
    with db.engine.connect() as connection:
        assert connection.exec_driver_sql(
            "SELECT totp_secret FROM user_account WHERE username = 'principal@example.com'").scalar() == 'JBSWY3DPEHPK3PXP'
    upgrade()


def test_institution_settings_migration_round_trip(app, data):
    """0017 adds institution_setting; down drops it and nothing else, up brings it back empty."""
    from flask_migrate import downgrade, upgrade

    from attendqr.extensions import db
    from attendqr.models import InstitutionSetting, User

    principal_id = User.query.filter_by(username=data.principal).one().id
    db.session.add(InstitutionSetting(key='attendance_target', value='70', updated_by_user_id=principal_id))
    db.session.commit()
    accounts = User.query.count()
    db.session.remove()

    downgrade(revision='0016_encrypt_totp_secrets')
    with db.engine.connect() as connection:
        assert 'institution_setting' not in sa.inspect(connection).get_table_names()
        assert connection.exec_driver_sql('SELECT COUNT(*) FROM user_account').scalar() == accounts

    upgrade()
    with db.engine.connect() as connection:
        assert _foreign_keys(connection, 'institution_setting')['updated_by_user_id'] == ('user_account', 'SET NULL')
        assert connection.exec_driver_sql('SELECT COUNT(*) FROM institution_setting').scalar() == 0


def test_eligibility_at_lock_migration_round_trip(app, data):
    """0018 adds the two percentages to semester; down drops them and keeps the lock itself."""
    from flask_migrate import downgrade, upgrade

    from attendqr.extensions import db
    from attendqr.models import Semester
    from attendqr.utils import utc_now

    semester = db.session.get(Semester, data.sem1_id)
    semester.attendance_locked_at = utc_now()
    semester.locked_eligibility_percent, semester.locked_condonation_percent = 75, 65
    db.session.commit()
    db.session.remove()

    downgrade(revision='0017_institution_settings')
    with db.engine.connect() as connection:
        columns = {column['name'] for column in sa.inspect(connection).get_columns('semester')}
        assert 'locked_eligibility_percent' not in columns
        assert connection.execute(sa.text('SELECT attendance_locked_at FROM semester WHERE id = :id'),
                                  {'id': data.sem1_id}).scalar() is not None

    upgrade()
    with db.engine.connect() as connection:
        assert connection.execute(sa.text('SELECT locked_eligibility_percent FROM semester WHERE id = :id'),
                                  {'id': data.sem1_id}).scalar() is None


def test_class_groups_migration_round_trip(app, data):
    """0019 adds groups, memberships and class_session.group_id; down removes them and keeps the sessions."""
    from flask_migrate import downgrade, upgrade

    from attendqr.extensions import db
    from attendqr.models import ClassGroup, ClassGroupMember
    from conftest import add_session

    group = ClassGroup(department_id=data.mca_id, semester_id=data.sem1_id, name='Gr-1')
    db.session.add(group)
    db.session.flush()
    db.session.add(ClassGroupMember(group_id=group.id, student_id=data.student_id))
    db.session.commit()
    add_session(data, group_id=group.id)
    add_session(data)
    db.session.remove()

    downgrade(revision='0018_eligibility_at_lock')
    with db.engine.connect() as connection:
        tables = sa.inspect(connection).get_table_names()
        columns = {column['name'] for column in sa.inspect(connection).get_columns('class_session')}
        assert 'class_group' not in tables and 'class_group_member' not in tables and 'group_id' not in columns
        assert connection.exec_driver_sql('SELECT COUNT(*) FROM class_session').scalar() == 2

    upgrade()
    with db.engine.connect() as connection:
        assert _foreign_keys(connection, 'class_session')['group_id'][0] == 'class_group'
        assert connection.exec_driver_sql('SELECT COUNT(*) FROM class_group').scalar() == 0


def test_subject_options_migration_round_trip(app, data):
    """0020 adds counts_towards_attendance (on for existing subjects), subject.group_id, co-teachers and short names."""
    from flask_migrate import downgrade, upgrade

    from attendqr.extensions import db
    from attendqr.models import Subject, Teacher

    subject = db.session.get(Subject, data.subject_id)
    subject.counts_towards_attendance = False
    subject.co_teachers = [Teacher.query.filter_by(email='ravi@college.example').one()]
    db.session.commit()
    db.session.remove()

    downgrade(revision='0019_class_groups')
    with db.engine.connect() as connection:
        assert 'subject_teacher' not in sa.inspect(connection).get_table_names()
        columns = {column['name'] for column in sa.inspect(connection).get_columns('subject')}
        assert 'counts_towards_attendance' not in columns and 'group_id' not in columns
        assert connection.exec_driver_sql('SELECT COUNT(*) FROM subject').scalar() == 2

    upgrade()
    with db.engine.connect() as connection:
        counted = connection.exec_driver_sql('SELECT counts_towards_attendance FROM subject').scalars().all()
        assert all(counted) and len(counted) == 2  # existing subjects count, as before
        assert _foreign_keys(connection, 'subject_teacher')['teacher_id'] == ('teacher', 'CASCADE')


def test_routine_migration_round_trip(app, data):
    """0021 adds bell schedules, holidays and timetables; down drops them and leaves the rest."""
    from datetime import date, time

    from flask_migrate import downgrade, upgrade

    from attendqr.extensions import db
    from attendqr.models import BellPeriod, BellSchedule, Holiday, Teacher, Timetable, TimetableEntry

    schedule = BellSchedule(name='Weekday')
    schedule.periods = [BellPeriod(position=1, label='1', start_time=time(10), end_time=time(10, 50))]
    timetable = Timetable(department_id=data.mca_id, semester_id=data.sem1_id, status='published',
                          effective_from=date(2026, 11, 2))
    entry = TimetableEntry(timetable=timetable, weekday=0, start_time=time(10), end_time=time(10, 50),
                           subject_id=data.subject_id)
    entry.teachers = [db.session.get(Teacher, data.teacher_id)]
    db.session.add_all([schedule, Holiday(start_date=date(2026, 12, 25), end_date=date(2026, 12, 25), title='X'),
                        timetable, entry])
    db.session.commit()
    db.session.remove()

    downgrade(revision='0020_subject_options')
    with db.engine.connect() as connection:
        tables = set(sa.inspect(connection).get_table_names())
        assert not tables & {'bell_schedule', 'bell_period', 'holiday', 'timetable', 'timetable_entry',
                             'timetable_entry_teacher'}
        assert connection.exec_driver_sql('SELECT COUNT(*) FROM subject').scalar() == 2

    upgrade()
    with db.engine.connect() as connection:
        assert _foreign_keys(connection, 'timetable_entry_teacher')['entry_id'] == ('timetable_entry', 'CASCADE')
        assert connection.exec_driver_sql('SELECT COUNT(*) FROM timetable').scalar() == 0


def test_session_occurrence_migration_round_trip(app, data):
    """0022 links sessions to routine classes; down drops the link and keeps the sessions."""
    from datetime import date, time

    from flask_migrate import downgrade, upgrade

    from attendqr.extensions import db
    from attendqr.models import Timetable, TimetableEntry
    from conftest import add_session

    timetable = Timetable(department_id=data.mca_id, semester_id=data.sem1_id, status='published',
                          effective_from=date(2026, 11, 2))
    entry = TimetableEntry(timetable=timetable, weekday=0, start_time=time(10), end_time=time(11), title='X')
    db.session.add_all([timetable, entry])
    db.session.commit()
    session = add_session(data)
    session.timetable_entry_id, session.occurrence_date = entry.id, date(2026, 11, 2)
    db.session.commit()
    db.session.remove()

    downgrade(revision='0021_routine')
    with db.engine.connect() as connection:
        columns = {column['name'] for column in sa.inspect(connection).get_columns('class_session')}
        assert 'timetable_entry_id' not in columns and 'occurrence_date' not in columns
        assert connection.exec_driver_sql('SELECT COUNT(*) FROM class_session').scalar() == 1

    upgrade()
    with db.engine.connect() as connection:
        assert _foreign_keys(connection, 'class_session')['timetable_entry_id'] == ('timetable_entry', 'SET NULL')


def test_routine_changes_migration_round_trip(app, data):
    """0023 adds one-date changes; down drops the table and nothing else."""
    from datetime import date

    from flask_migrate import downgrade, upgrade

    from attendqr.extensions import db
    from attendqr.models import RoutineChange

    db.session.add(RoutineChange(kind='extra', date=date(2026, 11, 2), department_id=data.mca_id,
                                 semester_id=data.sem1_id, title='Revision', reason='Before the exam'))
    db.session.commit()
    db.session.remove()

    downgrade(revision='0022_session_occurrence')
    with db.engine.connect() as connection:
        assert 'routine_change' not in sa.inspect(connection).get_table_names()

    upgrade()
    with db.engine.connect() as connection:
        assert _foreign_keys(connection, 'routine_change')['session_id'] == ('class_session', 'SET NULL')
        assert connection.exec_driver_sql('SELECT COUNT(*) FROM routine_change').scalar() == 0


def test_class_joined_at_migration_round_trip(app, data):
    """0024 adds when a student joined their class (and kept it in the history); existing rows stay NULL,
    so they count as before. Down drops both columns and keeps the students and the history."""
    from flask_migrate import downgrade, upgrade

    from attendqr.extensions import db
    from attendqr.models import StudentClassHistory
    from attendqr.utils import utc_now

    db.session.add(StudentClassHistory(student_id=data.student_id, department_id=data.mca_id,
                                       semester_id=data.sem2_id, left_at=utc_now()))
    db.session.commit()
    db.session.remove()

    downgrade(revision='0023_routine_changes')
    with db.engine.connect() as connection:
        assert 'class_joined_at' not in {c['name'] for c in sa.inspect(connection).get_columns('student')}
        assert 'joined_at' not in {c['name'] for c in sa.inspect(connection).get_columns('student_class_history')}
        assert connection.exec_driver_sql('SELECT COUNT(*) FROM student_class_history').scalar() == 1

    upgrade()
    with db.engine.connect() as connection:
        assert connection.exec_driver_sql('SELECT COUNT(*) FROM student WHERE class_joined_at IS NOT NULL').scalar() == 0
        assert connection.exec_driver_sql('SELECT joined_at FROM student_class_history').scalar() is None


def test_subject_color_migration_round_trip(app, data):
    """0025 adds subject.color (NULL: automatic); down drops it and keeps the subjects."""
    from flask_migrate import downgrade, upgrade

    from attendqr.extensions import db
    from attendqr.models import Subject

    db.session.get(Subject, data.subject_id).color = 'teal'
    db.session.commit()
    db.session.remove()

    downgrade(revision='0024_class_joined_at')
    with db.engine.connect() as connection:
        assert 'color' not in {c['name'] for c in sa.inspect(connection).get_columns('subject')}
        assert connection.exec_driver_sql('SELECT COUNT(*) FROM subject').scalar() == 2

    upgrade()
    with db.engine.connect() as connection:
        assert connection.exec_driver_sql('SELECT COUNT(*) FROM subject WHERE color IS NOT NULL').scalar() == 0


def test_activity_log_text_migration_round_trip(app, data, caplog):
    """0026 makes descriptions TEXT without changing them, so the chain still verifies. Down goes
    back to 255 characters, but refuses while a longer entry exists rather than cut sealed text."""
    from flask_migrate import downgrade, upgrade

    from attendqr.access import log_activity
    from attendqr.extensions import db
    from attendqr.models import ActivityLog
    from attendqr.services.audit import seal_pending, verify_chain

    with app.test_request_context():
        log_activity('test_short', 'A short entry.')
        db.session.commit()
    seal_pending()
    db.session.remove()

    downgrade(revision='0025_subject_color')
    with db.engine.connect() as connection:
        column = next(c for c in sa.inspect(connection).get_columns('activity_log') if c['name'] == 'description')
        assert getattr(column['type'], 'length', None) == 255

    upgrade()
    with db.engine.connect() as connection:
        column = next(c for c in sa.inspect(connection).get_columns('activity_log') if c['name'] == 'description')
        assert getattr(column['type'], 'length', None) is None
    assert verify_chain().ok

    db.session.add(ActivityLog(actor_username='test', action='test_long', description='y' * 300))
    db.session.commit()
    db.session.remove()
    # Flask-Migrate logs the migration's error and exits.
    with pytest.raises(SystemExit):
        downgrade(revision='0025_subject_color')
    assert 'longer than 255 characters' in caplog.text


def test_data_constraints_migration_round_trip(app, data, caplog):
    """0027 stores logins and emails lower case (unless that would clash), adds CHECK constraints and the
    group-name index, and refuses to upgrade over rows that break them. Down drops the constraints."""
    from flask_migrate import downgrade, upgrade
    from sqlalchemy.exc import IntegrityError

    from attendqr.extensions import db

    db.session.remove()
    downgrade(revision='0026_activity_log_text')
    with db.engine.begin() as connection:
        connection.execute(sa.text("UPDATE user_account SET username = 'Rahul@Example.com' WHERE username = :name"),
                           {'name': data.student})
        connection.execute(sa.text("UPDATE student SET roll_no = 'mca002' WHERE id = :id"), {'id': data.student2_id})
        # Two logins that differ only in case are left alone rather than merged.
        connection.exec_driver_sql("UPDATE user_account SET username = 'HOD@college.example' WHERE username = 'asha@college.example'")
        connection.exec_driver_sql("UPDATE user_account SET username = 'hod@College.example' WHERE username = 'hod@college.example'")
        connection.exec_driver_sql("UPDATE user_account SET role = 'janitor' WHERE username = 'ravi@college.example'")

    with pytest.raises(SystemExit):
        upgrade()
    assert 'break ck_user_account_role' in caplog.text

    with db.engine.begin() as connection:
        connection.exec_driver_sql("UPDATE user_account SET role = 'teacher' WHERE username = 'ravi@college.example'")
    upgrade()
    with db.engine.begin() as connection:
        names = {row[0] for row in connection.exec_driver_sql('SELECT username FROM user_account')}
        assert data.student in names and 'Rahul@Example.com' not in names
        assert {'HOD@college.example', 'hod@College.example'} <= names
        roll_no = connection.execute(sa.text('SELECT roll_no FROM student WHERE id = :id'), {'id': data.student2_id})
        assert roll_no.scalar() == 'MCA002'
    with pytest.raises(IntegrityError), db.engine.begin() as connection:
        connection.exec_driver_sql("INSERT INTO user_account (username, password, role, is_active, must_change_password, "
                                   "session_token) VALUES ('x@example.com', 'x', 'janitor', TRUE, FALSE, 't')")
    with pytest.raises(IntegrityError), db.engine.begin() as connection:
        connection.execute(sa.text("INSERT INTO class_group (department_id, semester_id, name, created_at) VALUES "
                                   "(:department, :semester, 'Gr-1', '2026-01-01'), "
                                   "(:department, :semester, 'GR-1', '2026-01-01')"),
                           {'department': data.mca_id, 'semester': data.sem1_id})

    downgrade(revision='0026_activity_log_text')
    with db.engine.begin() as connection:  # the constraints are gone again
        connection.exec_driver_sql("UPDATE user_account SET role = 'janitor' WHERE username = 'ravi@college.example'")
        connection.exec_driver_sql("UPDATE user_account SET role = 'teacher' WHERE username = 'ravi@college.example'")
    upgrade()


def test_session_create_token_migration_round_trip(app, data):
    """0028 adds class_session.create_token with a unique index; down drops it and keeps the sessions."""
    from flask_migrate import downgrade, upgrade

    from attendqr.extensions import db
    from conftest import add_session

    add_session(data)
    db.session.remove()
    downgrade(revision='0027_data_constraints')
    with db.engine.connect() as connection:
        assert 'create_token' not in {c['name'] for c in sa.inspect(connection).get_columns('class_session')}
        assert connection.exec_driver_sql('SELECT COUNT(*) FROM class_session').scalar() == 1
    upgrade()
    with db.engine.connect() as connection:
        indexes = {index['name']: index for index in sa.inspect(connection).get_indexes('class_session')}
        assert indexes['uq_class_session_create_token']['unique']
