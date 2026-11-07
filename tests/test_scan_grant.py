"""A scan grant lives in a cookie the browser holds; copying it must not let it mark more than one attendance."""
from attendqr.extensions import db
from attendqr.models import Attendance, Student
from attendqr.services.qr import make_qr_token
from conftest import add_session, login, post_json

OFF_CAMPUS = {'REMOTE_ADDR': '8.8.8.8'}


def open_scan_without_logging_in(app, class_session):
    phone = app.test_client()
    assert phone.get(f'/scan/{make_qr_token(class_session.id)}').status_code == 302
    return phone


def copy_cookies(source, target, *names):
    for name in names:
        target.set_cookie(name, source.get_cookie(name).value)


def mark_with_copy(app, phone, email):
    browser = app.test_client()
    copy_cookies(phone, browser, 'session', 'device_id')
    login(browser, email)
    return post_json(browser, '/mark_attendance')


def test_a_copied_grant_marks_only_one_attendance(app, data):
    class_session = add_session(data)
    phone = open_scan_without_logging_in(app, class_session)

    assert mark_with_copy(app, phone, data.student).get_json()['success'] is True
    # The device is now the first student's, so the copy fails that check already.
    assert mark_with_copy(app, phone, data.student2).status_code == 403
    assert Attendance.query.filter_by(session_id=class_session.id).count() == 1


def test_a_used_grant_stays_used_even_when_the_device_is_free_again(app, data):
    class_session = add_session(data)
    phone = open_scan_without_logging_in(app, class_session)
    assert mark_with_copy(app, phone, data.student).get_json()['success'] is True

    first = db.session.get(Student, data.student_id)
    first.device_id = None  # e.g. staff reset the device lock
    db.session.commit()

    second = mark_with_copy(app, phone, data.student2)
    assert second.status_code == 409
    assert 'already been used' in second.get_json()['message']
    assert Attendance.query.filter_by(session_id=class_session.id).count() == 1


def test_a_grant_cannot_be_confirmed_off_campus(app, data):
    class_session = add_session(data)
    phone = open_scan_without_logging_in(app, class_session)
    remote = app.test_client()
    remote.environ_base.update(OFF_CAMPUS)
    copy_cookies(phone, remote, 'session', 'device_id')
    login(remote, data.student)

    page = remote.get('/attend')
    assert page.status_code == 403 and 'Campus Network Required' in page.get_data(as_text=True)
    response = post_json(remote, '/mark_attendance')
    assert response.status_code == 403
    assert 'campus network' in response.get_json()['message']
    assert Attendance.query.count() == 0


def test_a_grant_only_works_on_the_phone_that_scanned(app, data):
    class_session = add_session(data)
    phone = open_scan_without_logging_in(app, class_session)
    other_browser = app.test_client()
    copy_cookies(phone, other_browser, 'session')  # the grant, but not the device cookie
    login(other_browser, data.student)

    page = other_browser.get('/attend')
    assert page.status_code == 403 and 'Scan Again on This Phone' in page.get_data(as_text=True)
    assert post_json(other_browser, '/mark_attendance').status_code == 403
    assert Attendance.query.count() == 0


def test_attendance_records_the_grant_it_used(app, data):
    class_session = add_session(data)
    phone = open_scan_without_logging_in(app, class_session)
    login(phone, data.student)
    assert post_json(phone, '/mark_attendance').get_json()['success'] is True
    entry = Attendance.query.filter_by(session_id=class_session.id).one()
    assert entry.scan_grant and len(entry.scan_grant) == 32


def test_scans_that_register_a_device_are_flagged_for_staff(app, data, client_for):
    class_session = add_session(data)
    phone = open_scan_without_logging_in(app, class_session)
    login(phone, data.student)
    assert post_json(phone, '/mark_attendance').get_json()['success'] is True
    assert Attendance.query.filter_by(session_id=class_session.id).one().new_device is True

    page = client_for(data.teacher).get(f'/generate_qr?session_id={class_session.id}').get_data(as_text=True)
    assert 'New device' in page and '1 scan in this session registered a new device' in page

    # The same phone in the next session is the registered device, not a new one.
    second = add_session(data, subject_id=data.subject2_id)
    assert phone.get(f'/scan/{make_qr_token(second.id)}').status_code == 302
    assert post_json(phone, '/mark_attendance').get_json()['success'] is True
    assert Attendance.query.filter_by(session_id=second.id).one().new_device is False
