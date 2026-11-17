"""Role-based access control, scoped queries, and display helpers."""
import re
from functools import wraps

from flask import abort, current_app, g, has_app_context
from flask_login import current_user
from sqlalchemy import and_, false, or_, select

from .extensions import db, login_manager
from .models import ActivityLog, ClassSession, Department, Semester, Student, Subject, SubjectTeacher, Teacher, User
from .utils import infer_department_branch, normalize_email, normalize_paper_code, utc_now

ROLE_LABELS = {
    'principal': 'Principal',
    'hod': 'HOD',
    'teacher': 'Teacher',
    'student': 'Student',
}
STAFF_ROLES = ('principal', 'hod', 'teacher')
SPECIAL_CLASS_SUBJECT_NAME = '__SPECIAL_CLASS__'


def role_label(role):
    """What a role is called here: the Settings page's word ("Director"), else the usual one."""
    if role in ROLE_LABELS and has_app_context():
        from .services.settings import get_setting  # here: services.settings imports this module
        return get_setting(f'role_{role}')
    return ROLE_LABELS.get(role, role.replace('_', ' ').title() if role else 'User')


def user_role():
    """The current user's role, or None. Unknown or empty roles never gain access."""
    if not current_user.is_authenticated:
        return None
    role = getattr(current_user, 'role', None)
    return role if role in ROLE_LABELS else None


def is_principal():
    return user_role() == 'principal'


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
    if not allowed_staff_domains():
        return (
            "Staff accounts cannot be created yet: the server administrator must set ALLOWED_STAFF_DOMAINS "
            "(for example @your-college.edu)."
        )
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
    if role == 'teacher':
        # A teacher account without a teacher profile has no scope at all.
        return current_user.teacher.department_id if current_user.teacher else None
    if role == 'student' and current_user.student:
        return current_user.student.department_id
    return current_user.department_id


def current_teacher_id():
    if current_user.is_authenticated and user_role() == 'teacher':
        return current_user.teacher_id
    return None


def own_teacher_profile():
    """The current user's teacher profile when they teach: a teacher, or an HOD who also teaches.

    Only for their own classes (Today's classes, My routine). Scoping stays by role:
    current_teacher_id() is for the teacher role alone, and an HOD keeps department-wide rights.
    """
    if current_user.is_authenticated and user_role() in ('teacher', 'hod'):
        return current_user.teacher
    return None


def own_teacher_filter(column):
    """SQL condition: `column` is the current teacher. Never true without a teacher profile,
    because `column == None` would become `IS NULL` and match every unassigned row."""
    teacher_id = current_teacher_id()
    return column == teacher_id if teacher_id else false()


def is_own_teacher(teacher_id):
    """Whether `teacher_id` is the current teacher's profile (never true without one)."""
    own = current_teacher_id()
    return own is not None and teacher_id == own


def can_access_department(department_id):
    if department_id is None:
        return False
    if is_principal():
        return True
    dept_id = current_department_id()
    return dept_id is not None and int(department_id) == int(dept_id)


def can_access_student(student):
    if is_principal():
        return True
    if user_role() == 'student':
        return current_user.student_id == student.id
    return can_access_department(student.department_id)


def taught_subject_clause(subject_id_column):
    """SQL condition: `subject_id_column` is a subject the current teacher teaches, as its main
    teacher or a co-teacher. Never true without a teacher profile."""
    teacher_id = current_teacher_id()
    if not teacher_id:
        return false()
    return or_(
        subject_id_column.in_(select(Subject.id).where(Subject.teacher_id == teacher_id)),
        subject_id_column.in_(select(SubjectTeacher.subject_id).where(SubjectTeacher.teacher_id == teacher_id)),
    )


def taught_subject_ids():
    """For teachers: the ids of the subjects they teach (main or co-teacher), once per request."""
    if 'taught_subject_ids' not in g:
        g.taught_subject_ids = {
            subject_id for (subject_id,) in db.session.query(Subject.id).filter(taught_subject_clause(Subject.id))
        } if current_teacher_id() else set()
    return g.taught_subject_ids


def teacher_session_clause():
    """SQL condition for a teacher's sessions: ones they ran, and every session of a subject they teach
    (so co-teachers can run and correct their shared classes)."""
    return or_(own_teacher_filter(ClassSession.teacher_id), taught_subject_clause(ClassSession.subject_id))


