"""`flask seed-demo`: sample data for demonstrations."""
from attendqr.extensions import db
from attendqr.models import Attendance, AuditChain, ClassSession, Department, Student, User
from attendqr.services.audit import verify_chain
from attendqr.services.demo import DEFAULT_DEMO_PASSWORD, seed_demo
from conftest import login, post

DEMO_PASSWORD = 'Demo-pass-123'


def run_seed(app, *args):
    return app.test_cli_runner().invoke(args=['seed-demo', '--password', DEMO_PASSWORD, *args])


def test_seed_demo_loads_a_college_and_prints_logins(app):
    result = run_seed(app)

    assert result.exit_code == 0, result.output
    assert Department.query.count() == 2
    assert Student.query.count() == 68
    assert ClassSession.query.count() > 50
    assert Attendance.query.filter_by(source=Attendance.SOURCE_MANUAL).count() > 0
    # Every student has a login, and nobody is asked to change their password first.
    assert User.query.filter_by(role='student').count() == 68
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
    assert b'MCA2024' in shortage.data  # roll numbers of the MCA class appear in the shortage table
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
    assert Student.query.count() == 68


def test_reset_keeps_the_activity_log_sealed(app, client_for, data):
    """Deleting every row took the audit chain's head too, and nothing was sealed after it."""
    post(client_for(data.principal), '/semesters', {'name': 'Extra', 'department_id': data.mca_id})
    assert verify_chain().entries  # a chain to wipe

    assert run_seed(app, '--reset', '--yes').exit_code == 0

    check = verify_chain()
    assert db.session.get(AuditChain, 1) is not None
    assert check.ok and check.entries >= 1 and check.unsealed == 0


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


def test_demo_has_a_lab_split_into_two_batches(app):
    """Each batch's lab sessions count only for its own members, so nobody is absent for the other batch."""
    from attendqr.models import ClassGroup, ClassGroupMember
    from attendqr.services.reports import NOT_IN_GROUP, ReportFilters, build_attendance_matrix

    seed_demo(DEMO_PASSWORD)
    groups = ClassGroup.query.order_by(ClassGroup.name).all()
    assert [g.name for g in groups] == ['Gr-1', 'Gr-2']
    assert ClassGroupMember.query.count() == 40
    lab_sessions = ClassSession.query.filter(ClassSession.group_id.isnot(None)).all()
    assert lab_sessions and {s.subject.paper_code for s in lab_sessions} == {'MCA C191', 'MCA C192'}

    semester = groups[0].semester
    filters = ReportFilters(department_id=semester.department_id, semester_id=semester.id, subject_id=None,
                            parsed_from=None, parsed_to=None, date_from='', date_to='', error=None)
    with app.test_request_context():
        sessions, rows = build_attendance_matrix(filters)
    gr1_lab = next(s for s in sessions if s.group_id == groups[0].id)
    marks = [row['attendance'][gr1_lab.id] for row in rows]
    assert marks.count(NOT_IN_GROUP) == 20


def test_demo_has_a_co_taught_lab_and_a_course_that_does_not_count(app):
    from attendqr.models import Subject

    seed_demo(DEMO_PASSWORD)
    lab = Subject.query.filter_by(paper_code='MCA C192').one()
    assert lab.teacher and len(lab.co_teachers) == 1
    course = Subject.query.filter_by(paper_code='INTEL AI').one()
    assert course.counts_towards_attendance is False and course.sessions


def test_demo_has_a_published_routine_its_sessions_hold(app, client_for):
    from datetime import timedelta

    from attendqr.models import RoutineChange, Timetable
    from attendqr.services.occurrences import HELD, NOT_RECORDED, occurrences
    from attendqr.utils import local_now

    seed_demo(DEMO_PASSWORD)
    routine = Timetable.query.filter_by(status='published').one()
    linked = ClassSession.query.filter(ClassSession.timetable_entry_id.isnot(None)).count()
    assert routine.entries and linked > 20
    assert RoutineChange.query.count() == 2
    today = local_now().date()
    found = occurrences(today - timedelta(days=21), today, semester_ids=[routine.semester_id])
    statuses = {o.status for o in found}
    assert HELD in statuses and NOT_RECORDED in statuses  # the tutorial slot, recorded only last week

    hod = client_for('manas.ghosh@college.example', DEMO_PASSWORD)
    assert hod.get(f'/routine/class/{routine.semester_id}').status_code == 200
    assert hod.get(f'/reports/routine?semester_id={routine.semester_id}').status_code == 200
    teacher = client_for('ananya.mukherjee@college.example', DEMO_PASSWORD)
    assert teacher.get('/routine/me').status_code == 200


def test_demo_shows_an_mca_class_with_chocolate_and_an_hod_who_teaches(app, client_for):
    from attendqr.models import Subject, Timetable

    seed_demo(DEMO_PASSWORD)
    chocolate = Student.query.filter_by(roll_no='MCA2024003').one()
    assert (chocolate.name, chocolate.email) == ('Chocolate', 'mca2024003@college.example')
    assert chocolate.department.display_name == 'MCA' and chocolate.semester.name == 'Semester 1'

    hod = User.query.filter_by(username='manas.ghosh@college.example').one()
    assert hod.role == 'hod' and hod.teacher.name == 'Dr. Manas Ghosh'
    rdbms = Subject.query.filter_by(paper_code='MCA C102').one()
    assert rdbms.name == 'RDBMS' and rdbms.teacher_id == hod.teacher_id
    routine = Timetable.query.filter_by(status='published').one()
    assert routine.semester_id == chocolate.semester_id
    # The two lab batches run side by side, in different labs.
    side_by_side = [e for e in routine.entries if e.weekday == 1 and e.group is not None]
    assert {e.group.name for e in side_by_side} == {'Gr-1', 'Gr-2'} and len({e.start_time for e in side_by_side}) == 1

    client = client_for('manas.ghosh@college.example', DEMO_PASSWORD)
    assert client.get('/routine/me').status_code == 200
    assert '<span class="avatar">MG</span>' in client.get('/').get_data(as_text=True)


def test_the_integrity_report_finds_the_demo_pair(app):
    """Two students who always scan seconds apart from one address: the report must flag them."""
    from attendqr.models import Semester
    from attendqr.services.integrity import build_integrity_report

    seed_demo(DEMO_PASSWORD)
    semester = Semester.query.filter_by(name='Semester 1').join(Department).filter(
        Department.name.like('%(MCA)')).one()
    with app.test_request_context():
        report = build_integrity_report(semester.department_id, semester.id)
    assert len(report.pairs) == 1 and report.pairs[0]['share'] >= 50
    assert report.failed_checks >= 1
