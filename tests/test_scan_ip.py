"""Recording the IP address of each QR scan, showing it to staff, and clearing it after the retention period."""
from datetime import timedelta

import pytest

from attendqr import create_app
from attendqr.extensions import db
from attendqr.models import Attendance
from attendqr.services.qr import make_qr_token
from attendqr.services.retention import purge_expired_scan_ips
from attendqr.utils import ist_now
from conftest import add_session, login, post

SCAN_IP = '127.0.0.5'


def scan_and_mark(app, data, scan_ip=SCAN_IP, mark_ip='127.0.0.9'):
    class_session = add_session(data)
    phone = app.test_client()
    login(phone, data.student)
    phone.get(f'/scan/{make_qr_token(class_session.id)}', environ_base={'REMOTE_ADDR': scan_ip})
    result = phone.post('/mark_attendance', json={}, environ_base={'REMOTE_ADDR': mark_ip},
                        headers={'X-CSRFToken': _csrf(phone)})
    assert result.get_json()['success'] is True
    return class_session, Attendance.query.filter_by(session_id=class_session.id).one()


def _csrf(client):
    with client.session_transaction() as sess:
        return sess.setdefault('_csrf_token', 'test-csrf-token')


def test_the_address_the_scan_came_from_is_recorded(app, data):
    _, entry = scan_and_mark(app, data)
    # The scan address, not the one the confirmation was sent from.
    assert entry.scan_ip == SCAN_IP


def test_ipv4_mapped_addresses_are_stored_as_ipv4(app, data):
    app.config['SCAN_ALLOWED_NETWORKS'] = 'any'
    _, entry = scan_and_mark(app, data, scan_ip='::ffff:203.0.113.7')
    assert entry.scan_ip == '203.0.113.7'


def test_staff_see_the_address_in_the_session_view(app, data, client_for):
    class_session, _ = scan_and_mark(app, data)
    page = client_for(data.teacher).get(f'/generate_qr?session_id={class_session.id}').get_data(as_text=True)
    assert SCAN_IP in page
    assert 'deleted 30 days after the scan' in page


def test_address_is_not_shown_to_students_or_in_the_reports_api(app, data, client_for):
    class_session, _ = scan_and_mark(app, data)
    student = client_for(data.student)
    assert student.get(f'/generate_qr?session_id={class_session.id}').status_code in (302, 403)
    assert SCAN_IP not in student.get('/dashboard', follow_redirects=True).get_data(as_text=True)
    api = client_for(data.teacher).get(f'/api/session_attendance/{class_session.id}')
    assert api.status_code == 200 and SCAN_IP not in api.get_data(as_text=True)


def test_manual_entries_have_no_address(data, client_for):
    class_session = add_session(data)
    post(client_for(data.teacher), f'/sessions/{class_session.id}/attendance',
         {'student_id': data.student_id, 'note': 'Phone battery died'})
    assert Attendance.query.filter_by(session_id=class_session.id).one().scan_ip is None


def test_zero_retention_records_nothing(app, data):
    app.config['SCAN_IP_RETENTION_DAYS'] = 0
    class_session, entry = scan_and_mark(app, data)
    assert entry.scan_ip is None
    page = app.test_client()
    login(page, data.teacher)
    assert 'Scan IP addresses' not in page.get(f'/generate_qr?session_id={class_session.id}').get_data(as_text=True)


def _entry_with_ip(data, days_ago, ip):
    class_session = add_session(data, days_ago=days_ago)
    entry = Attendance(student_id=data.student_id, session_id=class_session.id,
                       timestamp=ist_now() - timedelta(days=days_ago), scan_ip=ip)
    db.session.add(entry)
    db.session.commit()
    return entry.id


def test_purge_clears_old_addresses_but_keeps_the_attendance(app, data):
    old = _entry_with_ip(data, 31, '10.0.0.1')
    recent = _entry_with_ip(data, 29, '10.0.0.2')

    assert purge_expired_scan_ips() == 1
    db.session.expire_all()
    assert db.session.get(Attendance, old).scan_ip is None
    assert db.session.get(Attendance, recent).scan_ip == '10.0.0.2'


def test_zero_retention_purges_every_address(app, data):
    _entry_with_ip(data, 0, '10.0.0.3')
    app.config['SCAN_IP_RETENTION_DAYS'] = 0
    assert purge_expired_scan_ips() == 1
    assert Attendance.query.filter(Attendance.scan_ip.is_not(None)).count() == 0


def test_the_app_purges_on_its_own(app, data):
    """No cron job needed: the first request after startup (then hourly) clears expired addresses."""
    old = _entry_with_ip(data, 31, '10.0.0.4')
    app.test_client().get('/login')
    db.session.expire_all()
    assert db.session.get(Attendance, old).scan_ip is None


@pytest.mark.parametrize('value', ['off', '-1', '2.5'])
def test_invalid_retention_stops_the_app_starting(tmp_path, value):
    with pytest.raises(ValueError, match='SCAN_IP_RETENTION_DAYS'):
        create_app({
            'TESTING': True,
            'SECRET_KEY': 'x' * 32,
            'SQLALCHEMY_DATABASE_URI': f"sqlite:///{(tmp_path / 'bad.db').as_posix()}",
            'SCAN_IP_RETENTION_DAYS': value,
            'AUTO_MIGRATE': False,
        })