def taught_classes():
    """For teachers: the classes, as (department_id, semester_id), they teach a subject in (main or co-teacher).

    Worked out once per request, since a student list asks for every row.
    """
    if 'taught_classes' not in g:
        ids = taught_subject_ids()
        g.taught_classes = {
            (department_id, semester_id) for department_id, semester_id in
            db.session.query(Subject.department_id, Subject.semester_id).filter(Subject.id.in_(ids)).distinct()
        } if ids else set()
    return g.taught_classes


def can_reset_student_access(student):
    """Whether the current user may reset a student's password or registered phone.

    Either reset hands over the account: a password reset shows the staff member a
    working one-time password, and a device reset lets the next phone the student scans
    from become theirs. So a teacher may do them only for classes they teach; the HOD
    and the Principal for every student they can see.
    """
    role = user_role()
    if role == 'teacher':
        return can_access_student(student) and (student.department_id, student.semester_id) in taught_classes()
    return role in ('principal', 'hod') and can_access_student(student)


def can_access_teacher(teacher):
    if is_principal():
        return True
    if user_role() == 'teacher':
        return is_own_teacher(teacher.id)
    return can_access_department(teacher.department_id)


def can_access_subject(subject):
    if not subject:
        return False
    if user_role() == 'teacher':
        return subject.id in taught_subject_ids()
    return can_access_department(subject.department_id)


def can_access_session(class_session):
    if not class_session or not class_session.subject:
        return False
    if user_role() == 'teacher':
        return (
            can_access_department(class_session.subject.department_id)
            and (is_own_teacher(class_session.teacher_id) or class_session.subject_id in taught_subject_ids())
        )
    return user_role() in ('principal', 'hod') and can_access_subject(class_session.subject)


def department_filter(column):
    """SQL condition: `column` is the current user's department. Never true without one
    (`column == None` would become `IS NULL`)."""
    dept_id = current_department_id()
    return column == dept_id if dept_id else false()


def is_teacher_in_own_department(account):
    """For HODs: whether `account` is a teacher account in the HOD's department."""
    dept_id = current_department_id()
    return account.role == 'teacher' and dept_id is not None and account.department_id == dept_id


def visible_departments_query():
    query = Department.query
    if not is_principal():
        query = query.filter(department_filter(Department.id))
    return query


def ordered_departments_query():
    return visible_departments_query().order_by(Department.branch, Department.name)


def visible_semesters_query():
    query = Semester.query
    if not is_principal():
        query = query.filter(department_filter(Semester.department_id))
    return query


def visible_subjects_query():
    query = Subject.query.filter(Subject.name != SPECIAL_CLASS_SUBJECT_NAME)
    if user_role() == 'teacher':
        query = query.filter(taught_subject_clause(Subject.id))
    elif not is_principal():
        query = query.filter(department_filter(Subject.department_id))
    return query


def visible_teachers_query():
    query = Teacher.query
    if user_role() == 'teacher':
        query = query.filter(own_teacher_filter(Teacher.id))
    elif not is_principal():
        query = query.filter(department_filter(Teacher.department_id))
    return query


def visible_students_query():
    query = Student.query
    if user_role() == 'student':
        query = query.filter(Student.id == current_user.student_id)
    elif not is_principal():
        query = query.filter(department_filter(Student.department_id))
    return query


def visible_sessions_query():
    query = ClassSession.query.join(Subject)
    if user_role() == 'teacher':
        query = query.filter(teacher_session_clause())
    elif not is_principal():
        query = query.filter(department_filter(Subject.department_id))
    return query


def visible_activity_logs_query():
    query = ActivityLog.query.outerjoin(User, ActivityLog.actor_user_id == User.id)
    if is_principal():
        return query
    if user_role() == 'hod':
        return query.filter(
            or_(
                ActivityLog.actor_user_id == current_user.id,
                and_(department_filter(ActivityLog.department_id), User.role == 'teacher'),
            )
        )
    return query.filter(ActivityLog.actor_user_id == current_user.id)


def own_security_activity_logs_query():
    return ActivityLog.query.filter(
        ActivityLog.action.in_(OWN_SECURITY_ACTIONS),
        or_(
            ActivityLog.actor_user_id == current_user.id,
            and_(ActivityLog.target_type == 'user', ActivityLog.target_id == current_user.id),
        ),
    )


# ── Account management rules ─────────────────────────────────

def has_active_hod(department_id, exclude_user_id=None):
    query = User.query.filter_by(role='hod', department_id=department_id, is_active=True)
    if exclude_user_id:
        query = query.filter(User.id != exclude_user_id)
    return db.session.query(query.exists()).scalar()


