"""Deleting a QR session, which erases its attendance records."""
from attendqr.extensions import db
from attendqr.models import ActivityLog, Attendance, ClassSession
from conftest import add_session, post


def delete(client, class_session, note=None):
    data = {'note': note} if note is not None else {}
    return post(client, f'/delete_session/{class_session.id}', data)


def test_teacher_deletes_session_without_attendance(data, client_for):
    class_session = add_session(data)
    response = delete(client_for(data.teacher), class_session)
    assert response.status_code == 302
    assert db.session.get(ClassSession, class_session.id) is None
    assert ActivityLog.query.filter_by(action='qr_session_deleted').count() == 1


def test_teacher_cannot_delete_session_with_attendance(data, client_for):
    class_session = add_session(data, present=[data.student_id])
    response = delete(client_for(data.teacher), class_session, note='Wrong class')
    assert response.status_code == 302
    assert db.session.get(ClassSession, class_session.id) is not None
    assert Attendance.query.filter_by(session_id=class_session.id).count() == 1
    assert ActivityLog.query.filter_by(action='qr_session_deleted').count() == 0


def test_hod_needs_a_reason_to_delete_session_with_attendance(data, client_for):
    class_session = add_session(data, present=[data.student_id])
    client = client_for(data.hod)

    delete(client, class_session, note='  ')
    assert db.session.get(ClassSession, class_session.id) is not None

    delete(client, class_session, note='Created for the wrong class')
    db.session.expire_all()
    assert db.session.get(ClassSession, class_session.id) is None
    assert Attendance.query.count() == 0
    log = ActivityLog.query.filter_by(action='qr_session_deleted').one()
    assert '1 attendance record(s)' in log.description and 'Created for the wrong class' in log.description


def test_principal_can_delete_session_with_attendance(data, client_for):
    class_session = add_session(data, present=[data.student_id, data.student2_id])
    delete(client_for(data.principal), class_session, note='Duplicate session')
    db.session.expire_all()
    assert db.session.get(ClassSession, class_session.id) is None


def test_session_view_hides_delete_from_teacher_once_attendance_exists(data, client_for):
    empty = add_session(data)
    taken = add_session(data, present=[data.student_id])
    teacher = client_for(data.teacher)
    assert f'/delete_session/{empty.id}' in teacher.get(f'/generate_qr?session_id={empty.id}').get_data(as_text=True)
    assert f'/delete_session/{taken.id}' not in teacher.get(f'/generate_qr?session_id={taken.id}').get_data(as_text=True)

    hod_page = client_for(data.hod).get(f'/generate_qr?session_id={taken.id}').get_data(as_text=True)
    assert f'data-action="/delete_session/{taken.id}"' in hod_page and 'deleteSessionModal' in hod_page
