from flask import Flask, render_template, redirect, url_for, request, jsonify, send_file, abort, flash, session, g
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from models import db, Admin, Student, ClassSession, Attendance, Department, Subject, Teacher, Semester, ActivityLog
from config import Config
from datetime import datetime, timedelta
from sqlalchemy import event, text, and_, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.engine import Engine
from functools import wraps
from math import ceil
import csv
import sqlite3
import qrcode, uuid, os, io, socket, re, secrets
import openpyxl
from urllib.parse import urlsplit
from reportlab.lib.pagesizes import letter, landscape, A4
from reportlab.platypus import SimpleDocTemplate, Table, Spacer
from reportlab.lib.units import cm
import pytz

# Define IST timezone
IST = pytz.timezone('Asia/Kolkata')

def get_ist_now():
    """Get current time in IST, return as naive datetime (no timezone info)"""
    return datetime.now(IST).replace(tzinfo=None)


def format_12h(dt, include_date=True):
    if not dt:
        return ''
    fmt = '%d-%m-%Y %I:%M %p' if include_date else '%I:%M %p'
    return dt.strftime(fmt)


def utc_to_ist(utc_dt):
    """Convert UTC datetime to IST for display"""
    if utc_dt is None:
        return None
    # Assume stored datetime is UTC
    utc_dt = pytz.utc.localize(utc_dt)
    ist_dt = utc_dt.astimezone(IST)
    return ist_dt


def get_lan_ip():
    """Return the LAN IP reachable by phones on the same network."""
    override = os.environ.get('APP_HOST_IP')
    if override:
        return override.strip()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(('8.8.8.8', 80))
        return sock.getsockname()[0]
    except OSError:
        return socket.gethostbyname(socket.gethostname())
    finally:
        sock.close()


def build_scan_url(token):
    base_url = os.environ.get('APP_BASE_URL')
    if base_url:
        return f"{base_url.rstrip('/')}/scan/{token}"
    port = os.environ.get('APP_PORT', '5000')
    return f"http://{get_lan_ip()}:{port}/scan/{token}"


def get_csrf_token():
    token = session.get('_csrf_token')
    if not token:
        token = secrets.token_urlsafe(32)
        session['_csrf_token'] = token
    return token


def validate_csrf_request():
    if request.method != 'POST':
        return None

    expected = session.get('_csrf_token')
    supplied = (
        request.form.get('_csrf_token')
        or request.headers.get('X-CSRFToken')
        or request.headers.get('X-CSRF-Token')
    )

    if expected and supplied and secrets.compare_digest(expected, supplied):
        return None

    if request.is_json or request.path == url_for('mark_attendance'):
        return jsonify({'success': False, 'message': 'Security token expired. Refresh the page and try again.'}), 400
    abort(400, description='Security token expired. Refresh the page and try again.')


def parse_int_value(value):
    if value is None:
        return None
    value = str(value).strip()
    if not value:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None

app = Flask(__name__)
app.config.from_object(Config)
db.init_app(app)

@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = None

@login_manager.user_loader
def load_user(user_id):
    return Admin.query.get(int(user_id))


@app.before_request
def prepare_request_state():
    g.password_reminder = False

    csrf_error = validate_csrf_request()
    if csrf_error:
        return csrf_error

    if current_user.is_authenticated and not getattr(current_user, 'is_active', True):
        logout_user()
        flash("That account is inactive. Please contact the Principal or HOD.", "warning")
        return redirect(url_for('login'))

    if current_user.is_authenticated and session.pop('show_password_reminder', False):
        g.password_reminder = bool(getattr(current_user, 'must_change_password', False))


@app.context_processor
def inject_now():
    return {
        'now': lambda: get_ist_now(),
        'role_label': role_label,
        'department_scope_label': department_scope_label,
        'subject_display_name': subject_display_name,
        'session_display_name': session_display_name,
        'format_12h': format_12h,
        'csrf_token': get_csrf_token,
        'password_reminder': getattr(g, 'password_reminder', False),
        'can_edit_account': can_edit_account,
        'can_manage_account': can_manage_account,
        'can_toggle_account_status': can_toggle_account_status,
        'can_reset_staff_account': can_reset_staff_account
    }


ROLE_LABELS = {
    'super_admin': 'Principal',
    'hod': 'HOD',
    'teacher': 'Teacher',
    'student': 'Student'
}

ATTENDANCE_TARGET = 80
ALLOWED_STAFF_EMAIL_DOMAINS = ('@rcciit.org.in', '@gmail.com')
SPECIAL_CLASS_SUBJECT_NAME = '__SPECIAL_CLASS__'
MIN_QR_DURATION = 1
MAX_QR_DURATION = 180
EMAIL_PATTERN = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}$")


def normalize_text(value):
    return ' '.join((value or '').strip().split())


def normalize_email(value):
    return normalize_text(value).lower()


def normalize_roll_no(value):
    return normalize_text(value).upper()


def is_valid_email(value):
    email = normalize_email(value)
    if not email or len(email) > 120:
        return False
    return bool(EMAIL_PATTERN.fullmatch(email))


def is_allowed_staff_email(value):
    email = normalize_email(value)
    return is_valid_email(email) and any(email.endswith(domain) for domain in ALLOWED_STAFF_EMAIL_DOMAINS)


def email_format_rule_message():
    return "Use a valid email address."


def staff_email_rule_message():
    return "Use a valid email ending with @rcciit.org.in or @gmail.com."


def normalize_branch(value):
    return ' '.join((value or '').strip().upper().split())


def normalize_course_name(value):
    course_name = ' '.join((value or '').strip().split())
    return re.sub(r'\s*\(', ' (', course_name)


def normalize_paper_code(value):
    return ' '.join((value or '').strip().upper().split())


def infer_department_branch(course_name):
    title = normalize_course_name(course_name)
    match = re.search(r'\(([A-Za-z0-9&./-]+)\)\s*$', title)
    if match:
        return normalize_branch(match.group(1))
    if title.isupper() and ' ' not in title and len(title) <= 12:
        return title

    words = re.findall(r'[A-Za-z0-9]+', title)
    ignored = {'of', 'and', 'in', 'the', 'for'}
    acronym = ''.join(word[0] for word in words if word.lower() not in ignored).upper()
    return acronym[:12] if acronym else ''


def role_label(role):
    return ROLE_LABELS.get(role, role.replace('_', ' ').title() if role else 'User')


def department_scope_label(department):
    if not department:
        return 'Not assigned'
    branch = department.branch_name or infer_department_branch(department.course_name)
    course_short = infer_department_branch(department.course_name)
    if branch and course_short:
        return f"{branch} - {course_short}"
    return branch or department.course_name


def log_activity(action, description, target_type=None, target_id=None, department_id=None):
    actor_id = current_user.id if current_user.is_authenticated else None
    actor_username = current_user.username if current_user.is_authenticated else 'System'
    db.session.add(ActivityLog(
        actor_admin_id=actor_id,
        actor_username=actor_username,
        department_id=department_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        description=description,
        created_at=get_ist_now()
    ))


def user_role():
    if not current_user.is_authenticated:
        return None
    return getattr(current_user, 'role', None) or 'super_admin'


def is_super_admin():
    return user_role() == 'super_admin'


def is_safe_redirect_target(target):
    if not target:
        return False
    parsed = urlsplit(target)
    return parsed.scheme == '' and parsed.netloc == '' and target.startswith('/')


def role_required(*roles):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                return login_manager.unauthorized()
            if user_role() not in roles:
                abort(403)
            return fn(*args, **kwargs)
        return wrapper
    return decorator


def current_department_id():
    if not current_user.is_authenticated:
        return None
    if user_role() == 'hod':
        return current_user.department_id
    if user_role() == 'teacher' and current_user.teacher:
        return current_user.teacher.department_id
    if user_role() == 'student' and current_user.student:
        return current_user.student.department_id
    return current_user.department_id


def current_teacher_id():
    if current_user.is_authenticated and user_role() == 'teacher':
        return current_user.teacher_id
    return None


def can_access_department(department_id):
    if is_super_admin():
        return True
    dept_id = current_department_id()
    return dept_id is not None and int(department_id) == int(dept_id)


def can_access_student(student):
    if is_super_admin():
        return True
    if user_role() == 'student':
        return current_user.student_id == student.id
    return can_access_department(student.department_id)


def can_access_teacher(teacher):
    if is_super_admin():
        return True
    if user_role() == 'teacher':
        return current_user.teacher_id == teacher.id
    return can_access_department(teacher.department_id)


def can_access_subject(subject):
    if not subject:
        return False
    if user_role() == 'teacher':
        return subject.teacher_id == current_teacher_id()
    return can_access_department(subject.department_id)


def can_access_session(session):
    if not session:
        return False
    if user_role() == 'teacher':
        return bool(
            session.subject
            and can_access_department(session.subject.department_id)
            and session.teacher_id == current_teacher_id()
        )
    if not session.subject or not can_access_subject(session.subject):
        return False
    return user_role() in ('super_admin', 'hod')


def visible_departments_query():
    query = Department.query
    if not is_super_admin():
        dept_id = current_department_id()
        query = query.filter(Department.id == dept_id if dept_id else False)
    return query


def ordered_departments_query():
    return visible_departments_query().order_by(Department.branch, Department.name)


def build_account_profile(admin):
    role = getattr(admin, 'role', None) or 'super_admin'
    profile = {
        'display_name': admin.username,
        'username': admin.username,
        'role_label': role_label(role),
        'email': admin.username if '@' in (admin.username or '') else None,
        'scope_label': 'System Access',
        'scope_value': 'All departments' if role == 'super_admin' else 'Not assigned',
        'details': []
    }

    if role == 'student' and admin.student:
        profile.update({
            'display_name': admin.student.name,
            'email': admin.student.email,
            'scope_label': 'Department',
            'scope_value': admin.student.department.display_name
        })
        profile['details'] = [
            ('Roll Number', admin.student.roll_no),
            ('Semester', admin.student.semester.name)
        ]
    elif role == 'teacher' and admin.teacher:
        profile.update({
            'display_name': admin.teacher.name,
            'email': admin.teacher.email,
            'scope_label': 'Department',
            'scope_value': admin.teacher.department.display_name
        })
        profile['details'] = [('Teacher Profile', admin.teacher.name)]
    elif role == 'hod':
        profile.update({
            'scope_label': 'Department',
            'scope_value': admin.department.display_name if admin.department else 'Not assigned'
        })
        profile['details'] = []
    else:
        profile['details'] = []

    return profile


def is_hidden_special_subject(subject):
    return bool(subject and subject.name == SPECIAL_CLASS_SUBJECT_NAME)


def subject_display_name(subject):
    if not subject:
        return ''
    if is_hidden_special_subject(subject):
        return 'Special Class'
    paper_code = normalize_paper_code(getattr(subject, 'paper_code', None))
    if paper_code:
        return f"{paper_code} - {subject.name}"
    return subject.name


def session_display_name(session):
    if getattr(session, 'special_title', None):
        return session.special_title
    return subject_display_name(session.subject) if session.subject else 'Special Class'


def get_or_create_special_subject(department_id, semester_id):
    subject = Subject.query.filter_by(
        department_id=department_id,
        semester_id=semester_id,
        name=SPECIAL_CLASS_SUBJECT_NAME
    ).first()
    if subject:
        return subject

    subject = Subject(
        name=SPECIAL_CLASS_SUBJECT_NAME,
        paper_code=None,
        department_id=department_id,
        semester_id=semester_id
    )
    db.session.add(subject)
    db.session.flush()
    return subject


def visible_semesters_query():
    query = Semester.query
    if not is_super_admin():
        dept_id = current_department_id()
        query = query.filter(Semester.department_id == dept_id if dept_id else False)
    return query


def visible_subjects_query():
    query = Subject.query
    query = query.filter(Subject.name != SPECIAL_CLASS_SUBJECT_NAME)
    if user_role() == 'teacher':
        query = query.filter(Subject.teacher_id == current_teacher_id())
    elif not is_super_admin():
        dept_id = current_department_id()
        query = query.filter(Subject.department_id == dept_id if dept_id else False)
    return query


