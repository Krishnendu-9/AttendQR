from flask import Flask, render_template, redirect, url_for, request, jsonify, send_file, abort
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from models import db, Admin, Student, ClassSession, Attendance, Department, Subject, Teacher, Semester
from config import Config
from datetime import datetime, timedelta
from sqlalchemy import event, text
from sqlalchemy.engine import Engine
from functools import wraps
from math import ceil
import sqlite3
import qrcode, uuid, os, io, socket
import openpyxl
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Table
import pytz

# Define IST timezone
IST = pytz.timezone('Asia/Kolkata')

def get_ist_now():
    """Get current time in IST, return as naive datetime (no timezone info)"""
    return datetime.now(IST).replace(tzinfo=None)

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

@login_manager.user_loader
def load_user(user_id):
    return Admin.query.get(int(user_id))

@app.context_processor
def inject_now():
    return {
        'now': lambda: get_ist_now(),
        'role_label': role_label
    }


ROLE_LABELS = {
    'super_admin': 'Principal',
    'hod': 'HOD',
    'teacher': 'Teacher',
    'student': 'Student'
}

ATTENDANCE_TARGET = 80
ALLOWED_STAFF_EMAIL_DOMAINS = ('@rcciit.org.in', '@gmail.com')


def normalize_email(value):
    return (value or '').strip().lower()


def is_allowed_staff_email(value):
    email = normalize_email(value)
    return any(email.endswith(domain) for domain in ALLOWED_STAFF_EMAIL_DOMAINS)


def staff_email_rule_message():
    return "Use an email ending with @rcciit.org.in or @gmail.com"


def role_label(role):
    return ROLE_LABELS.get(role, role.replace('_', ' ').title() if role else 'User')


def user_role():
    if not current_user.is_authenticated:
        return None
    return getattr(current_user, 'role', None) or 'super_admin'


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
        return can_access_department(subject.department_id)
    return can_access_department(subject.department_id)


def can_access_session(session):
    if not session:
        return False
    if not can_access_subject(session.subject):
        return False
    if user_role() == 'teacher':
        return session.teacher_id == current_teacher_id()
    return user_role() in ('super_admin', 'hod')


def visible_departments_query():
    query = Department.query
    if not is_super_admin():
        dept_id = current_department_id()
        query = query.filter(Department.id == dept_id if dept_id else False)
    return query


def visible_semesters_query():
    query = Semester.query
    if not is_super_admin():
        dept_id = current_department_id()
        query = query.filter(Semester.department_id == dept_id if dept_id else False)
    return query


def visible_subjects_query():
    query = Subject.query
    if not is_super_admin():
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
    query = db.session.query(ClassSession).join(Subject)
    if user_role() == 'teacher':
        query = query.filter(ClassSession.teacher_id == current_teacher_id())
    elif not is_super_admin():
        dept_id = current_department_id()
        query = query.filter(Subject.department_id == dept_id if dept_id else False)
    return query


def classes_needed_for_target(attended, total, target=ATTENDANCE_TARGET):
    if total == 0 or attended / total * 100 >= target:
        return 0
    target_ratio = target / 100
    return max(0, ceil((target_ratio * total - attended) / (1 - target_ratio)))


def build_student_attendance_summary(student):
    subjects = Subject.query.filter_by(
        department_id=student.department_id,
        semester_id=student.semester_id
    ).order_by(Subject.name).all()
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


