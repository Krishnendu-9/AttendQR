"""Role-based access (ported from the old smoke test) and account management rules."""
import html
import io

import pytest

from attendqr.extensions import db
from attendqr.models import ClassSession, Student, Subject, Teacher, User
from attendqr.utils import utc_now
from conftest import PASSWORD, make_account, post


def test_protected_page_redirects_to_login_with_next(app):
    response = app.test_client().get('/accounts')
    assert response.status_code == 302
    assert '/login?next=%2Faccounts' in response.headers['Location']


def test_login_redirects_to_requested_page(app, data):
    client = app.test_client()
    response = post(client, '/login?next=%2Faccounts', {'username': data.principal, 'password': PASSWORD, 'next': '/accounts'})
    assert response.status_code == 302
    assert response.headers['Location'].endswith('/accounts')


@pytest.mark.parametrize('who, pages', [
    ('principal', {'/': 200, '/account': 200, '/students': 200, '/students/import-template': 200,
                   '/accounts': 200, '/generate_qr': 200, '/reports': 200, '/departments': 200,
                   '/semesters': 200, '/subjects': 200, '/teachers': 200}),
    ('hod', {'/': 200, '/students': 200, '/accounts': 200, '/generate_qr': 200, '/reports': 200,
             '/departments': 200, '/teachers': 200}),
    ('teacher', {'/': 200, '/students': 200, '/generate_qr': 200, '/reports': 200, '/subjects': 200,
                 '/accounts': 403, '/departments': 403, '/teachers': 403}),
    ('student', {'/': 200, '/student/attendance': 200, '/account': 200, '/students': 403,
                 '/generate_qr': 403, '/reports': 403, '/subjects': 403}),
])
def test_page_access_by_role(data, client_for, who, pages):
    client = client_for(getattr(data, who))
    for path, expected in pages.items():
        assert client.get(path).status_code == expected, path


def test_unknown_role_fails_closed(data, client_for):
    client = client_for(data.hod)
    account = User.query.filter_by(username=data.hod).one()
    account.role = 'superuser'
    db.session.commit()
    assert client.get('/accounts').status_code == 403
    assert client.get('/students').status_code == 403


def test_student_accounts_cannot_be_created_or_deleted_from_accounts(data, client_for):
    client = client_for(data.principal)
    response = post(client, '/accounts', {
        'username': 'blocked@gmail.com', 'role': 'student',
        'password': 'Bright7Lamp', 'confirm_password': 'Bright7Lamp',
    }, follow_redirects=True)
    assert 'Invalid role selected' in response.get_data(as_text=True)

    student_account = User.query.filter_by(username=data.student).one()
    assert post(client, f'/delete_admin/{student_account.id}').status_code == 403


def test_hod_only_offers_teacher_accounts(data, client_for):
    html = client_for(data.hod).get('/accounts').get_data(as_text=True)
    assert '<option value="teacher"' in html
    assert '<option value="hod"' not in html
    assert '<option value="student"' not in html


def test_principal_account_rules(data, client_for):
    client = client_for(data.principal)
    response = post(client, '/accounts', {
        'username': 'second.principal@gmail.com', 'role': 'principal',
        'password': 'Bright7Lamp', 'confirm_password': 'Bright7Lamp',
    }, follow_redirects=True)
    assert "Principal account 'second.principal@gmail.com' added" in html.unescape(response.get_data(as_text=True))

    me = User.query.filter_by(username=data.principal).one()
    assert post(client, f'/delete_admin/{me.id}').status_code == 403

    extra = User.query.filter_by(username='second.principal@gmail.com').one()
    assert post(client, f'/delete_admin/{extra.id}').status_code == 302
    assert db.session.get(User, extra.id) is None


def test_creating_teacher_account_creates_linked_profile(data, client_for):
    client = client_for(data.principal)
    response = post(client, '/accounts', {
        'username': 'new.teacher@gmail.com', 'role': 'teacher', 'teacher_name': 'New Teacher',
        'department_id': data.mca_id, 'password': 'Bright7Lamp', 'confirm_password': 'Bright7Lamp',
    }, follow_redirects=True)
    assert 'Teacher account' in response.get_data(as_text=True)
    profile = Teacher.query.filter_by(email='new.teacher@gmail.com').one()
    account = User.query.filter_by(username='new.teacher@gmail.com').one()
    assert account.teacher_id == profile.id
    assert account.must_change_password


def test_account_creation_enforces_password_policy(data, client_for):
    client = client_for(data.principal)
    response = post(client, '/accounts', {
        'username': 'weak@gmail.com', 'role': 'principal',
        'password': 'abcdefgh', 'confirm_password': 'abcdefgh',
    }, follow_redirects=True)
    assert 'at least one letter and one number' in response.get_data(as_text=True)
    assert not User.query.filter_by(username='weak@gmail.com').first()


def test_hod_cannot_edit_principal(data, client_for):
    principal = User.query.filter_by(username=data.principal).one()
    response = post(client_for(data.hod), f'/edit_admin/{principal.id}', {'username': 'x@gmail.com'})
    assert response.status_code == 403


def test_teachers_page_has_no_creation_controls(data, client_for):
    html = client_for(data.principal).get('/teachers').get_data(as_text=True)
    assert 'Add Teacher' not in html and 'teacher_email' not in html


