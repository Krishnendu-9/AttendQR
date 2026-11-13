"""Upgrading databases created before migrations existed."""
import sqlite3
from pathlib import Path

import pytest
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
INSERT INTO teacher VALUES (1, 'Asha', 'asha@rcciit.org.in', 1);
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
    with sqlite3.connect(path) as conn:
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

    db.session.add(ClassSession(subject_id=data.subject_id, token='utc', created_at=datetime(2026, 1, 1, 4, 30),
                                expires_at=datetime(2026, 1, 1, 5, 0)))
    db.session.commit()
    db.session.remove()

    def stored():
        with db.engine.connect() as connection:
            row = connection.exec_driver_sql(
                "SELECT created_at, expires_at FROM class_session WHERE token = 'utc'").fetchone()
        return [str(value)[:16] for value in row]

    downgrade(revision='0009_user_account')
    assert stored() == ['2026-01-01 10:00', '2026-01-01 10:30']  # as earlier versions stored it (IST)
    upgrade()
    assert stored() == ['2026-01-01 04:30', '2026-01-01 05:00']
