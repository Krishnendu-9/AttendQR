"""Database setup: Alembic migrations, a one-time bridge for pre-Alembic databases, and the first Principal."""
import os
import secrets

from flask import current_app
from flask_migrate import stamp, upgrade
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash

from .access import SPECIAL_CLASS_SUBJECT_NAME
from .extensions import db
from .models import ClassSession, Department, Student, Subject, User
from .utils import infer_department_branch, normalize_course_name, normalize_email

BASELINE_REVISION = '0001_baseline'

# Published in earlier versions of .env.example and the README, so never used as a real password.
PLACEHOLDER_PRINCIPAL_PASSWORDS = frozenset({'ChangeMe123!'})
INITIAL_PASSWORD_FILENAME = 'initial_principal_password'  # noqa: S105 - a file name, not a password

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
    discard_used_initial_password()


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
    created = sum(1 for student in Student.query.all() if ensure_student_account(student))
    if created:
        # Their random passwords are not shown anywhere, so staff must reset them.
        current_app.logger.warning(
            "Created %d student login(s) for existing student profiles. Use 'Reset password' on the "
            "Students page to give each student a temporary password.", created,
        )

    principal_email = normalize_email(current_app.config['DEFAULT_PRINCIPAL_EMAIL'])
    old_default = User.query.filter_by(username='admin', role='principal').first()
    new_password = write_file = None
    if old_default and not User.query.filter(db.func.lower(User.username) == principal_email).first():
        new_password, write_file = initial_principal_password()
        old_default.username = principal_email
        old_default.password = generate_password_hash(new_password)
        old_default.is_active = True
        old_default.must_change_password = True
        old_default.rotate_session_token()

    db.session.commit()
    if write_file:
        write_initial_password_file(new_password)


def _has_accounts():
    return db.session.query(User.query.exists()).scalar()


def ensure_default_principal():
    if _has_accounts():
        return
    email = normalize_email(current_app.config['DEFAULT_PRINCIPAL_EMAIL'])
    password, write_file = initial_principal_password()
    account = User(
        username=email,
        password=generate_password_hash(password),
        role='principal',
        is_active=True,
        # Whoever set up the server knows this password, so the Principal must replace it.
        must_change_password=True,
    )
    db.session.add(account)
    try:
        db.session.commit()
    except IntegrityError:
        # Several worker processes starting together: another one created the account
        # first. Its password file is the right one, so leave the file alone.
        db.session.rollback()
        current_app.logger.info("The initial Principal account was created by another process.")
        return

    password_file = None
    if write_file:
        # Written only after the account exists, so a process that lost the race above
        # can never replace the file with a password that no account has.
        try:
            password_file = write_initial_password_file(password)
        except OSError:
            # Without the file nobody could log in; remove the account so the next start retries.
            db.session.delete(account)
            db.session.commit()
            raise
    source = f"the one-time password in {password_file}" if password_file else "DEFAULT_PRINCIPAL_PASSWORD"
    current_app.logger.warning(
        "Created the initial Principal account %s with %s; it must be changed at first login.", email, source,
    )


# ── First Principal password ─────────────────────────────────

def initial_password_path():
    return current_app.config.get('INITIAL_PRINCIPAL_PASSWORD_FILE') or os.path.join(
        current_app.instance_path, INITIAL_PASSWORD_FILENAME,
    )


def _configured_principal_password():
    """DEFAULT_PRINCIPAL_PASSWORD, unless it is empty or the published placeholder."""
    configured = current_app.config.get('DEFAULT_PRINCIPAL_PASSWORD') or ''
    if configured in PLACEHOLDER_PRINCIPAL_PASSWORDS:
        current_app.logger.warning("Ignoring DEFAULT_PRINCIPAL_PASSWORD: it is a published placeholder.")
        return None
    return configured if configured.strip() else None


def initial_principal_password():
    """Return (password, write_file): the configured password and False, or a new random one and True.

    Call write_initial_password_file() once the account using it is committed.
    """
    configured = _configured_principal_password()
    if configured:
        return configured, False
    return secrets.token_urlsafe(18), True


def write_initial_password_file(password):
    """Write the generated first password and return the file's path.

    Only the path is ever logged. The file is readable by the app's user only
    (on Windows it inherits the instance folder's permissions).
    """
    path = initial_password_path()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as handle:
        handle.write(f"{password}\n")
    os.chmod(path, 0o600)  # The mode given to os.open does not apply to a file that already existed.
    return path


def discard_used_initial_password():
    """Delete the generated password file once it no longer opens any account. Returns True if deleted."""
    path = initial_password_path()
    try:
        with open(path, encoding='utf-8') as handle:
            password = handle.read().strip()
    except FileNotFoundError:
        return False
    waiting = User.query.filter_by(role='principal', must_change_password=True).all()
    if password and any(check_password_hash(account.password, password) for account in waiting):
        return False
    os.remove(path)
    current_app.logger.warning("Deleted %s: the initial Principal password has been replaced.", path)
    return True