def visible_teachers_query():
    query = Teacher.query
    if user_role() == 'teacher':
        query = query.filter(Teacher.id == current_teacher_id())
    elif not is_super_admin():
        dept_id = current_department_id()
        query = query.filter(Teacher.department_id == dept_id if dept_id else False)
    return query


def visible_students_query():
    query = Student.query
    if user_role() == 'student':
        query = query.filter(Student.id == current_user.student_id)
    elif not is_super_admin():
        dept_id = current_department_id()
        query = query.filter(Student.department_id == dept_id if dept_id else False)
    return query


def visible_sessions_query():
    query = ClassSession.query.join(Subject)
    if user_role() == 'teacher':
        query = query.filter(ClassSession.teacher_id == current_teacher_id())
    elif not is_super_admin():
        dept_id = current_department_id()
        query = query.filter(Subject.department_id == dept_id if dept_id else False)
    return query


def visible_activity_logs_query():
    query = ActivityLog.query.outerjoin(Admin, ActivityLog.actor_admin_id == Admin.id)
    if is_super_admin():
        return query
    if user_role() == 'hod':
        dept_id = current_department_id()
        return query.filter(
            or_(
                ActivityLog.actor_admin_id == current_user.id,
                and_(
                    ActivityLog.department_id == dept_id,
                    Admin.role == 'teacher'
                )
            )
        )
    return query.filter(ActivityLog.actor_admin_id == current_user.id)


def own_security_activity_logs_query():
    return ActivityLog.query.filter(
        ActivityLog.action.in_(['password_changed', 'account_password_reset', 'account_status_changed']),
        or_(
            ActivityLog.actor_admin_id == current_user.id,
            and_(
                ActivityLog.target_type == 'admin',
                ActivityLog.target_id == current_user.id
            )
        )
    )


def has_active_hod(department_id, exclude_admin_id=None):
    query = Admin.query.filter_by(role='hod', department_id=department_id, is_active=True)
    if exclude_admin_id:
        query = query.filter(Admin.id != exclude_admin_id)
    return db.session.query(query.exists()).scalar()


def generate_temporary_password():
    return f"Reset@{uuid.uuid4().hex[:6].upper()}"


def password_strength_label(password):
    score = 0
    if len(password) >= 6:
        score += 1
    if len(password) >= 8:
        score += 1
    if re.search(r'[a-z]', password):
        score += 1
    if re.search(r'[A-Z]', password):
        score += 1
    if re.search(r'\d', password):
        score += 1
    if re.search(r'[^A-Za-z0-9]', password):
        score += 1

    if len(password) < 6:
        return 'too_short'
    if score <= 3:
        return 'weak'
    if score <= 5:
        return 'medium'
    return 'strong'


def is_account_locked(admin):
    return bool(admin and admin.locked_until and get_ist_now() < admin.locked_until)


def can_manage_account(admin):
    if user_role() == 'hod':
        return admin.role == 'teacher' and admin.department_id == current_department_id()
    if not is_super_admin():
        return False
    if admin.role == 'student' or admin.id == current_user.id:
        return False
    if admin.role == 'super_admin' and admin.is_active and Admin.query.filter_by(role='super_admin', is_active=True).count() <= 1:
        return False
    return True


def can_edit_account(admin):
    if admin.role == 'student':
        return False
    if user_role() == 'hod':
        return admin.role == 'teacher' and admin.department_id == current_department_id()
    return is_super_admin()


def can_toggle_account_status(admin):
    return can_manage_account(admin)


def can_reset_staff_account(admin):
    if user_role() == 'hod':
        return admin.role == 'teacher' and admin.department_id == current_department_id()
    if not is_super_admin():
        return False
    return admin.role in ('super_admin', 'hod', 'teacher') and admin.id != current_user.id


def classes_needed_for_target(attended, total, target=ATTENDANCE_TARGET):
    if total == 0 or attended / total * 100 >= target:
        return 0
    target_ratio = target / 100
    return max(0, ceil((target_ratio * total - attended) / (1 - target_ratio)))


def build_student_attendance_summary(student):
    subjects = Subject.query.filter(
        Subject.department_id == student.department_id,
        Subject.semester_id == student.semester_id,
        Subject.name != SPECIAL_CLASS_SUBJECT_NAME
    ).order_by(Subject.paper_code, Subject.name).all()
    rows = []
    overall_attended = 0
    overall_total = 0

    for subject in subjects:
        sessions = ClassSession.query.filter_by(subject_id=subject.id).all()
        total = len(sessions)
        attended = Attendance.query.join(ClassSession).filter(
            Attendance.student_id == student.id,
            ClassSession.subject_id == subject.id
        ).count()
        percentage = round(attended / total * 100, 2) if total else 0
        rows.append({
            'subject': subject,
            'attended': attended,
            'total': total,
            'percentage': percentage,
            'needed': classes_needed_for_target(attended, total),
            'safe': percentage >= ATTENDANCE_TARGET if total else True
        })
        overall_attended += attended
        overall_total += total

    overall_percentage = round(overall_attended / overall_total * 100, 2) if overall_total else 0
    recent = Attendance.query.filter_by(student_id=student.id).order_by(Attendance.timestamp.desc()).limit(8).all()

    return {
        'rows': rows,
        'recent': recent,
        'target': ATTENDANCE_TARGET,
        'overall_attended': overall_attended,
        'overall_total': overall_total,
        'overall_percentage': overall_percentage,
        'overall_needed': classes_needed_for_target(overall_attended, overall_total),
        'overall_safe': overall_percentage >= ATTENDANCE_TARGET if overall_total else True
    }


def describe_delete_block(entity_name, counts):
    used_counts = [f"{label}: {value}" for label, value in counts.items() if value]
    if not used_counts:
        return f"{entity_name} still has linked records."
    return f"Cannot delete {entity_name} because it still has linked records ({', '.join(used_counts)})."


def ensure_rbac_columns():
    columns = {row[1] for row in db.session.execute(text("PRAGMA table_info(admin)")).fetchall()}
    migrations = {
        'role': "ALTER TABLE admin ADD COLUMN role VARCHAR(20) DEFAULT 'super_admin' NOT NULL",
        'department_id': "ALTER TABLE admin ADD COLUMN department_id INTEGER",
        'teacher_id': "ALTER TABLE admin ADD COLUMN teacher_id INTEGER",
        'student_id': "ALTER TABLE admin ADD COLUMN student_id INTEGER",
        'is_active': "ALTER TABLE admin ADD COLUMN is_active BOOLEAN DEFAULT 1 NOT NULL",
        'must_change_password': "ALTER TABLE admin ADD COLUMN must_change_password BOOLEAN DEFAULT 0 NOT NULL",
        'failed_login_attempts': "ALTER TABLE admin ADD COLUMN failed_login_attempts INTEGER DEFAULT 0 NOT NULL",
        'locked_until': "ALTER TABLE admin ADD COLUMN locked_until DATETIME"
    }
    for column, statement in migrations.items():
        if column not in columns:
            db.session.execute(text(statement))
    db.session.execute(text("UPDATE admin SET role = 'super_admin' WHERE role IS NULL OR role = ''"))
    db.session.execute(text("UPDATE admin SET is_active = 1 WHERE is_active IS NULL"))
    db.session.execute(text("UPDATE admin SET must_change_password = 0 WHERE must_change_password IS NULL"))
    db.session.execute(text("UPDATE admin SET failed_login_attempts = 0 WHERE failed_login_attempts IS NULL"))
    db.session.commit()


def ensure_class_session_columns():
    columns = {row[1] for row in db.session.execute(text("PRAGMA table_info(class_session)")).fetchall()}
    if 'special_title' not in columns:
        db.session.execute(text("ALTER TABLE class_session ADD COLUMN special_title VARCHAR(200)"))
        db.session.commit()


def ensure_department_columns():
    columns = {row[1] for row in db.session.execute(text("PRAGMA table_info(department)")).fetchall()}
    if 'branch' not in columns:
        db.session.execute(text("ALTER TABLE department ADD COLUMN branch VARCHAR(20)"))
        db.session.commit()

    updated = False
    for department in Department.query.all():
        normalized_name = normalize_course_name(department.name)
        inferred_branch = infer_department_branch(normalized_name)

        if department.name != normalized_name:
            department.name = normalized_name
            updated = True
        if not department.branch and inferred_branch:
            department.branch = inferred_branch
            updated = True

    if updated:
        db.session.commit()


def ensure_subject_columns():
    columns = {row[1] for row in db.session.execute(text("PRAGMA table_info(subject)")).fetchall()}
    if 'paper_code' not in columns:
        db.session.execute(text("ALTER TABLE subject ADD COLUMN paper_code VARCHAR(50)"))
        db.session.commit()
    if 'teacher_id' not in columns:
        db.session.execute(text("ALTER TABLE subject ADD COLUMN teacher_id INTEGER"))
        db.session.commit()


def backfill_subject_teacher_assignments():
    updated = False
    for subject in Subject.query.filter(Subject.name != SPECIAL_CLASS_SUBJECT_NAME, Subject.teacher_id.is_(None)).all():
        teacher_ids = {
            teacher_id for (teacher_id,) in db.session.query(ClassSession.teacher_id)
            .filter(ClassSession.subject_id == subject.id, ClassSession.teacher_id.isnot(None))
            .distinct()
            .all()
        }
        if len(teacher_ids) == 1:
            subject.teacher_id = teacher_ids.pop()
            updated = True
    if updated:
        db.session.commit()


def ensure_student_account(student):
    if not student.email:
        return
    existing = Admin.query.filter_by(student_id=student.id).first()
    if existing:
        existing.role = 'student'
        existing.username = student.email
        existing.student_id = student.id
        existing.department_id = student.department_id
        existing.is_active = True
        return
    username_match = Admin.query.filter_by(username=student.email).first()
    if username_match:
        return
    db.session.add(Admin(
        username=student.email,
        password=generate_password_hash(student.roll_no),
        role='student',
        department_id=student.department_id,
        student_id=student.id,
        is_active=True,
        must_change_password=True,
        failed_login_attempts=0,
        locked_until=None
    ))


def ensure_existing_student_accounts():
    for student in Student.query.all():
        ensure_student_account(student)
    db.session.commit()


def ensure_attendance_unique_index():
    try:
        db.session.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_attendance_student_session "
            "ON attendance(student_id, session_id)"
        ))
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise


def normalize_header(value):
    return (value or '').strip().lower().replace(' ', '_')


def parse_student_import(file_storage):
    filename = (file_storage.filename or '').lower()
    file_storage.stream.seek(0)
    if filename.endswith('.csv'):
        content = file_storage.stream.read().decode('utf-8-sig').splitlines()
        return list(csv.DictReader(content))

    if filename.endswith('.xlsx'):
        workbook = openpyxl.load_workbook(file_storage.stream, read_only=True, data_only=True)
        sheet = workbook.active
        rows = list(sheet.iter_rows(values_only=True))
        if not rows:
            return []
        headers = [normalize_header(value) for value in rows[0]]
        parsed = []
        for values in rows[1:]:
            parsed.append({headers[index]: value for index, value in enumerate(values) if index < len(headers)})
        return parsed

    raise ValueError("Upload a .csv or .xlsx file")


def row_value(row, *keys):
    normalized = {normalize_header(key): value for key, value in row.items()}
    for key in keys:
        value = normalized.get(key)
        if value is not None:
            if isinstance(value, float) and value.is_integer():
                value = int(value)
            return str(value).strip()
    return ''


def resolve_import_department(row):
    if not is_super_admin():
        return Department.query.get(current_department_id())

    dept_id = row_value(row, 'department_id', 'dept_id')
    if dept_id:
        try:
            return Department.query.get(int(dept_id))
        except ValueError:
            pass

    dept_name = row_value(row, 'department', 'dept')
    if dept_name:
        normalized = normalize_course_name(dept_name).lower()
        for department in Department.query.all():
            labels = {
                department.course_name.lower(),
                department.display_name.lower()
            }
            if department.branch_name:
                labels.add(department.branch_name.lower())
            if normalized in labels:
                return department

    return None


