"""Role-based access (ported from the old smoke test) and account management rules."""
import html
import io

import pytest

from attendqr.extensions import db
from attendqr.models import Admin, Student, Teacher
from conftest import PASSWORD, post


def test_protected_page_redirects_to_login_with_next(app):
    response = app.test_client().get('/add_admin')
    assert response.status_code == 302
    assert '/login?next=%2Fadd_admin' in response.headers['Location']


def test_login_redirects_to_requested_page(app, data):
    client = app.test_client()
    response = post(client, '/login?next=%2Fadd_admin', {'username': data.principal, 'password': PASSWORD, 'next': '/add_admin'})
    assert response.status_code == 302
    assert response.headers['Location'].endswith('/add_admin')


@pytest.mark.parametrize('who, pages', [
    ('principal', {'/': 200, '/account': 200, '/students': 200, '/students/import-template': 200,
                   '/add_admin': 200, '/generate_qr': 200, '/reports': 200, '/departments': 200,
                   '/semesters': 200, '/subjects': 200, '/teachers': 200}),
    ('hod', {'/': 200, '/students': 200, '/add_admin': 200, '/generate_qr': 200, '/reports': 200,
             '/departments': 200, '/teachers': 200}),
    ('teacher', {'/': 200, '/students': 200, '/generate_qr': 200, '/reports': 200, '/subjects': 200,
                 '/add_admin': 403, '/departments': 403, '/teachers': 403}),
    ('student', {'/': 200, '/student/attendance': 200, '/account': 200, '/students': 403,
                 '/generate_qr': 403, '/reports': 403, '/subjects': 403}),
])
def test_page_access_by_role(data, client_for, who, pages):
    client = client_for(getattr(data, who))
    for path, expected in pages.items():
        assert client.get(path).status_code == expected, path


def test_unknown_role_fails_closed(data, client_for):
    client = client_for(data.hod)
    account = Admin.query.filter_by(username=data.hod).one()
    account.role = 'superuser'
    db.session.commit()
    assert client.get('/add_admin').status_code == 403
    assert client.get('/students').status_code == 403


def test_student_accounts_cannot_be_created_or_deleted_from_accounts(data, client_for):
    client = client_for(data.principal)
    response = post(client, '/add_admin', {
        'username': 'blocked@gmail.com', 'role': 'student',
        'password': 'Secret123', 'confirm_password': 'Secret123',
    }, follow_redirects=True)
    assert 'Invalid role selected' in response.get_data(as_text=True)

    student_account = Admin.query.filter_by(username=data.student).one()
    assert post(client, f'/delete_admin/{student_account.id}').status_code == 403


def test_hod_only_offers_teacher_accounts(data, client_for):
    html = client_for(data.hod).get('/add_admin').get_data(as_text=True)
    assert '<option value="teacher"' in html
    assert '<option value="hod"' not in html
    assert '<option value="student"' not in html


def test_principal_account_rules(data, client_for):
    client = client_for(data.principal)
    response = post(client, '/add_admin', {
        'username': 'second.principal@gmail.com', 'role': 'super_admin',
        'password': 'Secret123', 'confirm_password': 'Secret123',
    }, follow_redirects=True)
    assert "Principal account 'second.principal@gmail.com' added" in html.unescape(response.get_data(as_text=True))

    me = Admin.query.filter_by(username=data.principal).one()
    assert post(client, f'/delete_admin/{me.id}').status_code == 403

    extra = Admin.query.filter_by(username='second.principal@gmail.com').one()
    assert post(client, f'/delete_admin/{extra.id}').status_code == 302
    assert db.session.get(Admin, extra.id) is None


def test_creating_teacher_account_creates_linked_profile(data, client_for):
    client = client_for(data.principal)
    response = post(client, '/add_admin', {
        'username': 'new.teacher@gmail.com', 'role': 'teacher', 'teacher_name': 'New Teacher',
        'department_id': data.mca_id, 'password': 'Secret123', 'confirm_password': 'Secret123',
    }, follow_redirects=True)
    assert 'Teacher account' in response.get_data(as_text=True)
    profile = Teacher.query.filter_by(email='new.teacher@gmail.com').one()
    account = Admin.query.filter_by(username='new.teacher@gmail.com').one()
    assert account.teacher_id == profile.id
    assert account.must_change_password


def test_account_creation_enforces_password_policy(data, client_for):
    client = client_for(data.principal)
    response = post(client, '/add_admin', {
        'username': 'weak@gmail.com', 'role': 'super_admin',
        'password': 'abcdefgh', 'confirm_password': 'abcdefgh',
    }, follow_redirects=True)
    assert 'at least one letter and one number' in response.get_data(as_text=True)
    assert not Admin.query.filter_by(username='weak@gmail.com').first()


def test_hod_cannot_edit_principal(data, client_for):
    principal = Admin.query.filter_by(username=data.principal).one()
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
    assert Admin.query.filter_by(username='smoke@example.com', role='student').one().must_change_password


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