def ensure_rbac_columns():
    columns = {row[1] for row in db.session.execute(text("PRAGMA table_info(admin)")).fetchall()}
    migrations = {
        'role': "ALTER TABLE admin ADD COLUMN role VARCHAR(20) DEFAULT 'super_admin' NOT NULL",
        'department_id': "ALTER TABLE admin ADD COLUMN department_id INTEGER",
        'teacher_id': "ALTER TABLE admin ADD COLUMN teacher_id INTEGER",
        'student_id': "ALTER TABLE admin ADD COLUMN student_id INTEGER"
    }
    for column, statement in migrations.items():
        if column not in columns:
            db.session.execute(text(statement))
    db.session.execute(text("UPDATE admin SET role = 'super_admin' WHERE role IS NULL OR role = ''"))
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
        return
    username_match = Admin.query.filter_by(username=student.email).first()
    if username_match:
        return
    db.session.add(Admin(
        username=student.email,
        password=generate_password_hash(student.roll_no),
        role='student',
        department_id=student.department_id,
        student_id=student.id
    ))


def ensure_existing_student_accounts():
    for student in Student.query.all():
        ensure_student_account(student)
    db.session.commit()


@app.route('/')
@login_required
def dashboard():
    if user_role() == 'student':
        if not current_user.student:
            abort(403)
        summary = build_student_attendance_summary(current_user.student)
        return render_template('student_dashboard.html', student=current_user.student, summary=summary)

    return render_template('dashboard.html',
        departments=visible_departments_query().count(),
        semesters=visible_semesters_query().count(),
        subjects=visible_subjects_query().count(),
        teachers=visible_teachers_query().count(),
        students=visible_students_query().count(),
        sessions=visible_sessions_query().count(),
        attendance=sum(Attendance.query.filter_by(session_id=s.id).count() for s in visible_sessions_query().all()))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = normalize_email(request.form['username'])
        admin = Admin.query.filter(db.func.lower(Admin.username) == username).first()
        if admin and check_password_hash(admin.password, request.form['password']):
            login_user(admin)
            return redirect(url_for('dashboard'))
    return render_template('login.html')

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
@app.route('/departments', methods=['GET', 'POST'])
@login_required
@role_required('super_admin', 'hod')
def departments():
    if request.method == 'POST':
        if not is_super_admin():
            abort(403)
        db.session.add(Department(name=request.form['name']))
        db.session.commit()
    return render_template('departments.html', departments=visible_departments_query().order_by(Department.name).all())

@app.route('/departments/edit/<int:id>', methods=['POST'])
@login_required
@role_required('super_admin')
def edit_department(id):
    d = Department.query.get_or_404(id)
    d.name = request.form['name']
    db.session.commit()
    return redirect(url_for('departments'))

@app.route('/departments/delete/<int:id>', methods=['POST'])
@login_required
@role_required('super_admin')
def delete_department(id):
    d = Department.query.get_or_404(id)
    Admin.query.filter(Admin.department_id == id, Admin.role != 'super_admin').delete(synchronize_session=False)
    Student.query.filter_by(department_id=id).delete()
    Subject.query.filter_by(department_id=id).delete()
    Teacher.query.filter_by(department_id=id).delete()
    Semester.query.filter_by(department_id=id).delete()
    db.session.delete(d)
    db.session.commit()
    return redirect(url_for('departments'))

# ── Semesters ────────────────────────────────────────────────
@app.route('/semesters', methods=['GET', 'POST'])
@login_required
@role_required('super_admin', 'hod')
def semesters():
    if request.method == 'POST':
        department_id = int(request.form['department_id']) if is_super_admin() else current_department_id()
        if not can_access_department(department_id):
            abort(403)
        db.session.add(Semester(name=request.form['name'], department_id=department_id))
        db.session.commit()
    return render_template('semesters.html',
        semesters=visible_semesters_query().order_by(Semester.department_id, Semester.name).all(),
        departments=visible_departments_query().order_by(Department.name).all())

@app.route('/semesters/edit/<int:id>', methods=['POST'])
@login_required
@role_required('super_admin', 'hod')
def edit_semester(id):
    s = Semester.query.get_or_404(id)
    if not can_access_department(s.department_id):
        abort(403)
    s.name = request.form['name']
    department_id = int(request.form['department_id']) if is_super_admin() else current_department_id()
    if not can_access_department(department_id):
        abort(403)
    s.department_id = department_id
    db.session.commit()
    return redirect(url_for('semesters'))