def resolve_import_semester(row, department_id):
    sem_id = row_value(row, 'semester_id', 'sem_id')
    if sem_id:
        try:
            semester = Semester.query.get(int(sem_id))
            if semester and semester.department_id == department_id:
                return semester
        except ValueError:
            pass

    sem_name = row_value(row, 'semester', 'sem')
    if sem_name:
        return Semester.query.filter(
            Semester.department_id == department_id,
            db.func.lower(Semester.name) == sem_name.lower()
        ).first()

    return None


def import_students_from_rows(rows):
    imported = 0
    errors = []
    seen_rolls = set()
    seen_emails = set()

    for index, row in enumerate(rows, start=2):
        name = normalize_text(row_value(row, 'name', 'student_name'))
        roll_no = normalize_roll_no(row_value(row, 'roll_no', 'roll', 'roll_number'))
        email = normalize_email(row_value(row, 'email', 'student_email'))
        department = resolve_import_department(row)

        if not name or not roll_no or not email:
            errors.append(f"Row {index}: name, roll_no, and email are required")
            continue
        if not is_valid_email(email):
            errors.append(f"Row {index}: valid student email is required")
            continue
        if not department or not can_access_department(department.id):
            errors.append(f"Row {index}: valid department is required")
            continue

        semester = resolve_import_semester(row, department.id)
        if not semester:
            errors.append(f"Row {index}: valid semester is required for {department.display_name}")
            continue

        roll_key = (roll_no, department.id, semester.id)
        if roll_key in seen_rolls:
            errors.append(f"Row {index}: duplicate roll number {roll_no} in uploaded file")
            continue
        if email in seen_emails:
            errors.append(f"Row {index}: duplicate email {email} in uploaded file")
            continue

        existing_roll = Student.query.filter(
            db.func.upper(Student.roll_no) == roll_no,
            Student.department_id == department.id,
            Student.semester_id == semester.id
        ).first()
        if existing_roll:
            errors.append(f"Row {index}: roll number {roll_no} already exists")
            continue
        if Student.query.filter(db.func.lower(Student.email) == email).first():
            errors.append(f"Row {index}: email {email} already exists")
            continue
        if Admin.query.filter(db.func.lower(Admin.username) == email).first():
            errors.append(f"Row {index}: login account email {email} already exists")
            continue

        student = Student(
            name=name,
            roll_no=roll_no,
            email=email,
            department_id=department.id,
            semester_id=semester.id
        )
        db.session.add(student)
        db.session.flush()
        ensure_student_account(student)
        imported += 1
        seen_rolls.add(roll_key)
        seen_emails.add(email)

    db.session.commit()
    return imported, errors


@app.route('/')
@login_required
def dashboard():
    if user_role() == 'student':
        if not current_user.student:
            abort(403)
        summary = build_student_attendance_summary(current_user.student)
        return render_template('student_dashboard.html', student=current_user.student, summary=summary)

    now_ist = get_ist_now()

    def session_panel_rows(sessions):
        rows = []
        for session in sessions:
            rows.append({
                'id': session.id,
                'name': session_display_name(session),
                'department': session.subject.department.display_name if session.subject and session.subject.department else '',
                'semester': session.subject.semester.name if session.subject and session.subject.semester else '',
                'teacher': session.teacher.name if session.teacher else 'Unassigned',
                'created_at': session.created_at,
                'expires_at': session.expires_at,
                'is_active': session.expires_at > now_ist,
                'attendance_count': Attendance.query.filter_by(session_id=session.id).count()
            })
        return rows

    scoped_sessions = visible_sessions_query().all()
    recent_sessions = visible_sessions_query().order_by(ClassSession.created_at.desc()).limit(3).all()
    recent_account_logs = visible_activity_logs_query().order_by(ActivityLog.created_at.desc()).limit(3).all()

    if is_super_admin():
        acc_principals = Admin.query.filter_by(role='super_admin').count()
        acc_hods = Admin.query.filter_by(role='hod').count()
        acc_teachers = Admin.query.filter_by(role='teacher').count()
        accounts = {
            'total': acc_principals + acc_hods + acc_teachers,
            'principals': acc_principals,
            'hods': acc_hods,
            'teachers': acc_teachers,
        }
    else:
        dept_id = current_department_id()
        acc_teachers = Admin.query.filter_by(role='teacher', department_id=dept_id).count()
        accounts = {
            'total': acc_teachers,
            'principals': None,
            'hods': None,
            'teachers': acc_teachers,
        }

    return render_template('dashboard.html',
        departments=visible_departments_query().count(),
        semesters=visible_semesters_query().count(),
        subjects=visible_subjects_query().count(),
        teachers=visible_teachers_query().count(),
        students=visible_students_query().count(),
        sessions=len(scoped_sessions),
        attendance=sum(Attendance.query.filter_by(session_id=s.id).count() for s in scoped_sessions),
        recent_sessions=session_panel_rows(recent_sessions),
        recent_account_logs=recent_account_logs,
        accounts=accounts)

@app.route('/login', methods=['GET', 'POST'])
def login():
    next_url = request.form.get('next') or request.args.get('next')
    error = None

    if current_user.is_authenticated:
        return redirect(next_url if is_safe_redirect_target(next_url) else url_for('dashboard'))

    if request.method == 'POST':
        username = normalize_email(request.form.get('username'))
        password = request.form.get('password', '')
        admin = Admin.query.filter(db.func.lower(Admin.username) == username).first()
        if admin and not admin.is_active:
            error = "This account is inactive. Please contact the Principal or HOD."
        elif admin and is_account_locked(admin):
            error = f"Too many failed login attempts. Try again after {format_12h(admin.locked_until, include_date=False)}."
        elif admin and check_password_hash(admin.password, password):
            admin.failed_login_attempts = 0
            admin.locked_until = None
            db.session.commit()
            login_user(admin)
            session.pop('_flashes', None)
            session['show_password_reminder'] = bool(admin.must_change_password)
            return redirect(next_url if is_safe_redirect_target(next_url) else url_for('dashboard'))
        else:
            if admin:
                admin.failed_login_attempts = (admin.failed_login_attempts or 0) + 1
                if admin.failed_login_attempts >= 5:
                    admin.locked_until = get_ist_now() + timedelta(minutes=10)
                    admin.failed_login_attempts = 0
                db.session.commit()
            error = "Invalid username/email or password"
    return render_template('login.html', error=error, next_url=next_url)

@app.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('login'))


@app.route('/student/attendance')
@login_required
@role_required('student')
def student_attendance():
    if not current_user.student:
        abort(403)
    summary = build_student_attendance_summary(current_user.student)
    return render_template('student_dashboard.html', student=current_user.student, summary=summary)

# ── Departments ──────────────────────────────────────────────
@app.route('/account', methods=['GET', 'POST'])
@login_required
def account():
    message = None
    error = None

    if request.method == 'POST':
        current_password = request.form.get('current_password', '')
        new_password = request.form.get('new_password', '')
        confirm_password = request.form.get('confirm_password', '')

        if not current_password or not new_password or not confirm_password:
            error = "All password fields are required"
        elif not check_password_hash(current_user.password, current_password):
            error = "Current password is incorrect"
        elif len(new_password) < 6:
            error = "New password must be at least 6 characters"
        elif new_password != confirm_password:
            error = "New password and confirm password do not match"
        elif check_password_hash(current_user.password, new_password):
            error = "New password must be different from the current password"
        else:
            current_user.password = generate_password_hash(new_password)
            current_user.must_change_password = False
            current_user.failed_login_attempts = 0
            current_user.locked_until = None
            log_activity(
                'password_changed',
                f"{role_label(current_user.role)} account '{current_user.username}' changed its password.",
                target_type='admin',
                target_id=current_user.id,
                department_id=current_department_id()
            )
            db.session.commit()
            message = "Password updated successfully."

    return render_template(
        'account.html',
        profile=build_account_profile(current_user),
        security_logs=own_security_activity_logs_query().order_by(ActivityLog.created_at.desc()).limit(8).all(),
        message=message,
        error=error
    )

@app.route('/departments', methods=['GET', 'POST'])
@login_required
@role_required('super_admin', 'hod')
def departments():
    if request.method == 'POST':
        if not is_super_admin():
            abort(403)
        branch = normalize_branch(request.form.get('branch'))
        course_name = normalize_course_name(request.form.get('name'))
        if not branch or not course_name:
            flash("Branch and course name are required.", "danger")
            return redirect(url_for('departments'))
        existing = Department.query.filter(db.func.lower(Department.name) == course_name.lower()).first()
        if existing:
            flash(f"Department '{existing.display_name}' already exists.", "warning")
            return redirect(url_for('departments'))
        department = Department(branch=branch, name=course_name)
        db.session.add(department)
        log_activity('department_created', f"Created department '{department.display_name}'.", 'department', None)
        db.session.commit()
    return render_template('departments.html', departments=ordered_departments_query().all())

@app.route('/departments/edit/<int:id>', methods=['POST'])
@login_required
@role_required('super_admin')
def edit_department(id):
    d = Department.query.get_or_404(id)
    branch = normalize_branch(request.form.get('branch'))
    course_name = normalize_course_name(request.form.get('name'))
    if not branch or not course_name:
        flash("Branch and course name are required.", "danger")
        return redirect(url_for('departments'))
    existing = Department.query.filter(
        db.func.lower(Department.name) == course_name.lower(),
        Department.id != d.id
    ).first()
    if existing:
        flash(f"Department '{existing.display_name}' already exists.", "warning")
        return redirect(url_for('departments'))
    d.branch = branch
    d.name = course_name
    log_activity('department_updated', f"Updated department '{d.display_name}'.", 'department', d.id, d.id)
    db.session.commit()
    return redirect(url_for('departments'))

@app.route('/departments/delete/<int:id>', methods=['POST'])
@login_required
@role_required('super_admin')
def delete_department(id):
    d = Department.query.get_or_404(id)
    counts = {
        'admins': Admin.query.filter(Admin.department_id == id, Admin.role != 'super_admin').count(),
        'teachers': Teacher.query.filter_by(department_id=id).count(),
        'students': Student.query.filter_by(department_id=id).count(),
        'subjects': Subject.query.filter_by(department_id=id).count(),
        'semesters': Semester.query.filter_by(department_id=id).count(),
        'sessions': db.session.query(ClassSession).join(Subject).filter(Subject.department_id == id).count(),
        'attendance': db.session.query(Attendance).join(ClassSession).join(Subject).filter(Subject.department_id == id).count()
    }
    if any(counts.values()):
        flash(describe_delete_block(d.display_name, counts), 'warning')
        return redirect(url_for('departments'))

    log_activity('department_deleted', f"Deleted department '{d.display_name}'.", 'department', d.id)
    db.session.delete(d)
    db.session.commit()
    return redirect(url_for('departments'))

# ── Semesters ────────────────────────────────────────────────
@app.route('/semesters', methods=['GET', 'POST'])
@login_required
@role_required('super_admin', 'hod')
def semesters():
    if request.method == 'POST':
        department_id = parse_int_value(request.form.get('department_id')) if is_super_admin() else current_department_id()
        name = normalize_text(request.form.get('name'))
        if not name:
            flash("Semester name is required.", "danger")
            return redirect(url_for('semesters'))
        if not department_id:
            flash("Valid department is required.", "danger")
            return redirect(url_for('semesters'))
        if not can_access_department(department_id):
            abort(403)
        existing = Semester.query.filter(
            Semester.department_id == department_id,
            db.func.lower(Semester.name) == name.lower()
        ).first()
        if existing:
            flash(f"Semester '{name}' already exists for this department.", "warning")
            return redirect(url_for('semesters'))
        semester = Semester(name=name, department_id=department_id)
        db.session.add(semester)
        log_activity('semester_created', f"Created semester '{semester.name}' for {semester.department.display_name if semester.department else 'selected department'}.", 'semester', None, department_id)
        db.session.commit()
    return render_template('semesters.html',
        semesters=visible_semesters_query().order_by(Semester.department_id, Semester.name).all(),
        departments=ordered_departments_query().all())

