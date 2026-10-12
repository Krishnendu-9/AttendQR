"""Role-based access control, scoped queries, and display helpers."""
import re
from functools import wraps

from flask import abort, current_app
from flask_login import current_user
from sqlalchemy import and_, false, or_

from .extensions import db, login_manager
from .models import ActivityLog, Admin, ClassSession, Department, Semester, Student, Subject, Teacher
from .utils import infer_department_branch, ist_now, normalize_email, normalize_paper_code

ROLE_LABELS = {
    'super_admin': 'Principal',
    'hod': 'HOD',
    'teacher': 'Teacher',
    'student': 'Student',
}
STAFF_ROLES = ('super_admin', 'hod', 'teacher')
SPECIAL_CLASS_SUBJECT_NAME = '__SPECIAL_CLASS__'


def role_label(role):
    return ROLE_LABELS.get(role, role.replace('_', ' ').title() if role else 'User')


def user_role():
    """The current user's role, or None. Unknown or empty roles never gain access."""
    if not current_user.is_authenticated:
        return None
    role = getattr(current_user, 'role', None)
    return role if role in ROLE_LABELS else None


def is_super_admin():
    return user_role() == 'super_admin'


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


# ── Email rules ──────────────────────────────────────────────

EMAIL_PATTERN = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}$")


def allowed_staff_domains():
    return tuple(current_app.config.get('ALLOWED_STAFF_DOMAINS') or ())


def is_valid_email(value):
    email = normalize_email(value)
    if not email or len(email) > 120:
        return False
    return bool(EMAIL_PATTERN.fullmatch(email))


def is_allowed_staff_email(value):
    email = normalize_email(value)
    return is_valid_email(email) and any(email.endswith(domain) for domain in allowed_staff_domains())


def email_format_rule_message():
    return "Use a valid email address."


def staff_email_rule_message():
    return f"Use a valid email ending with {' or '.join(allowed_staff_domains())}."


def staff_email_html_pattern():
    """HTML pattern attribute matching the configured staff domains."""
    domains = '|'.join(domain.lstrip('@').replace('.', r'\.') for domain in allowed_staff_domains())
    return rf"^[^@\s]+@({domains})$" if domains else r"^[^@\s]+@[^@\s]+$"


# ── Scope ────────────────────────────────────────────────────

def current_department_id():
    if not current_user.is_authenticated:
        return None
    role = user_role()
    if role == 'teacher' and current_user.teacher:
        return current_user.teacher.department_id
    if role == 'student' and current_user.student:
        return current_user.student.department_id
    return current_user.department_id


def current_teacher_id():
    if current_user.is_authenticated and user_role() == 'teacher':
        return current_user.teacher_id
    return None


def can_access_department(department_id):
    if department_id is None:
        return False
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


def can_access_session(class_session):
    if not class_session or not class_session.subject:
        return False
    if user_role() == 'teacher':
        return (
            can_access_department(class_session.subject.department_id)
            and class_session.teacher_id == current_teacher_id()
        )
    return user_role() in ('super_admin', 'hod') and can_access_subject(class_session.subject)


def _department_filter(column):
    dept_id = current_department_id()
    return column == dept_id if dept_id else false()


def visible_departments_query():
    query = Department.query
    if not is_super_admin():
        query = query.filter(_department_filter(Department.id))
    return query


def ordered_departments_query():
    return visible_departments_query().order_by(Department.branch, Department.name)


def visible_semesters_query():
    query = Semester.query
    if not is_super_admin():
        query = query.filter(_department_filter(Semester.department_id))
    return query


def visible_subjects_query():
    query = Subject.query.filter(Subject.name != SPECIAL_CLASS_SUBJECT_NAME)
    if user_role() == 'teacher':
        query = query.filter(Subject.teacher_id == current_teacher_id())
    elif not is_super_admin():
        query = query.filter(_department_filter(Subject.department_id))
    return query


def visible_teachers_query():
    query = Teacher.query
    if user_role() == 'teacher':
        query = query.filter(Teacher.id == current_teacher_id())
    elif not is_super_admin():
        query = query.filter(_department_filter(Teacher.department_id))
    return query


def visible_students_query():
    query = Student.query
    if user_role() == 'student':
        query = query.filter(Student.id == current_user.student_id)
    elif not is_super_admin():
        query = query.filter(_department_filter(Student.department_id))
    return query


def visible_sessions_query():
    query = ClassSession.query.join(Subject)
    if user_role() == 'teacher':
        query = query.filter(ClassSession.teacher_id == current_teacher_id())
    elif not is_super_admin():
        query = query.filter(_department_filter(Subject.department_id))
    return query