@app.route('/semesters/delete/<int:id>', methods=['POST'])
@login_required
@role_required('super_admin', 'hod')
def delete_semester(id):
    semester = Semester.query.get_or_404(id)
    if not can_access_department(semester.department_id):
        abort(403)
    student_ids = [s.id for s in Student.query.filter_by(semester_id=id).all()]
    if student_ids:
        Admin.query.filter(Admin.student_id.in_(student_ids)).delete(synchronize_session=False)
    Student.query.filter_by(semester_id=id).delete()
    Subject.query.filter_by(semester_id=id).delete()
    db.session.delete(semester)
    db.session.commit()
    return redirect(url_for('semesters'))

# ── Subjects ─────────────────────────────────────────────────
@app.route('/subjects', methods=['GET', 'POST'])
@login_required
@role_required('super_admin', 'hod')
def subjects():
    if request.method == 'POST':
        department_id = int(request.form['department_id']) if is_super_admin() else current_department_id()
        semester_id = int(request.form['semester_id'])
        semester = Semester.query.get_or_404(semester_id)
        if not can_access_department(department_id) or semester.department_id != department_id:
            abort(403)
        db.session.add(Subject(
            name=request.form['name'],
            department_id=department_id,
            semester_id=semester_id))
        db.session.commit()
    return render_template('subjects.html',
        subjects=visible_subjects_query().order_by(Subject.name).all(),
        departments=visible_departments_query().order_by(Department.name).all(),
        semesters=visible_semesters_query().order_by(Semester.name).all())

@app.route('/subjects/edit/<int:id>', methods=['POST'])
@login_required
@role_required('super_admin', 'hod')
def edit_subject(id):
    s = Subject.query.get_or_404(id)
    if not can_access_department(s.department_id):
        abort(403)
    department_id = int(request.form['department_id']) if is_super_admin() else current_department_id()
    semester_id = int(request.form['semester_id'])
    semester = Semester.query.get_or_404(semester_id)
    if not can_access_department(department_id) or semester.department_id != department_id:
        abort(403)
    s.name = request.form['name']
    s.department_id = department_id
    s.semester_id = semester_id
    db.session.commit()
    return redirect(url_for('subjects'))

@app.route('/subjects/delete/<int:id>', methods=['POST'])
@login_required
@role_required('super_admin', 'hod')
def delete_subject(id):
    subject = Subject.query.get_or_404(id)
    if not can_access_department(subject.department_id):
        abort(403)
    db.session.delete(subject)
    db.session.commit()
    return redirect(url_for('subjects'))

# ── Teachers ─────────────────────────────────────────────────
@app.route('/teachers', methods=['GET', 'POST'])
@login_required
@role_required('super_admin', 'hod')
def teachers():
    error = None
    if request.method == 'POST':
        department_id = int(request.form['department_id']) if is_super_admin() else current_department_id()
        email = normalize_email(request.form['email'])
        if not can_access_department(department_id):
            abort(403)
        elif not is_allowed_staff_email(email):
            error = staff_email_rule_message()
        else:
            db.session.add(Teacher(name=request.form['name'], email=email, department_id=department_id))
            db.session.commit()
    teacher_accounts = {}
    for account in Admin.query.filter(Admin.role.in_(['teacher', 'hod'])).all():
        if account.role == 'teacher' and account.teacher_id:
            teacher_accounts.setdefault(account.teacher_id, []).append(role_label(account.role))
        elif account.role == 'hod':
            matched_teacher = Teacher.query.filter(
                db.func.lower(Teacher.email) == normalize_email(account.username),
                Teacher.department_id == account.department_id
            ).first()
            if matched_teacher:
                teacher_accounts.setdefault(matched_teacher.id, []).append(role_label(account.role))

    return render_template('teachers.html',
        teachers=visible_teachers_query().order_by(Teacher.name).all(),
        departments=visible_departments_query().order_by(Department.name).all(),
        teacher_accounts=teacher_accounts,
        error=error)