@app.route('/semesters/edit/<int:id>', methods=['POST'])
@login_required
@role_required('super_admin', 'hod')
def edit_semester(id):
    s = Semester.query.get_or_404(id)
    if not can_access_department(s.department_id):
        abort(403)
    name = normalize_text(request.form.get('name'))
    if not name:
        flash("Semester name is required.", "danger")
        return redirect(url_for('semesters'))
    department_id = parse_int_value(request.form.get('department_id')) if is_super_admin() else current_department_id()
    if not department_id:
        flash("Valid department is required.", "danger")
        return redirect(url_for('semesters'))
    if not can_access_department(department_id):
        abort(403)
    existing = Semester.query.filter(
        Semester.id != s.id,
        Semester.department_id == department_id,
        db.func.lower(Semester.name) == name.lower()
    ).first()
    if existing:
        flash(f"Semester '{name}' already exists for this department.", "warning")
        return redirect(url_for('semesters'))
    s.name = name
    s.department_id = department_id
    log_activity('semester_updated', f"Updated semester '{s.name}'.", 'semester', s.id, department_id)
    db.session.commit()
    return redirect(url_for('semesters'))

@app.route('/semesters/delete/<int:id>', methods=['POST'])
@login_required
@role_required('super_admin', 'hod')
def delete_semester(id):
    semester = Semester.query.get_or_404(id)
    if not can_access_department(semester.department_id):
        abort(403)
    counts = {
        'students': Student.query.filter_by(semester_id=id).count(),
        'subjects': Subject.query.filter_by(semester_id=id).count(),
        'sessions': db.session.query(ClassSession).join(Subject).filter(Subject.semester_id == id).count(),
        'attendance': db.session.query(Attendance).join(ClassSession).join(Subject).filter(Subject.semester_id == id).count()
    }
    if any(counts.values()):
        flash(describe_delete_block(f"semester '{semester.name}'", counts), 'warning')
        return redirect(url_for('semesters'))

    log_activity('semester_deleted', f"Deleted semester '{semester.name}'.", 'semester', semester.id, semester.department_id)
    db.session.delete(semester)
    db.session.commit()
    return redirect(url_for('semesters'))

# ── Subjects ─────────────────────────────────────────────────
@app.route('/subjects', methods=['GET', 'POST'])
@login_required
@role_required('super_admin', 'hod', 'teacher')
def subjects():
    if request.method == 'POST':
        if user_role() == 'teacher':
            abort(403)
        department_id = parse_int_value(request.form.get('department_id')) if is_super_admin() else current_department_id()
        semester_id = parse_int_value(request.form.get('semester_id'))
        teacher_id = parse_int_value(request.form.get('teacher_id'))
        paper_code = normalize_paper_code(request.form.get('paper_code'))
        subject_name = normalize_text(request.form.get('name'))
        if not paper_code or not subject_name:
            flash("Paper code and subject name are required.", "danger")
            return redirect(url_for('subjects'))
        if len(paper_code) > 50:
            flash("Paper code must be 50 characters or fewer.", "danger")
            return redirect(url_for('subjects'))
        if not department_id or not semester_id:
            flash("Valid department and semester are required.", "danger")
            return redirect(url_for('subjects'))
        semester = Semester.query.get_or_404(semester_id)
        if not can_access_department(department_id) or semester.department_id != department_id:
            abort(403)
        existing = Subject.query.filter(
            Subject.department_id == department_id,
            Subject.semester_id == semester_id,
            db.func.lower(Subject.name) == subject_name.lower()
        ).first()
        if existing:
            flash(f"Subject '{subject_name}' already exists for this semester.", "warning")
            return redirect(url_for('subjects'))
        if paper_code:
            existing_code = Subject.query.filter(
                Subject.department_id == department_id,
                db.func.upper(Subject.paper_code) == paper_code
            ).first()
            if existing_code:
                flash(f"Paper code '{paper_code}' already exists for this department.", "warning")
                return redirect(url_for('subjects'))
        if teacher_id:
            teacher = Teacher.query.get_or_404(teacher_id)
            if teacher.department_id != department_id or not can_access_teacher(teacher):
                abort(403)
            teacher_id = teacher.id
        subject = Subject(
            name=subject_name,
            paper_code=paper_code or None,
            department_id=department_id,
            semester_id=semester_id,
            teacher_id=teacher_id)
        db.session.add(subject)
        teacher_suffix = f" and assigned to {subject.teacher.name}" if subject.teacher else ""
        log_activity('subject_created', f"Created subject '{subject_display_name(subject)}'{teacher_suffix}.", 'subject', None, department_id)
        db.session.commit()
    return render_template('subjects.html',
        subjects=visible_subjects_query().order_by(Subject.paper_code, Subject.name).all(),
        departments=ordered_departments_query().all(),
        semesters=visible_semesters_query().order_by(Semester.name).all(),
        teachers=visible_teachers_query().order_by(Teacher.name).all())

@app.route('/subjects/edit/<int:id>', methods=['POST'])
@login_required
@role_required('super_admin', 'hod')
def edit_subject(id):
    s = Subject.query.get_or_404(id)
    if not can_access_department(s.department_id):
        abort(403)
    department_id = parse_int_value(request.form.get('department_id')) if is_super_admin() else current_department_id()
    semester_id = parse_int_value(request.form.get('semester_id'))
    teacher_id = parse_int_value(request.form.get('teacher_id'))
    paper_code = normalize_paper_code(request.form.get('paper_code'))
    subject_name = normalize_text(request.form.get('name'))
    if not paper_code or not subject_name:
        flash("Paper code and subject name are required.", "danger")
        return redirect(url_for('subjects'))
    if len(paper_code) > 50:
        flash("Paper code must be 50 characters or fewer.", "danger")
        return redirect(url_for('subjects'))
    if not department_id or not semester_id:
        flash("Valid department and semester are required.", "danger")
        return redirect(url_for('subjects'))
    semester = Semester.query.get_or_404(semester_id)
    if not can_access_department(department_id) or semester.department_id != department_id:
        abort(403)
    existing = Subject.query.filter(
        Subject.id != s.id,
        Subject.department_id == department_id,
        Subject.semester_id == semester_id,
        db.func.lower(Subject.name) == subject_name.lower()
    ).first()
    if existing:
        flash(f"Subject '{subject_name}' already exists for this semester.", "warning")
        return redirect(url_for('subjects'))
    if paper_code:
        existing_code = Subject.query.filter(
            Subject.id != s.id,
            Subject.department_id == department_id,
            db.func.upper(Subject.paper_code) == paper_code
        ).first()
        if existing_code:
            flash(f"Paper code '{paper_code}' already exists for this department.", "warning")
            return redirect(url_for('subjects'))
    if teacher_id:
        teacher = Teacher.query.get_or_404(teacher_id)
        if teacher.department_id != department_id or not can_access_teacher(teacher):
            abort(403)
        teacher_id = teacher.id
    s.name = subject_name
    s.paper_code = paper_code or None
    s.department_id = department_id
    s.semester_id = semester_id
    s.teacher_id = teacher_id
    teacher_suffix = f" Assigned teacher: {s.teacher.name}." if s.teacher else " No teacher assigned."
    log_activity('subject_updated', f"Updated subject '{subject_display_name(s)}'.{teacher_suffix}", 'subject', s.id, department_id)
    db.session.commit()
    return redirect(url_for('subjects'))

@app.route('/subjects/delete/<int:id>', methods=['POST'])
@login_required
@role_required('super_admin', 'hod')
def delete_subject(id):
    subject = Subject.query.get_or_404(id)
    if not can_access_department(subject.department_id):
        abort(403)
    counts = {
        'sessions': ClassSession.query.filter_by(subject_id=id).count(),
        'attendance': db.session.query(Attendance).join(ClassSession).filter(ClassSession.subject_id == id).count()
    }
    if any(counts.values()):
        flash(describe_delete_block(f"subject '{subject.name}'", counts), 'warning')
        return redirect(url_for('subjects'))
    log_activity('subject_deleted', f"Deleted subject '{subject_display_name(subject)}'.", 'subject', subject.id, subject.department_id)
    db.session.delete(subject)
    db.session.commit()
    return redirect(url_for('subjects'))

# ── Teachers ─────────────────────────────────────────────────
@app.route('/teachers')
@login_required
@role_required('super_admin', 'hod')
def teachers():
    return render_template('teachers.html',
        teachers=visible_teachers_query().order_by(Teacher.name).all(),
        departments=ordered_departments_query().all(),
        error=None)

@app.route('/teachers/edit/<int:id>', methods=['POST'])
@login_required
@role_required('super_admin', 'hod')
def edit_teacher(id):
    t = Teacher.query.get_or_404(id)
    if not can_access_teacher(t):
        abort(403)
    department_id = parse_int_value(request.form.get('department_id')) if is_super_admin() else current_department_id()
    if not department_id:
        flash("Valid department is required.", "danger")
        return redirect(url_for('teachers'))
    if not can_access_department(department_id):
        abort(403)
    name = normalize_text(request.form.get('name'))
    email = normalize_email(request.form.get('email'))
    if not name:
        flash("Teacher name is required.", "danger")
        return redirect(url_for('teachers'))
    if not is_allowed_staff_email(email):
        flash(staff_email_rule_message(), "danger")
        return redirect(url_for('teachers'))
    duplicate_teacher = Teacher.query.filter(Teacher.id != t.id, db.func.lower(Teacher.email) == email).first()
    if duplicate_teacher:
        flash(f"Teacher email '{email}' already exists.", "warning")
        return redirect(url_for('teachers'))
    duplicate_admin = Admin.query.filter(db.func.lower(Admin.username) == email).filter(or_(Admin.teacher_id.is_(None), Admin.teacher_id != t.id)).first()
    if duplicate_admin:
        flash(f"Account email '{email}' already exists.", "warning")
        return redirect(url_for('teachers'))
    if department_id != t.department_id:
        assigned_subjects = Subject.query.filter_by(teacher_id=t.id).count()
        sessions_count = ClassSession.query.filter_by(teacher_id=t.id).count()
        if assigned_subjects or sessions_count:
            flash(
                describe_delete_block(
                    f"moving teacher '{t.name}'",
                    {'assigned subjects': assigned_subjects, 'sessions': sessions_count}
                ),
                'warning'
            )
            return redirect(url_for('teachers'))
    t.name = name
    t.email = email
    t.department_id = department_id
    linked_admin = Admin.query.filter_by(teacher_id=t.id).first()
    if linked_admin:
        linked_admin.username = email
        linked_admin.department_id = department_id
    log_activity('teacher_updated', f"Updated teacher profile '{t.name}'.", 'teacher', t.id, department_id)
    db.session.commit()
    return redirect(url_for('teachers'))

@app.route('/teachers/delete/<int:id>', methods=['POST'])
@login_required
@role_required('super_admin', 'hod')
def delete_teacher(id):
    teacher = Teacher.query.get_or_404(id)
    if not can_access_teacher(teacher):
        abort(403)
    assigned_subjects = Subject.query.filter_by(teacher_id=teacher.id).count()
    sessions_count = ClassSession.query.filter_by(teacher_id=teacher.id).count()
    if assigned_subjects or sessions_count:
        flash(
            describe_delete_block(
                f"teacher '{teacher.name}'",
                {'assigned subjects': assigned_subjects, 'sessions': sessions_count}
            ),
            'warning'
        )
        return redirect(url_for('teachers'))
    log_activity('teacher_deleted', f"Deleted teacher profile '{teacher.name}'.", 'teacher', teacher.id, teacher.department_id)
    Admin.query.filter_by(teacher_id=teacher.id).delete()
    db.session.delete(teacher)
    db.session.commit()
    return redirect(url_for('teachers'))


