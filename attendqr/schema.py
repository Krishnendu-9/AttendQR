"""Database setup: Alembic migrations, a one-time bridge for pre-Alembic databases, and the first Principal."""
from flask import current_app
from flask_migrate import stamp, upgrade
from sqlalchemy import inspect, text
from werkzeug.security import generate_password_hash

from .access import SPECIAL_CLASS_SUBJECT_NAME
from .extensions import db
from .models import Admin, ClassSession, Department, Student, Subject
from .utils import infer_department_branch, normalize_course_name, normalize_email

BASELINE_REVISION = '0001_baseline'

# Columns that older versions added at startup with ALTER TABLE. A database
# created before migrations existed is brought up to the baseline schema with
# these, then stamped so Alembic takes over.
LEGACY_COLUMNS = {
    'admin': {
        'role': "VARCHAR(20) DEFAULT 'super_admin' NOT NULL",
        'department_id': "INTEGER",
        'teacher_id': "INTEGER",
        'student_id': "INTEGER",
        'is_active': "BOOLEAN DEFAULT 1 NOT NULL",
        'must_change_password': "BOOLEAN DEFAULT 0 NOT NULL",
        'failed_login_attempts': "INTEGER DEFAULT 0 NOT NULL",
        'locked_until': "DATETIME",
    },
    'class_session': {
        'special_title': "VARCHAR(200)",
        'is_active': "BOOLEAN DEFAULT 1 NOT NULL",
    },
    'department': {'branch': "VARCHAR(20)"},
    'subject': {'paper_code': "VARCHAR(50)", 'teacher_id': "INTEGER"},
    'student': {'device_id': "VARCHAR(100)"},
}


def prepare_database():
    """Bring the database to the latest schema and make sure a Principal exists."""
    tables = set(inspect(db.engine).get_table_names())
    bridged = False
    if 'alembic_version' not in tables and 'admin' in tables:
        _bring_legacy_schema_to_baseline(tables)
        stamp(revision=BASELINE_REVISION)
        bridged = True

    upgrade()
    db.session.remove()

    if bridged:
        _legacy_data_fixups()
    ensure_default_principal()


def _bring_legacy_schema_to_baseline(tables):
    if db.engine.dialect.name != 'sqlite':
        raise RuntimeError("Found an AttendQR database without migration history; only SQLite ones can be upgraded automatically.")
    with db.engine.begin() as connection:
        for table, columns in LEGACY_COLUMNS.items():
            if table not in tables:
                continue
            existing = {row[1] for row in connection.execute(text(f"PRAGMA table_info({table})"))}
            for column, ddl in columns.items():
                if column not in existing:
                    connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
        if 'admin' in tables:
            connection.execute(text("UPDATE admin SET role = 'super_admin' WHERE role IS NULL OR role = ''"))
        if 'attendance' in tables:
            connection.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_attendance_student_session "
                "ON attendance(student_id, session_id)"
            ))


def _legacy_data_fixups():
    """Data clean-ups that older versions re-ran on every startup; now run once."""
    for department in Department.query.all():
        normalized_name = normalize_course_name(department.name)
        if department.name != normalized_name:
            department.name = normalized_name
        if not department.branch:
            department.branch = infer_department_branch(normalized_name) or None

    for subject in Subject.query.filter(Subject.name != SPECIAL_CLASS_SUBJECT_NAME, Subject.teacher_id.is_(None)):
        teacher_ids = {
            teacher_id for (teacher_id,) in db.session.query(ClassSession.teacher_id)
            .filter(ClassSession.subject_id == subject.id, ClassSession.teacher_id.isnot(None))
            .distinct()
        }
        if len(teacher_ids) == 1:
            subject.teacher_id = teacher_ids.pop()

    from .services.roster import ensure_student_account
    for student in Student.query.all():
        ensure_student_account(student)

    principal_email = normalize_email(current_app.config['DEFAULT_PRINCIPAL_EMAIL'])
    old_default = Admin.query.filter_by(username='admin', role='super_admin').first()
    if old_default and not Admin.query.filter(db.func.lower(Admin.username) == principal_email).first():
        old_default.username = principal_email
        old_default.password = generate_password_hash(current_app.config['DEFAULT_PRINCIPAL_PASSWORD'])
        old_default.is_active = True
        old_default.must_change_password = True
        old_default.rotate_session_token()

    db.session.commit()


def ensure_default_principal():
    if Admin.query.first():
        return
    email = normalize_email(current_app.config['DEFAULT_PRINCIPAL_EMAIL'])
    db.session.add(Admin(
        username=email,
        password=generate_password_hash(current_app.config['DEFAULT_PRINCIPAL_PASSWORD']),
        role='super_admin',
        is_active=True,
        # The default password is published in .env.example, so it must be replaced at first login.
        must_change_password=True,
    ))
    db.session.commit()
    current_app.logger.warning("Created the initial Principal account %s; it must change its password at first login.", email)