def visible_activity_logs_query():
    query = ActivityLog.query.outerjoin(Admin, ActivityLog.actor_admin_id == Admin.id)
    if is_super_admin():
        return query
    if user_role() == 'hod':
        return query.filter(
            or_(
                ActivityLog.actor_admin_id == current_user.id,
                and_(ActivityLog.department_id == current_department_id(), Admin.role == 'teacher'),
            )
        )
    return query.filter(ActivityLog.actor_admin_id == current_user.id)


def own_security_activity_logs_query():
    return ActivityLog.query.filter(
        ActivityLog.action.in_(['password_changed', 'account_password_reset', 'account_status_changed']),
        or_(
            ActivityLog.actor_admin_id == current_user.id,
            and_(ActivityLog.target_type == 'admin', ActivityLog.target_id == current_user.id),
        ),
    )


# ── Account management rules ─────────────────────────────────

def has_active_hod(department_id, exclude_admin_id=None):
    query = Admin.query.filter_by(role='hod', department_id=department_id, is_active=True)
    if exclude_admin_id:
        query = query.filter(Admin.id != exclude_admin_id)
    return db.session.query(query.exists()).scalar()


def can_manage_account(admin):
    if user_role() == 'hod':
        return admin.role == 'teacher' and admin.department_id == current_department_id()
    if not is_super_admin():
        return False
    if admin.role == 'student' or admin.id == current_user.id:
        return False
    if (
        admin.role == 'super_admin'
        and admin.is_active
        and Admin.query.filter_by(role='super_admin', is_active=True).count() <= 1
    ):
        return False
    return True


def can_edit_account(admin):
    if admin.role == 'student':
        return False
    if user_role() == 'hod':
        return admin.role == 'teacher' and admin.department_id == current_department_id()
    return is_super_admin()


def can_reset_staff_account(admin):
    if user_role() == 'hod':
        return admin.role == 'teacher' and admin.department_id == current_department_id()
    if not is_super_admin():
        return False
    return admin.role in STAFF_ROLES and admin.id != current_user.id


# ── Display helpers ──────────────────────────────────────────

def department_scope_label(department):
    if not department:
        return 'Not assigned'
    branch = department.branch_name or infer_department_branch(department.course_name)
    course_short = infer_department_branch(department.course_name)
    if branch and course_short:
        return f"{branch} - {course_short}"
    return branch or department.course_name


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


def session_display_name(class_session):
    if getattr(class_session, 'special_title', None):
        return class_session.special_title
    return subject_display_name(class_session.subject) if class_session.subject else 'Special Class'


def build_account_profile(admin):
    role = admin.role
    profile = {
        'display_name': admin.username,
        'username': admin.username,
        'role_label': role_label(role),
        'email': admin.username if '@' in (admin.username or '') else None,
        'scope_label': 'System Access',
        'scope_value': 'All departments' if role == 'super_admin' else 'Not assigned',
        'details': [],
    }

    if role == 'student' and admin.student:
        profile.update({
            'display_name': admin.student.name,
            'email': admin.student.email,
            'scope_label': 'Department',
            'scope_value': admin.student.department.display_name,
        })
        profile['details'] = [
            ('Roll Number', admin.student.roll_no),
            ('Semester', admin.student.semester.name),
        ]
    elif role == 'teacher' and admin.teacher:
        profile.update({
            'display_name': admin.teacher.name,
            'email': admin.teacher.email,
            'scope_label': 'Department',
            'scope_value': admin.teacher.department.display_name,
        })
        profile['details'] = [('Teacher Profile', admin.teacher.name)]
    elif role == 'hod':
        profile.update({
            'scope_label': 'Department',
            'scope_value': admin.department.display_name if admin.department else 'Not assigned',
        })
    return profile


def log_activity(action, description, target_type=None, target_id=None, department_id=None):
    authenticated = current_user.is_authenticated
    db.session.add(ActivityLog(
        actor_admin_id=current_user.id if authenticated else None,
        actor_username=current_user.username if authenticated else 'System',
        department_id=department_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        description=description[:255],
        created_at=ist_now(),
    ))


def describe_delete_block(entity_name, counts):
    used_counts = [f"{label}: {value}" for label, value in counts.items() if value]
    if not used_counts:
        return f"{entity_name} still has linked records."
    return f"Cannot delete {entity_name} because it still has linked records ({', '.join(used_counts)})."