# ── Students ─────────────────────────────────────────────────
@app.route('/students', methods=['GET', 'POST'])
@login_required
@role_required('super_admin', 'hod', 'teacher')
def students():
    error = None
    if request.method == 'POST':
        try:
            dept_id = parse_int_value(request.form.get('department_id')) if is_super_admin() else current_department_id()
            sem_id = parse_int_value(request.form.get('semester_id'))
            name = normalize_text(request.form.get('name'))
            roll_no = normalize_roll_no(request.form.get('roll_no'))
            email = normalize_email(request.form.get('email'))
            
            # Debug: Check what department and semester are selected
            department = Department.query.get(dept_id)
            semester = Semester.query.get(sem_id)
            
            if not name or not roll_no or not email:
                error = "Name, roll number, and email are required"
            elif not is_valid_email(email):
                error = email_format_rule_message()
            elif not dept_id:
                error = "Valid department is required"
            elif not sem_id:
                error = "Valid semester is required"
            elif not department:
                error = f"Department with ID {dept_id} not found"
            elif not semester:
                error = f"Semester with ID {sem_id} not found"
            elif semester.department_id != dept_id:
                error = f"Semester '{semester.name}' belongs to '{Semester.query.get(sem_id).department.display_name}', not '{department.display_name}'"
            else:
                # Check if roll_no exists in same department and semester (case-insensitive)
                existing = Student.query.filter(
                    db.func.upper(Student.roll_no) == roll_no,
                    Student.department_id == dept_id,
                    Student.semester_id == sem_id
                ).first()
                if existing:
                    error = f"Roll number {roll_no} already exists in {department.display_name} - {semester.name}"
                else:
                    # Check if email already exists
                    existing_email = Student.query.filter(db.func.lower(Student.email) == email).first()
                    if existing_email:
                        error = f"Email {email} already exists"
                    elif Admin.query.filter(db.func.lower(Admin.username) == email).first():
                        error = f"Login account email {email} already exists"
                    else:
                        student = Student(
                            name=name,
                            roll_no=roll_no,
                            email=email,
                            department_id=dept_id,
                            semester_id=sem_id)
                        db.session.add(student)
                        db.session.flush()
                        ensure_student_account(student)
                        log_activity(
                            'student_created',
                            f"Created student profile '{student.name}' ({student.roll_no}).",
                            'student',
                            student.id,
                            dept_id
                        )
                        db.session.commit()
        except ValueError as e:
            db.session.rollback()
            error = f"Invalid input: {str(e)}"
        except Exception as e:
            db.session.rollback()
            error = f"Error: {str(e)}"
    
    return render_template('students.html',
        students=visible_students_query().order_by(Student.roll_no).all(),
        departments=ordered_departments_query().all(),
        semesters=visible_semesters_query().order_by(Semester.name).all(),
        error=error)


@app.route('/students/import', methods=['POST'])
@login_required
@role_required('super_admin', 'hod', 'teacher')
def import_students():
    upload = request.files.get('student_file')
    if not upload or not upload.filename:
        flash("Please choose a CSV or XLSX file.", "danger")
        return redirect(url_for('students'))

    try:
        rows = parse_student_import(upload)
        if not rows:
            flash("The uploaded file has no student rows.", "warning")
            return redirect(url_for('students'))

        imported, errors = import_students_from_rows(rows)
        if imported:
            flash(f"Imported {imported} student profile{'s' if imported != 1 else ''}. Student logins were created with roll number as initial password.", "success")
            log_activity('student_imported', f"Imported {imported} student profile(s) by bulk upload.", 'student', None, current_department_id())
        if errors:
            preview = '; '.join(errors[:5])
            suffix = f" and {len(errors) - 5} more" if len(errors) > 5 else ""
            flash(f"{len(errors)} row issue{'s' if len(errors) != 1 else ''}: {preview}{suffix}", "warning")
    except Exception as exc:
        db.session.rollback()
        flash(f"Import failed: {exc}", "danger")

    return redirect(url_for('students'))


@app.route('/students/import-template')
@login_required
@role_required('super_admin', 'hod', 'teacher')
def student_import_template():
    output = io.StringIO()
    writer = csv.writer(output)
    headers = ['name', 'roll_no', 'email', 'semester']
    sample = ['Rahul Das', 'MCA2026001', 'mca2026001@rcciit.org.in', 'Semester 1']
    if is_super_admin():
        headers.append('department')
        sample.append('MCA - Master of Computer Application (MCA)')
    writer.writerow(headers)
    writer.writerow(sample)

    buffer = io.BytesIO(output.getvalue().encode('utf-8'))
    return send_file(
        buffer,
        mimetype='text/csv',
        download_name='student_import_template.csv',
        as_attachment=True
    )

@app.route('/students/edit/<int:id>', methods=['POST'])
@login_required
@role_required('super_admin', 'hod')
def edit_student(id):
    s = Student.query.get_or_404(id)
    if not can_access_student(s):
        abort(403)
    name = normalize_text(request.form.get('name'))
    roll_no = normalize_roll_no(request.form.get('roll_no'))
    email = normalize_email(request.form.get('email'))
    dept_id = parse_int_value(request.form.get('department_id')) if is_super_admin() else current_department_id()
    sem_id = parse_int_value(request.form.get('semester_id'))
    if not name or not roll_no or not email:
        flash("Name, roll number, and email are required.", "danger")
        return redirect(url_for('students'))
    if not is_valid_email(email):
        flash(email_format_rule_message(), "danger")
        return redirect(url_for('students'))
    if not dept_id or not sem_id:
        flash("Valid department and semester are required.", "danger")
        return redirect(url_for('students'))
    if not can_access_department(dept_id):
        abort(403)
    
    # Validate semester belongs to department
    semester = Semester.query.get(sem_id)
    if not semester or semester.department_id != dept_id:
        flash("Selected semester does not belong to the selected department.", "danger")
        return redirect(url_for('students'))
    
    # Check if roll_no changed and exists in same dept/sem (case-insensitive)
    if (s.roll_no.upper() != roll_no or s.department_id != dept_id or s.semester_id != sem_id):
        existing = Student.query.filter(
            db.func.upper(Student.roll_no) == roll_no,
            Student.department_id == dept_id,
            Student.semester_id == sem_id
        ).first()
        if existing:
            flash(f"Roll number {roll_no} already exists in this department and semester.", "warning")
            return redirect(url_for('students'))
    
    # Check if email changed and already exists
    if normalize_email(s.email) != email:
        existing_email = Student.query.filter(db.func.lower(Student.email) == email).first()
        if existing_email:
            flash(f"Email {email} already exists.", "warning")
            return redirect(url_for('students'))
        existing_login = Admin.query.filter(
            db.func.lower(Admin.username) == email,
            or_(Admin.student_id.is_(None), Admin.student_id != s.id)
        ).first()
        if existing_login:
            flash(f"Login account email {email} already exists.", "warning")
            return redirect(url_for('students'))
    
    s.name = name
    s.roll_no = roll_no
    s.email = email
    s.department_id = dept_id
    s.semester_id = sem_id
    ensure_student_account(s)
    log_activity('student_updated', f"Updated student profile '{s.name}' ({s.roll_no}).", 'student', s.id, dept_id)
    db.session.commit()
    return redirect(url_for('students'))


@app.route('/students/delete/<int:id>', methods=['POST'])
@login_required
@role_required('super_admin', 'hod')
def delete_student(id):
    student = Student.query.get_or_404(id)
    if not can_access_student(student):
        abort(403)
    log_activity('student_deleted', f"Deleted student profile '{student.name}' ({student.roll_no}).", 'student', student.id, student.department_id)
    Admin.query.filter_by(student_id=student.id).delete()
    db.session.delete(student)
    db.session.commit()
    return redirect(url_for('students'))


@app.route('/students/reset-password/<int:id>', methods=['POST'])
@login_required
@role_required('super_admin', 'hod', 'teacher')
def reset_student_password(id):
    student = Student.query.get_or_404(id)
    if not can_access_student(student):
        abort(403)

    ensure_student_account(student)
    admin = Admin.query.filter_by(student_id=student.id).first()
    if not admin:
        abort(500)

    admin.password = generate_password_hash(student.roll_no)
    admin.is_active = True
    admin.must_change_password = True
    admin.failed_login_attempts = 0
    admin.locked_until = None
    log_activity(
        'student_password_reset',
        f"Reset student password for '{student.name}' to the roll number.",
        'student',
        student.id,
        student.department_id
    )
    db.session.commit()
    flash(f"Password for {student.name} was reset to the roll number ({student.roll_no}).", 'success')
    return redirect(url_for('students'))

# ── API: filtered dropdowns ───────────────────────────────────
@app.route('/api/semesters/<int:department_id>')
@login_required
@role_required('super_admin', 'hod', 'teacher')
def api_semesters(department_id):
    if not can_access_department(department_id):
        abort(403)
    rows = Semester.query.filter_by(department_id=department_id).order_by(Semester.name).all()
    return jsonify([{'id': r.id, 'name': r.name} for r in rows])

@app.route('/api/subjects/<int:semester_id>')
@login_required
@role_required('super_admin', 'hod', 'teacher')
def api_subjects(semester_id):
    semester = Semester.query.get_or_404(semester_id)
    if not can_access_department(semester.department_id):
        abort(403)
    rows = visible_subjects_query().filter(Subject.semester_id == semester_id).order_by(Subject.paper_code, Subject.name).all()
    return jsonify([{
        'id': r.id,
        'name': r.name,
        'paper_code': r.paper_code or '',
        'display_name': subject_display_name(r),
        'teacher_id': r.teacher_id,
        'teacher_name': r.teacher.name if r.teacher else ''
    } for r in rows])


@app.route('/api/department-subjects/<int:department_id>')
@login_required
@role_required('super_admin', 'hod')
def api_department_subjects(department_id):
    if not can_access_department(department_id):
        abort(403)
    rows = Subject.query.filter(
        Subject.department_id == department_id,
        Subject.name != SPECIAL_CLASS_SUBJECT_NAME
    ).order_by(Subject.paper_code, Subject.name).all()
    return jsonify([{
        'id': r.id,
        'name': r.name,
        'paper_code': r.paper_code or '',
        'display_name': subject_display_name(r),
        'semester': r.semester.name if r.semester else '',
        'teacher_id': r.teacher_id,
        'teacher_name': r.teacher.name if r.teacher else ''
    } for r in rows])

@app.route('/api/teachers/<int:department_id>')
@login_required
@role_required('super_admin', 'hod', 'teacher')
def api_teachers(department_id):
    if not can_access_department(department_id):
        abort(403)
    rows = Teacher.query.filter_by(department_id=department_id)
    if user_role() == 'teacher':
        rows = rows.filter(Teacher.id == current_teacher_id())
    rows = rows.order_by(Teacher.name).all()
    return jsonify([{'id': r.id, 'name': r.name} for r in rows])