@app.route('/teachers/edit/<int:id>', methods=['POST'])
@login_required
@role_required('super_admin', 'hod')
def edit_teacher(id):
    t = Teacher.query.get_or_404(id)
    if not can_access_teacher(t):
        abort(403)
    department_id = int(request.form['department_id']) if is_super_admin() else current_department_id()
    if not can_access_department(department_id):
        abort(403)
    email = normalize_email(request.form['email'])
    if not is_allowed_staff_email(email):
        abort(400, description=staff_email_rule_message())
    t.name = request.form['name']
    t.email = email
    t.department_id = department_id
    db.session.commit()
    return redirect(url_for('teachers'))

@app.route('/teachers/delete/<int:id>', methods=['POST'])
@login_required
@role_required('super_admin', 'hod')
def delete_teacher(id):
    teacher = Teacher.query.get_or_404(id)
    if not can_access_teacher(teacher):
        abort(403)
    Admin.query.filter_by(teacher_id=teacher.id).delete()
    db.session.delete(teacher)
    db.session.commit()
    return redirect(url_for('teachers'))


# ── Students ─────────────────────────────────────────────────
@app.route('/students', methods=['GET', 'POST'])
@login_required
@role_required('super_admin', 'hod')
def students():
    error = None
    if request.method == 'POST':
        try:
            dept_id = int(request.form['department_id']) if is_super_admin() else current_department_id()
            sem_id = int(request.form.get('semester_id', 0))
            roll_no = request.form['roll_no'].strip().upper()  # Convert to uppercase
            
            # Debug: Check what department and semester are selected
            department = Department.query.get(dept_id)
            semester = Semester.query.get(sem_id)
            
            if not department:
                error = f"Department with ID {dept_id} not found"
            elif not semester:
                error = f"Semester with ID {sem_id} not found"
            elif semester.department_id != dept_id:
                error = f"Semester '{semester.name}' belongs to '{Semester.query.get(sem_id).department.name}', not '{department.name}'"
            else:
                # Check if roll_no exists in same department and semester (case-insensitive)
                existing = Student.query.filter(
                    db.func.upper(Student.roll_no) == roll_no,
                    Student.department_id == dept_id,
                    Student.semester_id == sem_id
                ).first()
                if existing:
                    error = f"Roll number {roll_no} already exists in {department.name} - {semester.name}"
                else:
                    # Check if email already exists
                    existing_email = Student.query.filter_by(email=request.form['email']).first()
                    if existing_email:
                        error = f"Email {request.form['email']} already exists"
                    else:
                        student = Student(
                            name=request.form['name'],
                            roll_no=roll_no,
                            email=request.form['email'],
                            department_id=dept_id,
                            semester_id=sem_id)
                        db.session.add(student)
                        db.session.flush()
                        ensure_student_account(student)
                        db.session.commit()
        except ValueError as e:
            db.session.rollback()
            error = f"Invalid input: {str(e)}"
        except Exception as e:
            db.session.rollback()
            error = f"Error: {str(e)}"
    
    return render_template('students.html',
        students=visible_students_query().order_by(Student.roll_no).all(),
        departments=visible_departments_query().order_by(Department.name).all(),
        semesters=visible_semesters_query().order_by(Semester.name).all(),
        error=error)