def can_manage_account(account):
    if user_role() == 'hod':
        return is_teacher_in_own_department(account)
    if not is_principal():
        return False
    if account.role == 'student' or account.id == current_user.id:
        return False
    if (
        account.role == 'principal'
        and account.is_active
        and User.query.filter_by(role='principal', is_active=True).count() <= 1
    ):
        return False
    return True


def can_edit_account(account):
    if account.role == 'student':
        return False
    if user_role() == 'hod':
        return is_teacher_in_own_department(account)
    return is_principal()


def can_reset_staff_account(account):
    if user_role() == 'hod':
        return is_teacher_in_own_department(account)
    if not is_principal():
        return False
    return account.role in STAFF_ROLES and account.id != current_user.id


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


# Titles left out of automatic initials: "Dr. Sutapa Chatterjee" is SC.
_NAME_TITLES = frozenset({'dr', 'prof', 'mr', 'mrs', 'ms', 'smt', 'sri', 'shri', 'miss'})


# Role words that start some usernames ("hod.partha.sarkar"): not part of the person's name.
_ROLE_WORDS = frozenset({'principal', 'hod', 'teacher', 'student'})


def avatar_initials(name, from_email=False):
    """Up to two letters for an avatar: "Dr. Manas Ghosh" is "MG", "Chocolate" is "C".

    Titles (Dr, Prof, ...) are skipped. from_email: the name is the part of an email address
    before the @, where a leading role word is skipped too ("hod.partha.sarkar" is "PS").
    """
    words = re.findall(r"[^\W\d_]+", name or '')
    named = [word for word in words if word.lower() not in _NAME_TITLES]
    if from_email and len(named) > 1 and named[0].lower() in _ROLE_WORDS:
        named = named[1:]
    letters = ''.join(word[0] for word in named[:2]).upper()
    return letters or (words[0][0].upper() if words else '?')


def teacher_short_name(teacher):
    """The teacher's short name for the routine grid: the one saved, else their initials."""
    if teacher is None:
        return ''
    if teacher.short_name:
        return teacher.short_name
    words = [word for word in re.findall(r"[A-Za-z]+", teacher.name or '') if word.lower() not in _NAME_TITLES]
    return ''.join(word[0] for word in words).upper()[:4] or (teacher.name or '')[:4]


def session_display_name(class_session):
    """The session's title, with its group for a group's session: "C291 - Networks Lab · Gr-1"."""
    if getattr(class_session, 'special_title', None):
        name = class_session.special_title
    else:
        name = subject_display_name(class_session.subject) if class_session.subject else 'Special Class'
    group = getattr(class_session, 'group', None)
    return f"{name} · {group.name}" if group else name


def build_account_profile(account):
    role = account.role
    profile = {
        'display_name': account.username,
        'username': account.username,
        'role_label': role_label(role),
        'email': account.username if '@' in (account.username or '') else None,
        'scope_label': 'System Access',
        'scope_value': 'All departments' if role == 'principal' else 'Not assigned',
        'details': [],
    }

    if role == 'student' and account.student:
        profile.update({
            'display_name': account.student.name,
            'email': account.student.email,
            'scope_label': 'Department',
            'scope_value': account.student.department.display_name,
        })
        profile['details'] = [
            ('Roll Number', account.student.roll_no),
            ('Semester', account.student.semester.name),
        ]
    elif role == 'teacher' and account.teacher:
        profile.update({
            'display_name': account.teacher.name,
            'email': account.teacher.email,
            'scope_label': 'Department',
            'scope_value': account.teacher.department.display_name,
        })
        profile['details'] = [('Teacher Profile', account.teacher.name)]
    elif role == 'hod':
        profile.update({
            'scope_label': 'Department',
            'scope_value': account.department.display_name if account.department else 'Not assigned',
        })
    return profile


# Actions shown to users in their own "Recent Security Activity".
OWN_SECURITY_ACTIONS = (
    'password_changed', 'account_password_reset', 'account_status_changed', 'login_failed', 'login_locked',
)
SIGN_IN_ATTEMPT_ACTOR = 'Sign-in attempt'


def log_activity(action, description, target_type=None, target_id=None, department_id=None, anonymous_actor='System'):
    authenticated = current_user.is_authenticated
    db.session.add(ActivityLog(
        actor_user_id=current_user.id if authenticated else None,
        actor_username=current_user.username if authenticated else anonymous_actor,
        department_id=department_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        description=description[:255],
        created_at=utc_now(),
    ))


def describe_delete_block(entity_name, counts):
    used_counts = [f"{label}: {value}" for label, value in counts.items() if value]
    if not used_counts:
        return f"{entity_name} still has linked records."
    return f"Cannot delete {entity_name} because it still has linked records ({', '.join(used_counts)})."
