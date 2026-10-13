"""Shared fixtures. Every test gets its own temporary SQLite database built by the real migrations."""
from datetime import timedelta
from types import SimpleNamespace

import pytest
from flask import g
from flask.testing import FlaskClient
from werkzeug.security import generate_password_hash as _generate_password_hash

from attendqr import create_app
from attendqr.extensions import db
from attendqr.models import Admin, Attendance, ClassSession, Department, Semester, Student, Subject, Teacher
from attendqr.utils import ist_now

PASSWORD = 'Passw0rd!'


def generate_password_hash(password):
    # Seeded users only; a cheap method keeps the suite fast and check_password_hash accepts any method.
    return _generate_password_hash(password, method='pbkdf2:sha256:1000')


class IsolatedClient(FlaskClient):
    """Tests hold an app context open so they can use db.session directly. Requests then
    share that context's `g`, where Flask-Login caches the current user, so clear the
    per-request values before each request to keep clients independent."""

    def open(self, *args, **kwargs):
        g.pop('_login_user', None)
        g.pop('csp_nonce', None)
        return super().open(*args, **kwargs)


@pytest.fixture
def app(tmp_path):
    app = create_app({
        'TESTING': True,
        'SECRET_KEY': 'test-secret-key-for-the-suite-only',
        'SQLALCHEMY_DATABASE_URI': f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        'DEFAULT_PRINCIPAL_EMAIL': 'principal@example.com',
        'DEFAULT_PRINCIPAL_PASSWORD': 'Initial123',
        'ALLOWED_STAFF_DOMAINS': ['@rcciit.org.in', '@gmail.com'],
        'APP_BASE_URL': 'http://attendqr.test',
    })
    app.test_client_class = IsolatedClient
    with app.app_context():
        yield app
        db.session.remove()
        db.engine.dispose()


def make_account(username, role, password=PASSWORD, **fields):
    account = Admin(
        username=username,
        password=generate_password_hash(password),
        role=role,
        is_active=True,
        must_change_password=False,
        **fields,
    )
    db.session.add(account)
    db.session.flush()
    return account


def make_student(name, roll_no, email, department, semester, password=PASSWORD, must_change=False):
    student = Student(name=name, roll_no=roll_no, email=email,
                      department_id=department.id, semester_id=semester.id)
    db.session.add(student)
    db.session.flush()
    account = Admin(
        username=email,
        password=generate_password_hash(password),
        role='student',
        department_id=department.id,
        student_id=student.id,
        is_active=True,
        must_change_password=must_change,
    )
    db.session.add(account)
    db.session.flush()
    return student


@pytest.fixture
def data(app):
    """A department with two semesters, a teacher with a subject, students, and staff accounts."""
    principal = Admin.query.filter_by(username='principal@example.com').one()
    principal.password = generate_password_hash(PASSWORD)
    principal.must_change_password = False

    mca = Department(name='Master of Computer Application (MCA)', branch='MCA')
    other = Department(name='Bachelor of Technology (CSE)', branch='BTECH')
    db.session.add_all([mca, other])
    db.session.flush()

    sem1 = Semester(name='Semester 1', department_id=mca.id)
    sem2 = Semester(name='Semester 2', department_id=mca.id)
    other_sem = Semester(name='Semester 1', department_id=other.id)
    db.session.add_all([sem1, sem2, other_sem])
    db.session.flush()

    teacher = Teacher(name='Asha Teacher', email='asha@rcciit.org.in', department_id=mca.id)
    other_teacher = Teacher(name='Ravi Teacher', email='ravi@rcciit.org.in', department_id=other.id)
    db.session.add_all([teacher, other_teacher])
    db.session.flush()

    subject = Subject(name='DBMS', paper_code='MCA101', department_id=mca.id, semester_id=sem1.id, teacher_id=teacher.id)
    subject2 = Subject(name='Networks', paper_code='MCA102', department_id=mca.id, semester_id=sem1.id, teacher_id=teacher.id)
    db.session.add_all([subject, subject2])
    db.session.flush()

    teacher_account = make_account('asha@rcciit.org.in', 'teacher', department_id=mca.id, teacher_id=teacher.id)
    hod_account = make_account('hod@rcciit.org.in', 'hod', department_id=mca.id)
    other_teacher_account = make_account('ravi@rcciit.org.in', 'teacher', department_id=other.id, teacher_id=other_teacher.id)

    student = make_student('Rahul Das', 'MCA001', 'rahul@example.com', mca, sem1)
    student2 = make_student('Priya Sen', 'MCA002', 'priya@example.com', mca, sem1)
    sem2_student = make_student('Late Joiner', 'MCA201', 'late@example.com', mca, sem2)
    db.session.commit()

    return SimpleNamespace(
        principal=principal.username,
        hod=hod_account.username,
        teacher=teacher_account.username,
        other_teacher=other_teacher_account.username,
        student=student.email,
        student2=student2.email,
        sem2_student=sem2_student.email,
        mca_id=mca.id,
        other_id=other.id,
        sem1_id=sem1.id,
        sem2_id=sem2.id,
        other_sem_id=other_sem.id,
        teacher_id=teacher.id,
        subject_id=subject.id,
        subject2_id=subject2.id,
        student_id=student.id,
        student2_id=student2.id,
    )


def add_session(data, subject_id=None, active=True, present=(), special_title=None, days_ago=0):
    now = ist_now() - timedelta(days=days_ago)
    class_session = ClassSession(
        subject_id=subject_id or data.subject_id, teacher_id=data.teacher_id, special_title=special_title,
        token=f"t-{ClassSession.query.count()}-{now.timestamp()}", created_at=now,
        expires_at=now + timedelta(minutes=30), is_active=active,
    )
    db.session.add(class_session)
    db.session.flush()
    for student_id in present:
        db.session.add(Attendance(student_id=student_id, session_id=class_session.id, timestamp=now))
    db.session.commit()
    return class_session


# ── Client helpers ───────────────────────────────────────────

def csrf_token(client):
    with client.session_transaction() as sess:
        token = sess.get('_csrf_token')
        if not token:
            token = sess['_csrf_token'] = 'test-csrf-token'
    return token


def post(client, url, data=None, **kwargs):
    data = dict(data or {})
    data['_csrf_token'] = csrf_token(client)
    return client.post(url, data=data, **kwargs)


def post_json(client, url, payload=None):
    return client.post(url, json=payload or {}, headers={'X-CSRFToken': csrf_token(client)})


def login(client, username, password=PASSWORD, expect_success=True, **kwargs):
    response = post(client, '/login', {'username': username, 'password': password}, **kwargs)
    if expect_success:
        assert response.status_code == 302, f"login failed for {username}: {response.status_code}"
    return response


@pytest.fixture
def client_for(app):
    """Factory: a test client logged in as the given user."""
    def make(username, password=PASSWORD):
        client = app.test_client()
        login(client, username, password)
        return client
    return make