@app.route('/students/edit/<int:id>', methods=['POST'])
@login_required
@role_required('super_admin', 'hod')
def edit_student(id):
    s = Student.query.get_or_404(id)
    if not can_access_student(s):
        abort(403)
    roll_no = request.form['roll_no'].strip().upper()  # Convert to uppercase
    dept_id = int(request.form['department_id']) if is_super_admin() else current_department_id()
    sem_id = int(request.form['semester_id'])
    if not can_access_department(dept_id):
        abort(403)
    
    # Validate semester belongs to department
    semester = Semester.query.get(sem_id)
    if not semester or semester.department_id != dept_id:
        return redirect(url_for('students'))
    
    # Check if roll_no changed and exists in same dept/sem (case-insensitive)
    if (s.roll_no.upper() != roll_no or s.department_id != dept_id or s.semester_id != sem_id):
        existing = Student.query.filter(
            db.func.upper(Student.roll_no) == roll_no,
            Student.department_id == dept_id,
            Student.semester_id == sem_id
        ).first()
        if existing:
            return redirect(url_for('students'))
    
    # Check if email changed and already exists
    if s.email != request.form['email']:
        existing_email = Student.query.filter_by(email=request.form['email']).first()
        if existing_email:
            return redirect(url_for('students'))
    
    s.name = request.form['name']
    s.roll_no = roll_no
    s.email = request.form['email']
    s.department_id = dept_id
    s.semester_id = sem_id
    ensure_student_account(s)
    db.session.commit()
    return redirect(url_for('students'))


@app.route('/students/delete/<int:id>', methods=['POST'])
@login_required
@role_required('super_admin', 'hod')
def delete_student(id):
    student = Student.query.get_or_404(id)
    if not can_access_student(student):
        abort(403)
    Admin.query.filter_by(student_id=student.id).delete()
    db.session.delete(student)
    db.session.commit()
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
    rows = Subject.query.filter_by(semester_id=semester_id).order_by(Subject.name).all()
    return jsonify([{'id': r.id, 'name': r.name} for r in rows])

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
    
    # Check if viewing existing session
    view_session_id = request.args.get('session_id', type=int)
    if view_session_id:
        cs = ClassSession.query.get(view_session_id)
        if cs:
            if not can_access_session(cs):
                abort(403)
            qr_image = f"static/qrcodes/{cs.token}.png"
            session_id = cs.id
            subject_name = cs.subject.name
            scan_url = build_scan_url(cs.token)
            duration = int((cs.expires_at - cs.created_at).total_seconds() / 60)
    
    if request.method == 'POST':
        token = str(uuid.uuid4())
        subject_id = int(request.form['subject_id'])
        duration = int(request.form.get('duration', 30))
        teacher_id = request.form.get('teacher_id') or None
        
        subject = Subject.query.get_or_404(subject_id)
        if not can_access_subject(subject):
            abort(403)
        if user_role() == 'teacher':
            teacher_id = current_teacher_id()
        elif teacher_id:
            teacher = Teacher.query.get_or_404(int(teacher_id))
            if not can_access_teacher(teacher) or teacher.department_id != subject.department_id:
                abort(403)
        
        # Use IST timezone (stored as naive datetime)
        now_ist = get_ist_now()
        expires_ist = now_ist + timedelta(minutes=duration)
        
        cs = ClassSession(
            subject_id=subject_id, 
            token=token, 
            teacher_id=teacher_id,
            created_at=now_ist,
            expires_at=expires_ist
        )
        db.session.add(cs)
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
        departments=visible_departments_query().order_by(Department.name).all(),
        recent_sessions=recent_sessions,
        current_teacher=current_user.teacher if user_role() == 'teacher' else None)



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
    db.session.delete(cs)
    db.session.commit()
    
    return redirect(url_for('generate_qr'))



@app.route('/scan/<token>')
def scan_qr(token):
    return render_template('scan_qr.html', token=token)

