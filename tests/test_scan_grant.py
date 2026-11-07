"""A scan grant lives in a cookie the browser holds, so it can be copied; a copy must not work off campus or in another browser."""
from attendqr.models import Attendance
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
