"""Spot checks: random students marked present, confirmed in the room or removed."""
import html
from datetime import timedelta

from attendqr.extensions import db
from attendqr.models import ActivityLog, Attendance, SpotCheck
from conftest import add_session, make_student, post


def page(response):
    return html.unescape(response.get_data(as_text=True))


def class_of(data, count):
    """`count` extra students in the fixture's class, all marked present by scan in a new session."""
    from attendqr.models import Department, Semester

    department, semester = db.session.get(Department, data.mca_id), db.session.get(Semester, data.sem1_id)
    ids = [data.student_id, data.student2_id] + [
        make_student(f'Extra {n}', f'MCA9{n:02d}', f'extra{n}@example.com', department, semester).id
        for n in range(count)
    ]
    db.session.commit()
    return add_session(data, present=ids), ids


def test_starting_picks_three_random_scanned_students(data, client_for):
    class_session, ids = class_of(data, 4)
    response = post(client_for(data.teacher), f'/sessions/{class_session.id}/spot-checks', follow_redirects=True)

    checks = SpotCheck.query.filter_by(session_id=class_session.id).all()
    assert len(checks) == 3 and {c.result for c in checks} == {'pending'}
    assert {c.student_id for c in checks} <= set(ids)
    assert 'Spot check' in page(response)
    assert ActivityLog.query.filter_by(action='spot_check_started').count() == 1


def test_more_checks_never_repeat_a_student_and_skip_manual_entries(data, client_for):
    class_session, _ = class_of(data, 1)  # 3 scanned students
    manual_only = add_session(data)
    db.session.add(Attendance(student_id=data.student_id, session_id=manual_only.id, source='manual', note='n'))
    db.session.commit()
    client = client_for(data.teacher)

    post(client, f'/sessions/{class_session.id}/spot-checks')
    response = post(client, f'/sessions/{class_session.id}/spot-checks', follow_redirects=True)
    assert 'Nobody marked present by QR scan is left to check' in page(response)
    assert SpotCheck.query.filter_by(session_id=class_session.id).count() == 3

    post(client, f'/sessions/{manual_only.id}/spot-checks')
    assert SpotCheck.query.filter_by(session_id=manual_only.id).count() == 0


def test_a_student_in_the_room_keeps_their_attendance(data, client_for):
    class_session, _ = class_of(data, 0)
    client = client_for(data.teacher)
    post(client, f'/sessions/{class_session.id}/spot-checks')
    check = SpotCheck.query.first()

    post(client, f'/sessions/{class_session.id}/spot-checks/{check.id}', {'result': 'present'})

    db.session.expire_all()
    assert check.result == 'present' and check.checked_by.username == data.teacher and check.resolved_at
    assert Attendance.query.filter_by(session_id=class_session.id, student_id=check.student_id).count() == 1


def test_a_student_not_in_the_room_loses_the_attendance_and_it_is_logged(data, client_for):
    class_session, _ = class_of(data, 0)
    client = client_for(data.teacher)
    post(client, f'/sessions/{class_session.id}/spot-checks')
    check = SpotCheck.query.first()

    response = post(client, f'/sessions/{class_session.id}/spot-checks/{check.id}', {'result': 'absent'},
                    follow_redirects=True)

    db.session.expire_all()
    assert check.result == 'absent'
    assert Attendance.query.filter_by(session_id=class_session.id, student_id=check.student_id).count() == 0
    assert ActivityLog.query.filter_by(action='spot_check_failed').count() == 1
    assert 'Not in the room at a spot check' in ActivityLog.query.filter_by(action='attendance_removed').one().description
    assert 'attendance removed' in page(response)
    # Answering twice changes nothing.
    again = post(client, f'/sessions/{class_session.id}/spot-checks/{check.id}', {'result': 'present'}, follow_redirects=True)
    assert 'already been answered' in page(again)


def test_spot_checks_close_two_hours_after_the_session(data, client_for):
    class_session, _ = class_of(data, 0)
    class_session.expires_at -= timedelta(hours=3)
    db.session.commit()
    response = post(client_for(data.teacher), f'/sessions/{class_session.id}/spot-checks', follow_redirects=True)
    assert 'up to 2 hours after it' in page(response)
    assert SpotCheck.query.count() == 0


def test_only_staff_who_can_open_the_session_can_check(data, client_for):
    class_session, _ = class_of(data, 0)
    assert post(client_for(data.other_teacher), f'/sessions/{class_session.id}/spot-checks').status_code == 403
    assert post(client_for(data.hod), f'/sessions/{class_session.id}/spot-checks').status_code == 302
    check = SpotCheck.query.first()
    assert post(client_for(data.student), f'/sessions/{class_session.id}/spot-checks/{check.id}',
                {'result': 'absent'}).status_code == 403


def test_deleting_a_session_removes_its_checks(data, client_for):
    class_session, _ = class_of(data, 0)
    post(client_for(data.teacher), f'/sessions/{class_session.id}/spot-checks')
    post(client_for(data.hod), f'/delete_session/{class_session.id}', {'note': 'Created by mistake'})
    assert SpotCheck.query.count() == 0