@app.route('/mark_attendance', methods=['POST'])
def mark_attendance():
    data = request.json
    cs = ClassSession.query.filter_by(token=data['token']).first()
    
    # Use IST timezone for comparison (naive datetime)
    now_ist = get_ist_now()
    
    if not cs or now_ist > cs.expires_at:
        return jsonify({'success': False, 'message': 'QR code expired or invalid'})
    
    # Case-insensitive roll number search
    roll_no = data['roll_no'].strip().upper()
    student = Student.query.filter(db.func.upper(Student.roll_no) == roll_no).first()
    
    if not student:
        return jsonify({'success': False, 'message': 'Student not found'})
    
    # Validate department and semester match
    subject = cs.subject
    if student.department_id != subject.department_id:
        return jsonify({'success': False, 'message': 'This QR code is not for your department'})
    
    if student.semester_id != subject.semester_id:
        return jsonify({'success': False, 'message': 'This QR code is not for your semester'})
    
    if Attendance.query.filter_by(student_id=student.id, session_id=cs.id).first():
        return jsonify({'success': False, 'message': 'Attendance already marked'})
    
    db.session.add(Attendance(student_id=student.id, session_id=cs.id, timestamp=now_ist))
    db.session.commit()
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
        if date_from:
            try:
                query = query.filter(ClassSession.created_at >= datetime.strptime(date_from, '%Y-%m-%d'))
            except:
                pass
        if date_to:
            try:
                query = query.filter(ClassSession.created_at <= datetime.strptime(date_to, '%Y-%m-%d'))
            except:
                pass
        
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
        departments=visible_departments_query().order_by(Department.name).all(),
        semesters=visible_semesters_query().order_by(Semester.name).all(),
        subjects=visible_subjects_query().order_by(Subject.name).all(),
        sessions=sessions,
        stats=stats,
        selected_dept=dept_id,
        selected_sem=sem_id,
        selected_subject=subject_id,
        date_from=date_from or '',
        date_to=date_to or '')

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
            'subject': cs.subject.name,
            'department': cs.subject.department.name,
            'semester': cs.subject.semester.name,
            'teacher': cs.teacher.name if cs.teacher else 'N/A',
            'created_at': cs.created_at.strftime('%Y-%m-%d %H:%M'),
            'expires_at': cs.expires_at.strftime('%Y-%m-%d %H:%M')
        },
        'attendances': [{
            'id': a.id,
            'student_name': a.student.name,
            'student_roll': a.student.roll_no,
            'student_email': a.student.email,
            'timestamp': a.timestamp.strftime('%Y-%m-%d %H:%M:%S')
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
        sem=Semester.query.get(sem_id),
        sessions=sessions,
        attendance_data=attendance_data)

@app.route('/export/<int:department_id>/<int:semester_id>/<fmt>')
@login_required
@role_required('super_admin', 'hod', 'teacher')
def export(department_id, semester_id, fmt):
    if not can_access_department(department_id):
        abort(403)
    students = Student.query.filter_by(department_id=department_id, semester_id=semester_id).all()
    sessions = db.session.query(ClassSession).join(Subject).filter(
        Subject.department_id == department_id,
        Subject.semester_id == semester_id
    )
    if user_role() == 'teacher':
        sessions = sessions.filter(ClassSession.teacher_id == current_teacher_id())
    sessions = sessions.order_by(ClassSession.created_at.desc()).all()
    
    dept = Department.query.get(department_id)
    sem = Semester.query.get(semester_id)
    
    # Build data
    headers = ['Roll No', 'Name', 'Email'] + [f"{s.subject.name}\n{s.created_at.strftime('%d/%m')}" for s in sessions] + ['Total', '%']
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
    
    filename = f'{dept.name}_{sem.name}_attendance'
    
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
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import Paragraph
        
        buf = io.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=letter)
        
        # Style table
        table = Table(data)
        table.setStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#366092')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('GRID', (0, 0), (-1, -1), 1, colors.black)
        ])
        
        doc.build([table])
        buf.seek(0)
        return send_file(buf, download_name=f'{filename}.pdf', as_attachment=True)

