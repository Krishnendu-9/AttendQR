"""`flask seed-demo`: sample data for demonstrations."""
from attendqr.extensions import db
from attendqr.models import Attendance, ClassSession, Department, Student, User
from attendqr.services.demo import DEFAULT_DEMO_PASSWORD, seed_demo
from conftest import login

DEMO_PASSWORD = 'Demo-pass-123'


def run_seed(app, *args):
    return app.test_cli_runner().invoke(args=['seed-demo', '--password', DEMO_PASSWORD, *args])


def test_seed_demo_loads_a_college_and_prints_logins(app):
    result = run_seed(app)

    assert result.exit_code == 0, result.output
    assert Department.query.count() == 2
    assert Student.query.count() == 40
    assert ClassSession.query.count() > 50
    assert Attendance.query.filter_by(source=Attendance.SOURCE_MANUAL).count() > 0
    # Every student has a login, and nobody is asked to change their password first.
    assert User.query.filter_by(role='student').count() == 40
    assert User.query.filter_by(must_change_password=True).count() == 0
    assert DEMO_PASSWORD in result.output
    assert 'principal@example.com' in result.output


def test_every_printed_login_works(app):
    summary = seed_demo(DEMO_PASSWORD)

    assert {role for role, _, _ in summary.logins} == {'Principal', 'HOD', 'Teacher', 'Student'}
    for role, username, _ in summary.logins:
        client = app.test_client()
        response = login(client, username, DEMO_PASSWORD)
        assert response.status_code == 302
        if role == 'Student':
            assert client.get('/student/attendance').status_code == 200


def test_demo_has_students_in_shortage_and_pages_render(app, client_for):
    seed_demo(DEMO_PASSWORD)
    client = client_for('principal@example.com', DEMO_PASSWORD)

    department = Department.query.order_by(Department.id).first()
    shortage = client.get(f'/reports/shortage?department_id={department.id}&semester_id='
                          f'{department.semesters[0].id}')
    assert shortage.status_code == 200
    assert b'CSE21' in shortage.data  # roll numbers of that class appear in the shortage table
    for url in ('/', '/reports', '/students', '/generate_qr'):
        assert client.get(url).status_code == 200, url


def test_void_session_has_no_attendance(app):
    seed_demo(DEMO_PASSWORD)

    void = ClassSession.query.filter_by(is_active=False).all()
    assert len(void) == 1
    assert Attendance.query.filter_by(session_id=void[0].id).count() == 0


def test_refuses_to_seed_over_existing_records(app, data):
    result = run_seed(app)

    assert result.exit_code != 0
    assert '--reset' in result.output
    assert Department.query.count() == 2  # the fixture's departments are untouched


def test_reset_replaces_existing_records(app, data):
    result = run_seed(app, '--reset', '--yes')

    assert result.exit_code == 0, result.output
    assert Student.query.filter_by(email='rahul@example.com').first() is None
    assert Student.query.count() == 40


def test_reset_asks_for_confirmation(app, data):
    result = app.test_cli_runner().invoke(args=['seed-demo', '--reset'], input='n\n')

    assert result.exit_code != 0
    db.session.expire_all()
    assert Student.query.filter_by(email='rahul@example.com').first() is not None


def _attendance_fingerprint():
    return sorted((a.student.roll_no, a.session.created_at, a.source) for a in Attendance.query)


def test_same_seed_gives_the_same_data(app):
    seed_demo(DEFAULT_DEMO_PASSWORD, seed=7)
    first = _attendance_fingerprint()

    seed_demo(DEFAULT_DEMO_PASSWORD, seed=7, reset=True)

    assert _attendance_fingerprint() == first