# ── QR & Attendance ──────────────────────────────────────────
@app.route('/generate_qr', methods=['GET', 'POST'])
@login_required
@role_required('super_admin', 'hod', 'teacher')
def generate_qr():
    qr_image = session_id = scan_url = subject_name = duration = None
    view_session = None
    attendance_records = []
    
    # Check if viewing existing session
    view_session_id = request.args.get('session_id', type=int)
    if view_session_id:
        cs = ClassSession.query.get(view_session_id)
        if cs:
            if not can_access_session(cs):
                abort(403)
            qr_image = f"static/qrcodes/{cs.token}.png"
            session_id = cs.id
            subject_name = session_display_name(cs)
            scan_url = build_scan_url(cs.token)
            duration = int((cs.expires_at - cs.created_at).total_seconds() / 60)
            view_session = cs
            attendance_records = Attendance.query.filter_by(session_id=cs.id).order_by(Attendance.timestamp.desc()).all()

    if request.method == 'POST':
        token = str(uuid.uuid4())
        class_mode = request.form.get('class_mode', 'regular')
        department_id = parse_int_value(request.form.get('department_id'))
        semester_id = parse_int_value(request.form.get('semester_id'))
        subject_id = parse_int_value(request.form.get('subject_id'))
        special_title = normalize_text(request.form.get('special_title'))
        try:
            duration = int(request.form.get('duration', 30))
        except (TypeError, ValueError):
            abort(400, description='Duration must be a number.')
        if duration < MIN_QR_DURATION or duration > MAX_QR_DURATION:
            abort(400, description=f'Duration must be between {MIN_QR_DURATION} and {MAX_QR_DURATION} minutes.')
        teacher_id = parse_int_value(request.form.get('teacher_id'))

        if not department_id or not semester_id:
            abort(400, description='Department and semester are required.')
        if not can_access_department(department_id):
            abort(403)
        semester = Semester.query.get_or_404(semester_id)
        if semester.department_id != department_id:
            abort(403)

        if class_mode == 'special':
            if not special_title:
                abort(400, description='Special class details are required.')
            subject = get_or_create_special_subject(department_id, semester_id)
        else:
            if not subject_id:
                abort(400, description='Subject is required.')
            subject = Subject.query.get_or_404(subject_id)
            if not can_access_subject(subject) or subject.department_id != department_id or subject.semester_id != semester_id:
                abort(403)
            special_title = None
            if user_role() != 'teacher' and subject.teacher_id and not teacher_id:
                teacher_id = subject.teacher_id

        if user_role() == 'teacher':
            teacher_id = current_teacher_id()
        elif teacher_id:
            teacher = Teacher.query.get_or_404(teacher_id)
            if not can_access_teacher(teacher) or teacher.department_id != subject.department_id:
                abort(403)
        elif class_mode != 'special':
            abort(400, description='Teacher is required for regular classes.')

        # Use IST timezone (stored as naive datetime)
        now_ist = get_ist_now()
        expires_ist = now_ist + timedelta(minutes=duration)
        created_session_name = special_title or subject_display_name(subject)

        cs = ClassSession(
            subject_id=subject.id,
            special_title=special_title,
            token=token,
            teacher_id=teacher_id,
            created_at=now_ist,
            expires_at=expires_ist
        )
        db.session.add(cs)
        log_activity(
            'qr_session_created',
            f"Generated QR session for '{created_session_name}'.",
            'class_session',
            None,
            department_id
        )
        db.session.commit()
        
        scan_url = build_scan_url(token)
        img = qrcode.make(scan_url)
        path = f"static/qrcodes/{token}.png"
        img.save(path)
        
        # Redirect to show the QR code persistently
        return redirect(url_for('generate_qr', session_id=cs.id))
    
    # Get recent sessions
    recent_sessions = visible_sessions_query().order_by(ClassSession.created_at.desc()).limit(10).all()
    
    return render_template('generate_qr.html',
        qr_image=qr_image, session_id=session_id,
        scan_url=scan_url, subject=subject_name, duration=duration,
        view_session=view_session,
        attendance_records=attendance_records,
        departments=ordered_departments_query().all(),
        recent_sessions=recent_sessions,
        current_teacher=current_user.teacher if user_role() == 'teacher' else None)



@app.route('/deactivate_session/<int:id>', methods=['POST'])
@login_required
@role_required('super_admin', 'hod', 'teacher')
def deactivate_session(id):
    cs = ClassSession.query.get_or_404(id)
    if not can_access_session(cs):
        abort(403)
    
    if cs.is_active and cs.expires_at > get_ist_now():
        cs.is_active = False
        log_activity(
            'qr_session_deactivated',
            f"Deactivated QR session for '{session_display_name(cs)}'.",
            'class_session',
            cs.id,
            cs.subject.department_id if cs.subject else None
        )
        db.session.commit()
        flash("Session deactivated successfully.", "success")
        
    return redirect(url_for('generate_qr'))

@app.route('/reactivate_session/<int:id>', methods=['POST'])
@login_required
@role_required('super_admin', 'hod', 'teacher')
def reactivate_session(id):
    cs = ClassSession.query.get_or_404(id)
    if not can_access_session(cs):
        abort(403)
    
    if not cs.is_active and cs.expires_at > get_ist_now():
        cs.is_active = True
        log_activity(
            'qr_session_reactivated',
            f"Reactivated QR session for '{session_display_name(cs)}'.",
            'class_session',
            cs.id,
            cs.subject.department_id if cs.subject else None
        )
        db.session.commit()
        flash("Session reactivated successfully.", "success")
        
    return redirect(url_for('generate_qr'))

@app.route('/delete_session/<int:id>', methods=['POST'])
@login_required
@role_required('super_admin', 'hod', 'teacher')
def delete_session(id):
    cs = ClassSession.query.get_or_404(id)
    if not can_access_session(cs):
        abort(403)
    
    # Delete QR code image file
    qr_path = f"static/qrcodes/{cs.token}.png"
    if os.path.exists(qr_path):
        os.remove(qr_path)
    
    # Delete attendance records for this session
    Attendance.query.filter_by(session_id=id).delete()
    
    # Delete session
    log_activity(
        'qr_session_deleted',
        f"Deleted QR session for '{session_display_name(cs)}'.",
        'class_session',
        cs.id,
        cs.subject.department_id if cs.subject else None
    )
    db.session.delete(cs)
    db.session.commit()
    
    return redirect(url_for('generate_qr'))



@app.route('/scan/<token>')
def scan_qr(token):
    cs = ClassSession.query.filter_by(token=token).first()
    now_ist = get_ist_now()

    if not cs or not cs.subject:
        return render_template(
            'scan_qr.html',
            token=token,
            is_active=False,
            status_title='QR Expired',
            status_message='This QR code is expired or invalid. Please ask your teacher for a new QR code.',
            session_info=None
        )

    session_info = {
        'subject': session_display_name(cs),
        'department': cs.subject.department.display_name,
        'semester': cs.subject.semester.name,
        'created_at': format_12h(cs.created_at),
        'expires_at': format_12h(cs.expires_at)
    }
    is_active = cs.is_active and now_ist <= cs.expires_at

    if is_active and not current_user.is_authenticated:
        return redirect(url_for('login', next=request.path))

    can_mark_attendance = bool(
        is_active
        and current_user.is_authenticated
        and user_role() == 'student'
        and current_user.student
    )
    student_scope_error = None
    already_marked = False

    if can_mark_attendance:
        student = current_user.student
        if student.department_id != cs.subject.department_id or student.semester_id != cs.subject.semester_id:
            can_mark_attendance = False
            student_scope_error = f'This QR code is only for {cs.subject.department.display_name} - {cs.subject.semester.name}.'
        else:
            already_marked = bool(Attendance.query.filter_by(student_id=student.id, session_id=cs.id).first())

    if is_active and current_user.is_authenticated and user_role() != 'student':
        status_title = 'Student Login Required'
        status_message = 'Attendance marking is only available for student accounts.'
    elif is_active and student_scope_error:
        status_title = 'Wrong Class'
        status_message = student_scope_error
    elif is_active and already_marked:
        status_title = 'Attendance Already Marked'
        status_message = 'Your attendance has already been marked for this QR session.'
    else:
        if is_active:
            status_title = 'Mark Attendance'
            status_message = 'Confirm your student account to register attendance for this session.'
        elif now_ist > cs.expires_at:
            status_title = 'QR Expired'
            status_message = 'This QR code has expired. Please ask your teacher for a new QR code.'
        else:
            status_title = 'QR Deactivated'
            status_message = 'This QR session has been deactivated by the teacher.'

    return render_template(
        'scan_qr.html',
        token=token,
        is_active=is_active,
        can_mark_attendance=can_mark_attendance and not already_marked,
        already_marked=already_marked,
        student=current_user.student if current_user.is_authenticated and user_role() == 'student' else None,
        status_title=status_title,
        status_message=status_message,
        session_info=session_info
    )

@app.route('/mark_attendance', methods=['POST'])
def mark_attendance():
    if not current_user.is_authenticated:
        return jsonify({'success': False, 'message': 'Please log in with your student account before marking attendance.'}), 401
    if user_role() != 'student' or not current_user.student:
        return jsonify({'success': False, 'message': 'Attendance marking is only available for student accounts.'}), 403

    data = request.get_json(silent=True) or {}
    token = normalize_text(data.get('token'))

    if not token:
        return jsonify({'success': False, 'message': 'QR token is required'}), 400

    cs = ClassSession.query.filter_by(token=token).first()
    
    # Use IST timezone for comparison (naive datetime)
    now_ist = get_ist_now()
    
    if not cs or now_ist > cs.expires_at:
        return jsonify({'success': False, 'message': 'QR code expired or invalid'})

    subject = cs.subject
    if not subject:
        return jsonify({'success': False, 'message': 'QR code expired or invalid'})

    student = current_user.student
    if student.department_id != subject.department_id or student.semester_id != subject.semester_id:
        return jsonify({
            'success': False,
            'message': f'This QR code is only for {subject.department.display_name} - {subject.semester.name}.'
        })
    
    if Attendance.query.filter_by(student_id=student.id, session_id=cs.id).first():
        return jsonify({'success': False, 'message': 'Attendance already marked'})
    
    db.session.add(Attendance(student_id=student.id, session_id=cs.id, timestamp=now_ist))
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({'success': False, 'message': 'Attendance already marked'})
    return jsonify({'success': True, 'message': f'Attendance marked for {student.name}'})


@app.route('/reports')
@login_required
@role_required('super_admin', 'hod', 'teacher')
def reports():
    dept_id = request.args.get('department_id', type=int)
    sem_id = request.args.get('semester_id', type=int)
    subject_id = request.args.get('subject_id', type=int)
    date_from = request.args.get('date_from')
    date_to = request.args.get('date_to')
    date_error = None
    today = get_ist_now().date()

    # Validate dates
    parsed_from = None
    parsed_to = None
    if date_from:
        try:
            parsed_from = datetime.strptime(date_from, '%Y-%m-%d').date()
            if parsed_from > today:
                date_error = "From Date cannot be a future date."
                parsed_from = None
                date_from = ''
        except ValueError:
            date_from = ''
    if date_to:
        try:
            parsed_to = datetime.strptime(date_to, '%Y-%m-%d').date()
            if parsed_to > today:
                date_error = "To Date cannot be a future date."
                parsed_to = None
                date_to = ''
        except ValueError:
            date_to = ''
    if parsed_from and parsed_to and parsed_from > parsed_to:
        date_error = "From Date cannot be later than To Date."
        parsed_from = None
        parsed_to = None
        date_from = ''
        date_to = ''

    sessions = []
    stats = {}
    
    if dept_id and sem_id:
        if not can_access_department(dept_id):
            abort(403)
        # Get all sessions for this department and semester
        query = db.session.query(ClassSession).join(Subject).filter(
            Subject.department_id == dept_id,
            Subject.semester_id == sem_id
        )
        if user_role() == 'teacher':
            query = query.filter(ClassSession.teacher_id == current_teacher_id())
        
        # Apply filters
        if subject_id:
            query = query.filter(Subject.id == subject_id)
        if parsed_from:
            query = query.filter(ClassSession.created_at >= datetime.combine(parsed_from, datetime.min.time()))
        if parsed_to:
            query = query.filter(ClassSession.created_at < datetime.combine(parsed_to, datetime.min.time()) + timedelta(days=1))
        
        sessions = query.order_by(ClassSession.created_at.desc()).all()
        
        # Calculate statistics
        total_students = Student.query.filter_by(department_id=dept_id, semester_id=sem_id).count()
        total_sessions = len(sessions)
        total_attendance = sum([Attendance.query.filter_by(session_id=s.id).count() for s in sessions])
        
        stats = {
            'total_students': total_students,
            'total_sessions': total_sessions,
            'total_attendance': total_attendance,
            'avg_attendance': round(total_attendance / (total_sessions * total_students) * 100, 2) if total_sessions and total_students else 0
        }
    
    return render_template('reports.html',
        departments=ordered_departments_query().all(),
        semesters=visible_semesters_query().order_by(Semester.name).all(),
        subjects=visible_subjects_query().order_by(Subject.paper_code, Subject.name).all(),
        sessions=sessions,
        stats=stats,
        selected_dept=dept_id,
        selected_sem=sem_id,
        selected_subject=subject_id,
        date_from=date_from or '',
        date_to=date_to or '',
        date_error=date_error,
        today=today.isoformat())

