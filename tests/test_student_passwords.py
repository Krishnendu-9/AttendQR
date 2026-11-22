"""Student logins start with a random one-time password, never the guessable roll number."""
import io
import re

from attendqr.extensions import db
from attendqr.models import User
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
    assert User.query.filter_by(username='new.student@example.com').one().must_change_password


def test_reset_gives_a_new_one_time_password(app, data, client_for):
    teacher = client_for(data.teacher)
    response = post(teacher, f'/students/reset-password/{data.student_id}', follow_redirects=True)
    password = ONE_TIME.search(response.get_data(as_text=True)).group(1)

    assert login(app.test_client(), data.student, 'MCA001', expect_success=False).status_code == 200
    login(app.test_client(), data.student, password)
    assert User.query.filter_by(username=data.student).one().must_change_password


def test_teacher_resets_passwords_only_in_classes_they_teach(app, data, client_for):
    """The teacher has subjects in Semester 1 only; a reset would hand them a Semester 2 student's account."""
    other_class = User.query.filter_by(username=data.sem2_student).one()
    old_hash = other_class.password
    teacher = client_for(data.teacher)

    assert 'Reset password' not in teacher.get(f'/students/{other_class.student_id}').get_data(as_text=True)
    assert 'Reset password' in teacher.get(f'/students/{data.student_id}').get_data(as_text=True)
    assert post(teacher, f'/students/reset-password/{other_class.student_id}').status_code == 403
    db.session.expire_all()
    assert db.session.get(User, other_class.id).password == old_hash
    login(app.test_client(), data.sem2_student)  # the student's own password still works

    response = post(client_for(data.hod), f'/students/reset-password/{other_class.student_id}', follow_redirects=True)
    assert ONE_TIME.search(response.get_data(as_text=True))


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


def test_oversized_import_is_refused_before_adding_anyone(app, data, client_for):
    app.config['MAX_IMPORT_ROWS'] = 3
    rows = ''.join(f"Student {n},MCA9{n:02d},bulk{n}@example.com,Semester 1\n" for n in range(4))
    response = post(client_for(data.hod), '/students/import', {
        'student_file': (io.BytesIO(("name,roll_no,email,semester\n" + rows).encode()), 'students.csv'),
    }, content_type='multipart/form-data', follow_redirects=True)
    assert 'more than 3 student rows' in response.get_data(as_text=True)
    assert not User.query.filter(User.username.like('bulk%')).count()


def test_a_teacher_adds_students_only_to_classes_they_teach(data, client_for):
    teacher = client_for(data.teacher)
    add_student(teacher, data)  # Semester 1, where Asha teaches DBMS
    response = post(teacher, '/students', {
        'name': 'Other Class', 'roll_no': 'MCA299', 'email': 'other.class@example.com',
        'department_id': data.mca_id, 'semester_id': data.sem2_id,
    }, follow_redirects=True)
    assert 'not a class you teach' in response.get_data(as_text=True)
    assert User.query.filter_by(username='other.class@example.com').first() is None


def test_a_teacher_imports_students_only_into_classes_they_teach(data, client_for):
    rows = "Taught,MCA060,taught@example.com,Semester 1\nNot Taught,MCA260,not.taught@example.com,Semester 2\n"
    response = post(client_for(data.teacher), '/students/import', {
        'student_file': (io.BytesIO(("name,roll_no,email,semester\n" + rows).encode()), 'students.csv'),
    }, content_type='multipart/form-data', follow_redirects=True)
    assert 'Semester 2 is not a class you teach' in response.get_data(as_text=True)
    assert User.query.filter_by(username='taught@example.com').first() is not None
    assert User.query.filter_by(username='not.taught@example.com').first() is None


def test_a_password_reset_does_not_reactivate_a_deactivated_student(data, client_for):
    account = User.query.filter_by(username=data.student).one()
    account.is_active = False
    db.session.commit()
    post(client_for(data.hod), f'/students/reset-password/{data.student_id}')
    db.session.expire_all()
    assert db.session.get(User, account.id).is_active is False


def test_one_time_passwords_never_travel_in_the_session_cookie(data, client_for):
    """Flashed messages live in the session cookie, which is signed but readable, so passwords are only rendered."""
    hod, principal = client_for(data.hod), client_for(data.principal)
    teacher_account = User.query.filter_by(username=data.teacher).one()
    responses = {
        'added': (hod, post(hod, '/students', {'name': 'New Student', 'roll_no': 'MCA050',
                                               'email': 'new.student@example.com', 'department_id': data.mca_id,
                                               'semester_id': data.sem1_id})),
        'student reset': (hod, post(hod, f'/students/reset-password/{data.student_id}')),
        'staff reset': (principal, post(principal, f'/reset_admin_password/{teacher_account.id}')),
    }
    for what, (client, response) in responses.items():
        assert response.status_code == 200, what
        password = ONE_TIME.search(response.get_data(as_text=True)).group(1)
        assert response.headers['Cache-Control'] == 'no-store', what
        with client.session_transaction() as session:
            assert password not in str(dict(session)), what