@app.route('/add_admin', methods=['GET', 'POST'])
@login_required
@role_required('super_admin', 'hod')
def add_admin():
    message = None
    error = None
    allowed_roles = ROLE_LABELS if is_super_admin() else {
        'teacher': ROLE_LABELS['teacher'],
        'student': ROLE_LABELS['student']
    }
    
    if request.method == 'POST':
        username = normalize_email(request.form.get('username', ''))
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')
        role = request.form.get('role', 'teacher' if user_role() == 'hod' else 'super_admin')
        department_id = request.form.get('department_id') or None
        teacher_id = request.form.get('teacher_id') or None
        student_id = request.form.get('student_id') or None
        
        # Validation
        if not username or not password:
            error = "Username and password are required"
        elif role not in allowed_roles:
            error = "Invalid role selected"
        elif role in ('hod', 'teacher') and not is_allowed_staff_email(username):
            error = staff_email_rule_message()
        elif len(password) < 6:
            error = "Password must be at least 6 characters"
        elif password != confirm_password:
            error = "Passwords do not match"
        elif Admin.query.filter_by(username=username).first():
            error = f"Username '{username}' already exists"
        else:
            try:
                if role == 'hod':
                    if not is_super_admin():
                        raise ValueError("Only Principal can create HOD accounts")
                    if not department_id:
                        raise ValueError("Department is required for HOD accounts")
                    teacher_id = None
                    student_id = None
                elif role == 'teacher':
                    teacher = Teacher.query.get(int(teacher_id)) if teacher_id else None
                    if not teacher:
                        raise ValueError("Teacher profile is required for teacher accounts")
                    if not can_access_teacher(teacher):
                        raise ValueError("You can create teacher accounts only for your department")
                    if not is_allowed_staff_email(teacher.email):
                        raise ValueError("Selected teacher profile does not use an allowed email format")
                    if normalize_email(teacher.email) != username:
                        raise ValueError("Teacher login username must match the selected teacher profile email")
                    department_id = teacher.department_id
                    student_id = None
                elif role == 'student':
                    student = Student.query.get(int(student_id)) if student_id else None
                    if not student:
                        raise ValueError("Student profile is required for student accounts")
                    if not can_access_student(student):
                        raise ValueError("You can create student accounts only for your department")
                    department_id = student.department_id
                    teacher_id = None
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
                    student_id=student_id
                )
                db.session.add(new_admin)
                db.session.commit()
                message = f"{role_label(role)} account '{username}' added successfully!"
            except Exception as e:
                db.session.rollback()
                error = f"Error adding account: {str(e)}"
    
    if is_super_admin():
        admins = Admin.query.order_by(Admin.role, Admin.username).all()
    else:
        admins = Admin.query.filter(
            Admin.department_id == current_department_id()
        ).order_by(Admin.role, Admin.username).all()
    
    return render_template('add_admin.html', 
                         admins=admins, 
                         departments=visible_departments_query().order_by(Department.name).all(),
                         teachers=visible_teachers_query().order_by(Teacher.name).all(),
                         students=visible_students_query().order_by(Student.roll_no).all(),
                         roles=allowed_roles,
                         message=message, 
                         error=error)

@app.route('/delete_admin/<int:id>', methods=['POST'])
@login_required
@role_required('super_admin', 'hod')
def delete_admin(id):
    admin = Admin.query.get_or_404(id)
    if user_role() == 'hod':
        if admin.id == current_user.id or admin.role not in ('teacher', 'student') or admin.department_id != current_department_id():
            abort(403)
    
    # Prevent deleting the last admin
    if is_super_admin() and Admin.query.count() <= 1:
        return redirect(url_for('add_admin'))
    
    db.session.delete(admin)
    db.session.commit()
    return redirect(url_for('add_admin'))



with app.app_context():
    db.create_all()
    ensure_rbac_columns()
    if not Admin.query.first():
        db.session.add(Admin(username='admin', password=generate_password_hash('admin123'), role='super_admin'))
        db.session.commit()
    ensure_existing_student_accounts()

if __name__ == '__main__':
    os.makedirs('static/qrcodes', exist_ok=True)
    app.run(host='0.0.0.0', port=5000, debug=True)