@app.route('/api/session_attendance/<int:session_id>')
@login_required
@role_required('super_admin', 'hod', 'teacher')
def api_session_attendance(session_id):
    cs = ClassSession.query.get_or_404(session_id)
    if not can_access_session(cs):
        abort(403)
    attendances = Attendance.query.filter_by(session_id=session_id).all()
    
    data = {
        'session': {
            'id': cs.id,
            'subject': session_display_name(cs),
            'department': cs.subject.department.display_name,
            'semester': cs.subject.semester.name,
            'teacher': cs.teacher.name if cs.teacher else 'N/A',
            'created_at': format_12h(cs.created_at),
            'expires_at': format_12h(cs.expires_at)
        },
        'attendances': [{
            'id': a.id,
            'student_name': a.student.name,
            'student_roll': a.student.roll_no,
            'student_email': a.student.email,
            'timestamp': format_12h(a.timestamp)
        } for a in attendances],
        'total': len(attendances)
    }
    
    return jsonify(data)


@app.route('/reports/detailed')
@login_required
@role_required('super_admin', 'hod', 'teacher')
def detailed_report():
    dept_id = request.args.get('department_id', type=int)
    sem_id = request.args.get('semester_id', type=int)
    
    if not dept_id or not sem_id:
        return redirect(url_for('reports'))
    if not can_access_department(dept_id):
        abort(403)
    semester = Semester.query.get_or_404(sem_id)
    if semester.department_id != dept_id:
        abort(403)
    
    # Get all students in department/semester
    students = Student.query.filter_by(department_id=dept_id, semester_id=sem_id).all()
    
    # Get all sessions for this department/semester
    sessions = db.session.query(ClassSession).join(Subject).filter(
        Subject.department_id == dept_id,
        Subject.semester_id == sem_id
    )
    if user_role() == 'teacher':
        sessions = sessions.filter(ClassSession.teacher_id == current_teacher_id())
    sessions = sessions.order_by(ClassSession.created_at.desc()).all()
    
    # Build attendance matrix
    attendance_data = []
    for student in students:
        row = {
            'student': student,
            'attendance': {},
            'total': 0,
            'percentage': 0
        }
        for session in sessions:
            attended = Attendance.query.filter_by(
                student_id=student.id,
                session_id=session.id
            ).first()
            row['attendance'][session.id] = bool(attended)
            if attended:
                row['total'] += 1
        
        row['percentage'] = round((row['total'] / len(sessions) * 100), 2) if sessions else 0
        attendance_data.append(row)
    
    return render_template('detailed_report.html',
        dept=Department.query.get(dept_id),
        sem=semester,
        sessions=sessions,
        attendance_data=attendance_data)

