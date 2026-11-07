"""Student logins start with a random one-time password, never the guessable roll number."""
import io
import re

from attendqr.models import Admin
from attendqr.security import TEMPORARY_PASSWORD_ALPHABET, generate_temporary_password
from conftest import login, post

ONE_TIME = re.compile(r'([A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4})')


def test_temporary_passwords_are_readable_and_random():
    passwords = {generate_temporary_password() for _ in range(50)}
    assert len(passwords) == 50
    for password in passwords:
        assert re.fullmatch(r'[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4}', password)
        assert set(password.replace('-', '')) <= set(TEMPORARY_PASSWORD_ALPHABET)


def add_student(client, data, roll_no='MCA050', email='new.student@example.com'):
    response = post(client, '/students', {
        'name': 'New Student', 'roll_no': roll_no, 'email': email,
        'department_id': data.mca_id, 'semester_id': data.sem1_id,
    }, follow_redirects=True)
    match = ONE_TIME.search(response.get_data(as_text=True))
    assert match, "the one-time password is shown to the staff member"
    return match.group(1)


def test_new_student_logs_in_with_the_one_time_password_not_the_roll_number(app, data, client_for):
    password = add_student(client_for(data.hod), data)

    assert login(app.test_client(), 'new.student@example.com', 'MCA050', expect_success=False).status_code == 200
    phone = app.test_client()
    login(phone, 'new.student@example.com', password)
    assert phone.get('/').headers['Location'].startswith('/account')
    assert Admin.query.filter_by(username='new.student@example.com').one().must_change_password


def test_reset_gives_a_new_one_time_password(app, data, client_for):
    teacher = client_for(data.teacher)
    response = post(teacher, f'/students/reset-password/{data.student_id}', follow_redirects=True)
    password = ONE_TIME.search(response.get_data(as_text=True)).group(1)

    assert login(app.test_client(), data.student, 'MCA001', expect_success=False).status_code == 200
    login(app.test_client(), data.student, password)
    assert Admin.query.filter_by(username=data.student).one().must_change_password


def test_import_shows_each_one_time_password_once(app, data, client_for):
    csv_data = (
        "name,roll_no,email,semester\n"
        "First Import,MCA901,first.import@example.com,Semester 1\n"
        "Second Import,MCA902,second.import@example.com,Semester 1\n"
    )
    response = post(client_for(data.hod), '/students/import', {
        'student_file': (io.BytesIO(csv_data.encode()), 'students.csv'),
    }, content_type='multipart/form-data')
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert 'shown <strong>only on this page</strong>' in page
    assert response.headers['Cache-Control'] == 'no-store'
    passwords = ONE_TIME.findall(page)
    # Each password appears in the table and in the JSON used for the CSV download.
    assert len(set(passwords)) == 2

    for email in ('first.import@example.com', 'second.import@example.com'):
        assert any(
            login(app.test_client(), email, password, expect_success=False).status_code == 302
            for password in set(passwords)
        )


def test_temporary_passwords_use_the_cheaper_hash_and_still_verify():
    from werkzeug.security import check_password_hash

    from attendqr.security import TEMPORARY_PASSWORD_HASH_METHOD, hash_temporary_password

    hashed = hash_temporary_password('K7RM-2XQD-9HFA')
    assert hashed.startswith(TEMPORARY_PASSWORD_HASH_METHOD + '$')
    assert check_password_hash(hashed, 'K7RM-2XQD-9HFA')