def test_teacher_import_is_scoped_to_own_department(data, client_for):
    client = client_for(data.teacher)
    csv_data = "name,roll_no,email,semester\nSmoke Student,MCA900,smoke@example.com,Semester 1\n"
    response = post(client, '/students/import', {
        'student_file': (io.BytesIO(csv_data.encode()), 'students.csv'),
    }, content_type='multipart/form-data', follow_redirects=True)
    assert 'Imported 1 student profile' in response.get_data(as_text=True)
    student = Student.query.filter_by(email='smoke@example.com').one()
    assert student.department_id == data.mca_id
    assert User.query.filter_by(username='smoke@example.com', role='student').one().must_change_password


def test_students_list_is_paginated_and_filtered(app, data, client_for):
    app.config['STUDENTS_PER_PAGE'] = 2
    client = client_for(data.principal)
    first = client.get('/students').get_data(as_text=True)
    assert 'aria-label="Pages"' in first
    filtered = client.get('/students', query_string={'q': 'priya'}).get_data(as_text=True)
    assert 'Priya Sen' in filtered and 'Rahul Das' not in filtered


def test_hod_cannot_see_other_department_students(data, client_for):
    other_student = Student(name='Outsider', roll_no='X1', email='x1@example.com',
                            department_id=data.other_id, semester_id=data.other_sem_id)
    db.session.add(other_student)
    db.session.commit()
    client = client_for(data.hod)
    assert 'Outsider' not in client.get('/students').get_data(as_text=True)
    assert post(client, f'/students/reset-device/{other_student.id}').status_code == 403


def test_without_staff_domains_no_staff_account_can_be_created(app, data, client_for):
    app.config['ALLOWED_STAFF_DOMAINS'] = []
    client = client_for(data.principal)
    page = client.get('/accounts').get_data(as_text=True)
    assert 'cannot be created until the server administrator sets' in page
    response = post(client, '/accounts', {
        'username': 'new.teacher@rcciit.org.in', 'role': 'teacher', 'teacher_name': 'New Teacher',
        'department_id': data.mca_id, 'password': 'Passw0rd!x', 'confirm_password': 'Passw0rd!x',
    }, follow_redirects=True)
    assert 'must set ALLOWED_STAFF_DOMAINS' in response.get_data(as_text=True)
    assert not User.query.filter_by(username='new.teacher@rcciit.org.in').first()


def test_staff_domain_setting_has_no_built_in_default(monkeypatch):
    from attendqr.config import staff_domains_from_env

    monkeypatch.delenv('ALLOWED_STAFF_DOMAINS', raising=False)
    assert staff_domains_from_env() == []
    monkeypatch.setenv('ALLOWED_STAFF_DOMAINS', 'college.edu, @Dept.College.edu')
    assert staff_domains_from_env() == ['@college.edu', '@dept.college.edu']


def test_malformed_staff_domain_stops_the_app_and_public_ones_warn(tmp_path, caplog):
    import logging

    import pytest

    from attendqr import create_app

    settings = {
        'TESTING': True, 'SECRET_KEY': 'x' * 32, 'AUTO_MIGRATE': False,
        'SQLALCHEMY_DATABASE_URI': f"sqlite:///{(tmp_path / 'domains.db').as_posix()}",
    }
    with pytest.raises(ValueError, match='ALLOWED_STAFF_DOMAINS has an invalid entry'):
        create_app({**settings, 'ALLOWED_STAFF_DOMAINS': ['@college edu']})
    with caplog.at_level(logging.WARNING):
        create_app({**settings, 'ALLOWED_STAFF_DOMAINS': ['@college.edu', '@gmail.com']})
    assert 'public email domains (@gmail.com)' in caplog.text


# ── Accounts that lost their scope ───────────────────────────

def test_teacher_account_without_a_profile_sees_nothing(app, data, client_for):
    # A special class in another department with no teacher: `teacher_id IS NULL`.
    special = Subject(name='__SPECIAL_CLASS__', department_id=data.other_id, semester_id=data.other_sem_id)
    db.session.add(special)
    db.session.flush()
    now = utc_now()
    orphan = ClassSession(subject_id=special.id, teacher_id=None, special_title='Hidden Workshop', token='orphan',
                          created_at=now, expires_at=now, is_active=True)
    db.session.add(orphan)
    make_account('ghost@rcciit.org.in', 'teacher', department_id=data.mca_id)
    db.session.commit()

    client = client_for('ghost@rcciit.org.in')
    assert 'Hidden Workshop' not in client.get('/').get_data(as_text=True)
    assert 'Hidden Workshop' not in client.get('/generate_qr').get_data(as_text=True)
    assert client.get(f'/generate_qr?session_id={orphan.id}').status_code == 403
    assert client.get(f'/api/session_attendance/{orphan.id}').status_code == 403
    assert client.get(f'/api/teachers/{data.mca_id}').status_code == 403
    response = post(client, '/generate_qr', {
        'class_mode': 'regular', 'department_id': data.mca_id, 'semester_id': data.sem1_id,
        'subject_id': data.subject_id, 'duration': 30,
    })
    assert response.status_code == 403
    assert ClassSession.query.count() == 1


def test_hod_without_a_department_manages_no_accounts(app, data, client_for):
    make_account('lost-hod@rcciit.org.in', 'hod')
    make_account('loose-teacher@rcciit.org.in', 'teacher')
    db.session.commit()
    client = client_for('lost-hod@rcciit.org.in')

    page = client.get('/accounts').get_data(as_text=True)
    assert data.principal not in page and 'loose-teacher@rcciit.org.in' not in page
    loose = User.query.filter_by(username='loose-teacher@rcciit.org.in').one()
    assert post(client, f'/toggle_admin_status/{loose.id}').status_code == 403
    assert post(client, f'/reset_admin_password/{loose.id}').status_code == 403
    assert client.get('/').status_code == 200