@app.route('/export/<int:department_id>/<int:semester_id>/<fmt>')
@login_required
@role_required('super_admin', 'hod', 'teacher')
def export(department_id, semester_id, fmt):
    if not can_access_department(department_id):
        abort(403)
    if fmt not in ('excel', 'pdf'):
        abort(404)
    dept = Department.query.get_or_404(department_id)
    sem = Semester.query.get_or_404(semester_id)
    if sem.department_id != department_id:
        abort(403)
    # Read optional filters from query string (forwarded from the reports page)
    subject_id = request.args.get('subject_id', type=int)
    date_from  = request.args.get('date_from', '')
    date_to    = request.args.get('date_to', '')

    parsed_from = None
    parsed_to   = None
    today = get_ist_now().date()
    if date_from:
        try:
            parsed_from = datetime.strptime(date_from, '%Y-%m-%d').date()
            if parsed_from > today:
                parsed_from = None
        except ValueError:
            pass
    if date_to:
        try:
            parsed_to = datetime.strptime(date_to, '%Y-%m-%d').date()
            if parsed_to > today:
                parsed_to = None
        except ValueError:
            pass
    if parsed_from and parsed_to and parsed_from > parsed_to:
        parsed_from = parsed_to = None

    students = Student.query.filter_by(department_id=department_id, semester_id=semester_id).all()
    sessions = db.session.query(ClassSession).join(Subject).filter(
        Subject.department_id == department_id,
        Subject.semester_id == semester_id
    )
    if user_role() == 'teacher':
        sessions = sessions.filter(ClassSession.teacher_id == current_teacher_id())
    if subject_id:
        sessions = sessions.filter(Subject.id == subject_id)
    if parsed_from:
        sessions = sessions.filter(ClassSession.created_at >= datetime.combine(parsed_from, datetime.min.time()))
    if parsed_to:
        sessions = sessions.filter(ClassSession.created_at < datetime.combine(parsed_to, datetime.min.time()) + timedelta(days=1))
    sessions = sessions.order_by(ClassSession.created_at.desc()).all()
    
    # Build session column headers: paper code on line 1, subject name on line 2 (no date)
    def session_col_header(s):
        if getattr(s, 'special_title', None):
            return s.special_title
        sub = s.subject
        if not sub:
            return 'Special Class'
        paper_code = normalize_paper_code(getattr(sub, 'paper_code', None))
        if paper_code:
            return f"{sub.name}<br/>{paper_code}"
        return sub.name

    headers = ['Roll No', 'Name', 'Email'] + [session_col_header(s) for s in sessions] + ['Total', '%']
    data = [headers]
    
    for student in students:
        row = [student.roll_no, student.name, student.email]
        total = 0
        for session in sessions:
            attended = Attendance.query.filter_by(student_id=student.id, session_id=session.id).first()
            row.append('P' if attended else 'A')
            if attended:
                total += 1
        row.append(total)
        row.append(f"{round(total/len(sessions)*100, 1)}%" if sessions else "0%")
        data.append(row)
    
    # Use subject paper code in filename when a specific subject is filtered
    if subject_id and sessions:
        _fsub = sessions[0].subject
        _fcode = normalize_paper_code(getattr(_fsub, 'paper_code', None)) if _fsub else None
        _fbase = _fcode or (_fsub.name if _fsub else (dept.branch_name or dept.course_name))
    else:
        _fbase = dept.branch_name or dept.course_name
    if subject_id and sessions:
        filename = f'{_fbase.replace(" ", "_")}_attendance'
    else:
        filename = f'{_fbase.replace(" ", "_")}_{sem.name}_attendance'
    
    if fmt == 'excel':
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Attendance Report"
        
        for row_idx, row in enumerate(data, 1):
            for col_idx, value in enumerate(row, 1):
                cell = ws.cell(row=row_idx, column=col_idx, value=value)
                if row_idx == 1:  # Header
                    cell.font = openpyxl.styles.Font(bold=True)
                    cell.fill = openpyxl.styles.PatternFill(start_color="366092", end_color="366092", fill_type="solid")
                    cell.font = openpyxl.styles.Font(color="FFFFFF", bold=True)
        
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        return send_file(buf, download_name=f'{filename}.xlsx', as_attachment=True)
    
    elif fmt == 'pdf':
        from reportlab.lib import colors
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.platypus import Paragraph
        from reportlab.lib.enums import TA_CENTER, TA_LEFT

        buf = io.BytesIO()

        # Portrait A4 — subject filter keeps column count manageable
        PAGE_SIZE = A4
        LEFT_MARGIN = RIGHT_MARGIN = 1.5 * cm
        TOP_MARGIN = BOTTOM_MARGIN = 1.5 * cm
        usable_width = PAGE_SIZE[0] - LEFT_MARGIN - RIGHT_MARGIN

        doc = SimpleDocTemplate(
            buf,
            pagesize=PAGE_SIZE,
            leftMargin=LEFT_MARGIN,
            rightMargin=RIGHT_MARGIN,
            topMargin=TOP_MARGIN,
            bottomMargin=BOTTOM_MARGIN
        )

        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            'ReportTitle',
            parent=styles['Heading1'],
            fontSize=14,
            textColor=colors.HexColor('#366092'),
            spaceAfter=8,
            alignment=TA_LEFT
        )
        header_cell_style = ParagraphStyle(
            'HeaderCell',
            fontSize=7,
            textColor=colors.whitesmoke,
            alignment=TA_CENTER,
            leading=9
        )
        body_cell_style = ParagraphStyle(
            'BodyCell',
            fontSize=8,
            alignment=TA_CENTER,
            leading=10
        )

        # Wrap header cells in Paragraphs so long subject names word-wrap
        wrapped_data = []
        for row_idx, row in enumerate(data):
            wrapped_row = []
            for col_idx, cell in enumerate(row):
                cell_str = str(cell) if cell is not None else ''
                if row_idx == 0:
                    wrapped_row.append(Paragraph(cell_str, header_cell_style))
                else:
                    wrapped_row.append(Paragraph(cell_str, body_cell_style))
            wrapped_data.append(wrapped_row)

        # --- Dynamic column widths ---
        # Fixed narrow columns for Roll No, Name, Email, Total, %
        # Session columns take whatever is left — narrows them to prevent overflow
        num_cols     = len(data[0]) if data else 1
        num_sessions = max(num_cols - 5, 0)  # subtract Roll, Name, Email, Total, %

        col_roll  = 2.0 * cm   # enough for 'MCA2026006'
        col_name  = 3.2 * cm   # first/last name
        col_email = 4.2 * cm   # email
        col_total = 1.1 * cm   # 'Total'
        col_pct   = 1.6 * cm   # '%' — wide enough for '100.0%' on one line
        fixed_width = col_roll + col_name + col_email + col_total + col_pct

        if num_sessions > 0:
            # cap session width so it never exceeds 3.5 cm but takes equal share of rest
            session_col_width = min(
                max((usable_width - fixed_width) / num_sessions, 1.5 * cm),
                3.5 * cm
            )
        else:
            session_col_width = 0

        col_widths = (
            [col_roll, col_name, col_email]
            + [session_col_width] * num_sessions
            + [col_total, col_pct]
        )

        # Always stretch the table to fill the full page width
        total_w = sum(col_widths)
        if total_w > 0 and total_w < usable_width:
            scale = usable_width / total_w
            col_widths = [w * scale for w in col_widths]

        table = Table(wrapped_data, colWidths=col_widths, repeatRows=1)
        table.setStyle([
            # Header row
            ('BACKGROUND',    (0, 0), (-1, 0),  colors.HexColor('#366092')),
            ('TEXTCOLOR',     (0, 0), (-1, 0),  colors.whitesmoke),
            ('FONTNAME',      (0, 0), (-1, 0),  'Helvetica-Bold'),
            ('FONTSIZE',      (0, 0), (-1, 0),  7),
            ('BOTTOMPADDING', (0, 0), (-1, 0),  8),
            ('TOPPADDING',    (0, 0), (-1, 0),  8),
            # Data rows — alternating background
            ('BACKGROUND',    (0, 1), (-1, -1), colors.white),
            ('ROWBACKGROUNDS',(0, 1), (-1, -1), [colors.white, colors.HexColor('#EEF3FA')]),
            # Alignment
            ('ALIGN',         (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
            # Padding
            ('TOPPADDING',    (0, 1), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 1), (-1, -1), 5),
            # Grid
            ('GRID',          (0, 0), (-1, -1), 0.5, colors.HexColor('#AAAAAA')),
            ('LINEBELOW',     (0, 0), (-1, 0),  1.5, colors.HexColor('#1E3F66')),
        ])

        export_date = get_ist_now().strftime('%d/%m/%Y')

        # Show subject code in title when a specific subject is filtered
        if subject_id and sessions:
            filtered_subject = sessions[0].subject
            sub_paper_code = normalize_paper_code(getattr(filtered_subject, 'paper_code', None)) if filtered_subject else None
            title_subject = sub_paper_code or (filtered_subject.name if filtered_subject else dept.display_name)
        else:
            title_subject = dept.display_name

        report_title = Paragraph(
            f"Attendance Report \u2014 {title_subject} | {sem.name} | Exported on {export_date}",
            title_style
        )

        doc.build([report_title, Spacer(1, 0.3 * cm), table])
        buf.seek(0)
        return send_file(buf, download_name=f'{filename}.pdf', as_attachment=True)

@app.route('/add_admin', methods=['GET', 'POST'])
@login_required
@role_required('super_admin', 'hod')
def add_admin():
    message = None
    error = None
    allowed_roles = {
        key: ROLE_LABELS[key]
        for key in (('super_admin', 'hod', 'teacher') if is_super_admin() else ('teacher',))
    }
    
    if request.method == 'POST':
        username = normalize_email(request.form.get('username', ''))
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')
        role = request.form.get('role', 'teacher' if user_role() == 'hod' else 'super_admin')
        department_id = request.form.get('department_id') or None
        subject_id = parse_int_value(request.form.get('subject_id'))
        teacher_id = None
        teacher_name = normalize_text(request.form.get('teacher_name'))
        student_id = None
        
        # Validation
        if not username or not password:
            error = "Account email and password are required"
        elif role not in allowed_roles:
            error = "Invalid role selected"
        elif not is_allowed_staff_email(username):
            error = staff_email_rule_message()
        elif len(password) < 6:
            error = "Password must be at least 6 characters"
        elif password != confirm_password:
            error = "Passwords do not match"
        elif Admin.query.filter(db.func.lower(Admin.username) == username).first():
            error = f"Account email '{username}' already exists"
        else:
            try:
                if role == 'hod':
                    if not is_super_admin():
                        raise ValueError("Only Principal can create HOD accounts")
                    if not department_id:
                        raise ValueError("Department is required for HOD accounts")
                    department_id = parse_int_value(department_id)
                    if not department_id:
                        raise ValueError("Valid department is required for HOD accounts")
                    if has_active_hod(department_id):
                        raise ValueError("This department already has an active HOD. Deactivate the current HOD first.")
                    teacher_id = None
                    student_id = None
                elif role == 'teacher':
                    department_id = parse_int_value(department_id) if is_super_admin() else current_department_id()
                    if not teacher_name:
                        raise ValueError("Teacher name is required for teacher accounts")
                    if not department_id or not can_access_department(department_id):
                        raise ValueError("Valid department is required for teacher accounts")

                    teacher = Teacher.query.filter(db.func.lower(Teacher.email) == username).first()
                    if teacher:
                        if not can_access_teacher(teacher):
                            raise ValueError("You can create teacher accounts only for your department")
                        teacher.name = teacher_name
                        teacher.department_id = department_id
                    else:
                        teacher = Teacher(
                            name=teacher_name,
                            email=username,
                            department_id=department_id
                        )
                        db.session.add(teacher)
                        db.session.flush()

                    teacher_id = teacher.id
                    student_id = None
                    if subject_id:
                        subject = Subject.query.get_or_404(subject_id)
                        if subject.name == SPECIAL_CLASS_SUBJECT_NAME or subject.department_id != department_id:
                            raise ValueError("Please select a valid subject from the same department")
                        subject.teacher_id = teacher.id
                else:
                    if not is_super_admin():
                        raise ValueError("Only Principal can create Principal accounts")
                    department_id = None
                    teacher_id = None
                    student_id = None
                new_admin = Admin(
                    username=username,
                    password=generate_password_hash(password),
                    role=role,
                    department_id=department_id,
                    teacher_id=teacher_id,
                    student_id=student_id,
                    is_active=True,
                    must_change_password=True,
                    failed_login_attempts=0,
                    locked_until=None
                )
                db.session.add(new_admin)
                scope_department_id = department_id or (Teacher.query.get(teacher_id).department_id if teacher_id else None)
                log_activity(
                    'account_created',
                    f"Created {role_label(role)} account '{username}'.",
                    'admin',
                    None,
                    scope_department_id
                )
                db.session.commit()
                assigned_subject = None
                if role == 'teacher' and subject_id:
                    assigned_subject = Subject.query.get(subject_id)
                message = f"{role_label(role)} account '{username}' added successfully."
                if assigned_subject:
                    message += f" Subject assigned: {subject_display_name(assigned_subject)}."
                message += " The user will be reminded to change the password after login."
            except Exception as e:
                db.session.rollback()
                error = f"Error adding account: {str(e)}"

    if is_super_admin():
        admins = Admin.query.filter(Admin.role != 'student').order_by(Admin.role, Admin.is_active.desc(), Admin.username).all()
    else:
        admins = Admin.query.filter(
            Admin.department_id == current_department_id(),
            Admin.role != 'student'
        ).order_by(Admin.role, Admin.is_active.desc(), Admin.username).all()

    return render_template('add_admin.html', 
                         admins=admins, 
                         departments=ordered_departments_query().all(),
                         principal_count=Admin.query.filter_by(role='super_admin', is_active=True).count(),
                         roles=allowed_roles,
                         recent_logs=visible_activity_logs_query().order_by(ActivityLog.created_at.desc()).limit(12).all(),
                         message=message, 
                         error=error)


@app.route('/edit_admin/<int:id>', methods=['POST'])
@login_required
@role_required('super_admin', 'hod')
def edit_admin(id):
    admin = Admin.query.get_or_404(id)
    if not can_edit_account(admin):
        abort(403)

    username = normalize_email(request.form.get('username', ''))
    teacher_name = normalize_text(request.form.get('teacher_name'))
    department_id = request.form.get('department_id') or None
    subject_id = parse_int_value(request.form.get('subject_id'))

    if not username:
        flash("Account email is required.", "danger")
        return redirect(url_for('add_admin'))

    if not is_allowed_staff_email(username):
        flash(staff_email_rule_message(), "danger")
        return redirect(url_for('add_admin'))

    if Admin.query.filter(Admin.id != admin.id, db.func.lower(Admin.username) == username).first():
        flash(f"Account email '{username}' already exists.", "danger")
        return redirect(url_for('add_admin'))

    try:
        if admin.role == 'super_admin':
            admin.username = username
        elif admin.role == 'hod':
            if not is_super_admin():
                abort(403)
            if not department_id:
                raise ValueError("Department is required for HOD accounts")
            department_id = parse_int_value(department_id)
            if not department_id:
                raise ValueError("Valid department is required for HOD accounts")
            if not can_access_department(department_id):
                abort(403)
            if admin.is_active and has_active_hod(department_id, exclude_admin_id=admin.id):
                raise ValueError("This department already has an active HOD. Deactivate the current HOD first.")
            admin.username = username
            admin.department_id = department_id
        elif admin.role == 'teacher':
            if not department_id:
                raise ValueError("Department is required for Teacher accounts")
            department_id = parse_int_value(department_id) if is_super_admin() else current_department_id()
            if not teacher_name:
                raise ValueError("Teacher name is required for Teacher accounts")
            if not department_id:
                raise ValueError("Valid department is required for Teacher accounts")
            if not can_access_department(department_id):
                abort(403)

            teacher = admin.teacher
            if not teacher:
                teacher = Teacher(name=teacher_name, email=username, department_id=department_id)
                db.session.add(teacher)
                db.session.flush()
                admin.teacher_id = teacher.id

            if department_id != teacher.department_id:
                assigned_subjects = Subject.query.filter_by(teacher_id=teacher.id).count()
                sessions_count = ClassSession.query.filter_by(teacher_id=teacher.id).count()
                if assigned_subjects or sessions_count:
                    raise ValueError(
                        describe_delete_block(
                            f"moving teacher '{teacher.name}'",
                            {'assigned subjects': assigned_subjects, 'sessions': sessions_count}
                        )
                    )

            teacher.name = teacher_name
            teacher.email = username
            teacher.department_id = department_id
            admin.username = username
            admin.department_id = department_id

            if subject_id:
                subject = Subject.query.get_or_404(subject_id)
                if subject.name == SPECIAL_CLASS_SUBJECT_NAME or subject.department_id != department_id:
                    raise ValueError("Please select a valid subject from the same department")
                subject.teacher_id = teacher.id

        log_activity(
            'account_updated',
            f"Updated {role_label(admin.role)} account '{admin.username}'.",
            'admin',
            admin.id,
            admin.department_id
        )
        db.session.commit()
        flash(f"{role_label(admin.role)} account '{admin.username}' updated successfully.", "success")
    except Exception as exc:
        db.session.rollback()
        flash(f"Unable to update account: {exc}", "danger")

    return redirect(url_for('add_admin'))

@app.route('/delete_admin/<int:id>', methods=['POST'])
@login_required
@role_required('super_admin', 'hod')
def delete_admin(id):
    admin = Admin.query.get_or_404(id)
    if not can_manage_account(admin):
        abort(403)

    if admin.teacher_id:
        assigned_subjects = Subject.query.filter_by(teacher_id=admin.teacher_id).count()
        sessions_count = ClassSession.query.filter_by(teacher_id=admin.teacher_id).count()
        if assigned_subjects or sessions_count:
            flash(
                describe_delete_block(
                    f"account '{admin.username}'",
                    {'assigned subjects': assigned_subjects, 'sessions': sessions_count}
                ),
                'warning'
            )
            return redirect(url_for('add_admin'))

    log_activity(
        'account_deleted',
        f"Deleted {role_label(admin.role)} account '{admin.username}'.",
        'admin',
        admin.id,
        admin.department_id
    )
    db.session.delete(admin)
    db.session.commit()
    return redirect(url_for('add_admin'))


@app.route('/toggle_admin_status/<int:id>', methods=['POST'])
@login_required
@role_required('super_admin', 'hod')
def toggle_admin_status(id):
    admin = Admin.query.get_or_404(id)
    if not can_toggle_account_status(admin):
        abort(403)

    next_status = not admin.is_active
    if next_status and admin.role == 'hod' and admin.department_id and has_active_hod(admin.department_id, exclude_admin_id=admin.id):
        flash("This department already has an active HOD. Deactivate that HOD first.", 'warning')
        return redirect(url_for('add_admin'))

    admin.is_active = next_status
    log_activity(
        'account_status_changed',
        f"{'Activated' if next_status else 'Deactivated'} {role_label(admin.role)} account '{admin.username}'.",
        'admin',
        admin.id,
        admin.department_id
    )
    db.session.commit()
    flash(f"{role_label(admin.role)} account '{admin.username}' is now {'active' if next_status else 'inactive'}.", 'success')
    return redirect(url_for('add_admin'))


@app.route('/reset_admin_password/<int:id>', methods=['POST'])
@login_required
@role_required('super_admin', 'hod')
def reset_admin_password(id):
    admin = Admin.query.get_or_404(id)
    if not can_reset_staff_account(admin):
        abort(403)

    temporary_password = generate_temporary_password()
    admin.password = generate_password_hash(temporary_password)
    admin.is_active = True
    admin.must_change_password = True
    admin.failed_login_attempts = 0
    admin.locked_until = None
    log_activity(
        'account_password_reset',
        f"Reset password for {role_label(admin.role)} account '{admin.username}'.",
        'admin',
        admin.id,
        admin.department_id
    )
    db.session.commit()
    flash(f"Temporary password for {admin.username}: {temporary_password}", 'success')
    return redirect(url_for('add_admin'))



with app.app_context():
    db.create_all()
    ensure_rbac_columns()
    ensure_class_session_columns()
    ensure_department_columns()
    ensure_subject_columns()
    ensure_attendance_unique_index()
    backfill_subject_teacher_assignments()
    default_principal_username = normalize_email(app.config['DEFAULT_PRINCIPAL_EMAIL'])
    default_principal_password = app.config['DEFAULT_PRINCIPAL_PASSWORD']
    if not Admin.query.first():
        db.session.add(Admin(
            username=default_principal_username,
            password=generate_password_hash(default_principal_password),
            role='super_admin',
            is_active=True,
            must_change_password=False,
            failed_login_attempts=0
        ))
        db.session.commit()
    else:
        old_default = Admin.query.filter_by(username='admin', role='super_admin').first()
        if old_default and not Admin.query.filter(db.func.lower(Admin.username) == default_principal_username).first():
            old_default.username = default_principal_username
            old_default.password = generate_password_hash(default_principal_password)
            old_default.is_active = True
            old_default.must_change_password = False
            old_default.failed_login_attempts = 0
            old_default.locked_until = None
            db.session.commit()
    ensure_existing_student_accounts()

if __name__ == '__main__':
    os.makedirs('static/qrcodes', exist_ok=True)
    app.run(host='0.0.0.0', port=5000, debug=True)
