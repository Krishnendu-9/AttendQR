"""The guard under every route: no change to a locked semester's records reaches the database.

The routes check the lock themselves (test_audit_and_lock.py). These tests go around them,
straight through the models, as a route that forgot its check would.
"""
import html
from datetime import timedelta

import pytest

from attendqr.extensions import db
from attendqr.models import (
    Attendance,
    ClassSession,
    Condonation,
    LeaveRequest,
    Semester,
    SpotCheck,
    Student,
    Subject,
)
from attendqr.services.locks import SemesterLocked
from attendqr.services.retention import purge_expired_scan_ips
from attendqr.utils import local_now, utc_now
from conftest import add_session, post


@pytest.fixture
def locked(data):
    """Semester 1 with a session Rahul attended and a pending check, then locked."""
    class_session = add_session(data, present=[data.student_id])
    check = SpotCheck(session_id=class_session.id, student_id=data.student_id)
    leave = LeaveRequest(student_id=data.student2_id, start_date=local_now().date(), end_date=local_now().date(),
                         kind='medical', reason='Fever')
    db.session.add_all([check, leave])
    db.session.commit()
    db.session.get(Semester, data.sem1_id).attendance_locked_at = utc_now()
    db.session.commit()
    return class_session, check, leave


def refused(change):
    change()
    with pytest.raises(SemesterLocked, match='is locked'):
        db.session.commit()
    db.session.rollback()


def test_attendance_and_sessions_cannot_change(data, locked):
    class_session, check, _ = locked
    entry = class_session.attendances[0]
    refused(lambda: db.session.add(Attendance(student_id=data.student2_id, session_id=class_session.id,
                                              timestamp=utc_now())))
    refused(lambda: db.session.delete(entry))
    refused(lambda: setattr(entry, 'source', Attendance.SOURCE_MANUAL))
    refused(lambda: setattr(class_session, 'is_active', False))
    refused(lambda: db.session.delete(class_session))
    refused(lambda: db.session.add(ClassSession(subject_id=data.subject_id, expires_at=utc_now() + timedelta(hours=1))))
    refused(lambda: setattr(check, 'result', SpotCheck.ABSENT))
    db.session.expire_all()
    assert len(db.session.get(ClassSession, class_session.id).attendances) == 1


def test_leave_students_and_subjects_cannot_change(data, locked):
    _, _, leave = locked
    refused(lambda: setattr(leave, 'status', LeaveRequest.APPROVED))
    refused(lambda: db.session.delete(db.session.get(Student, data.student_id)))
    refused(lambda: setattr(db.session.get(Subject, data.subject_id), 'counts_towards_attendance', False))


def test_what_does_not_change_the_records_is_still_allowed(app, data, locked):
    class_session, _, _ = locked
    entry = class_session.attendances[0]
    entry.scan_ip = '10.0.0.9'  # the retention purge clears these
    student = db.session.get(Student, data.student_id)
    student.name = 'Rahul Kumar Das'
    db.session.add(Condonation(student_id=data.student2_id, semester_id=data.sem1_id, reason='Medical board'))
    db.session.add(LeaveRequest(student_id=data.student_id, start_date=local_now().date(),
                                end_date=local_now().date(), kind='personal', reason='Wedding'))  # pending only
    db.session.commit()
    app.config['SCAN_IP_RETENTION_DAYS'] = 0
    assert purge_expired_scan_ips() == 1
    # Moving a student out of the locked class keeps them in it for its sessions (the class history).
    student.semester_id = data.sem2_id
    db.session.commit()
    # Unlocking is a change to the semester itself.
    db.session.get(Semester, data.sem1_id).attendance_locked_at = None
    db.session.commit()


def test_a_route_that_forgets_the_check_is_stopped_with_the_usual_message(data, locked, client_for, monkeypatch):
    from attendqr.blueprints import attendance

    class_session, _, _ = locked
    monkeypatch.setattr(attendance, 'session_lock_error', lambda class_session: None)
    hod = client_for(data.hod)
    response = post(hod, f'/sessions/{class_session.id}/attendance',
                    {'student_id': data.student2_id, 'note': 'phone died'},
                    headers={'Referer': f'http://localhost/generate_qr?session_id={class_session.id}'})
    assert response.status_code == 302
    assert response.headers['Location'] == f'/generate_qr?session_id={class_session.id}'
    assert 'is locked' in html.unescape(hod.get(response.headers['Location']).get_data(as_text=True))
    db.session.expire_all()
    assert Attendance.query.filter_by(session_id=class_session.id).count() == 1
