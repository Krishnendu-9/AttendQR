"""QR sessions and the student scan → attend → mark flow."""
import time
from datetime import timedelta

from itsdangerous import TimestampSigner

from attendqr.extensions import db
from attendqr.models import Admin, Attendance, ClassSession, Student, Teacher
from attendqr.services.qr import make_qr_token
from attendqr.utils import ist_now
from conftest import PASSWORD, login, post, post_json


def create_session(client, data, **overrides):
    form = {
        'class_mode': 'regular', 'department_id': data.mca_id, 'semester_id': data.sem1_id,
        'subject_id': data.subject_id, 'duration': 30,
    }
    form.update(overrides)
    response = post(client, '/generate_qr', form)
    assert response.status_code == 302, response.get_data(as_text=True)
    return ClassSession.query.order_by(ClassSession.id.desc()).first()


def scan_and_mark(client, class_session):
    scan = client.get(f'/scan/{make_qr_token(class_session.id)}')
    assert scan.status_code == 302 and scan.headers['Location'].endswith('/attend')
    return post_json(client, '/mark_attendance')


def test_full_scan_flow_marks_attendance_once(data, client_for):
    class_session = create_session(client_for(data.teacher), data)
    student = client_for(data.student)

    attend = student.get(f'/scan/{make_qr_token(class_session.id)}', follow_redirects=True)
    assert 'Mark My Attendance' in attend.get_data(as_text=True)

    result = post_json(student, '/mark_attendance').get_json()
    assert result == {'success': True, 'message': 'Attendance marked for Rahul Das'}
    assert Attendance.query.filter_by(session_id=class_session.id).count() == 1
    assert db.session.get(Student, data.student_id).device_id

    again = scan_and_mark(student, class_session).get_json()
    assert again['success'] is False and 'already' in again['message'].lower()


def test_scan_survives_login_and_first_password_change(app, data, client_for):
    """A student who is not logged in still has time to log in and replace the roll-number password."""
    class_session = create_session(client_for(data.teacher), data)
    account = Admin.query.filter_by(username=data.student).one()
    account.must_change_password = True
    db.session.commit()

    phone = app.test_client()
    scan = phone.get(f'/scan/{make_qr_token(class_session.id)}')
    attend = phone.get(scan.headers['Location'])
    assert '/login?next=%2Fattend' in attend.headers['Location']

    login_page = phone.get(attend.headers['Location']).get_data(as_text=True)
    assert 'name="next" value="/attend"' in login_page
    after_login = post(phone, '/login', {'username': data.student, 'password': PASSWORD, 'next': '/attend'})
    assert after_login.headers['Location'].endswith('/attend')
    forced = phone.get('/attend')
    assert forced.headers['Location'].endswith('/account?next=/attend')

    changed = post(phone, '/account', {
        'current_password': PASSWORD, 'new_password': 'MyOwnPass1', 'confirm_password': 'MyOwnPass1', 'next': '/attend',
    })
    assert changed.headers['Location'].endswith('/attend')
    assert post_json(phone, '/mark_attendance').get_json()['success'] is True


def test_one_phone_cannot_mark_for_several_students(app, data, client_for):
    class_session = create_session(client_for(data.teacher), data)
    phone = app.test_client()

    login(phone, data.student)
    assert scan_and_mark(phone, class_session).get_json()['success'] is True
    post(phone, '/logout')

    login(phone, data.student2)  # same browser, same device cookie
    page = phone.get(f'/scan/{make_qr_token(class_session.id)}', follow_redirects=True)
    assert 'Device Verification Failed' in page.get_data(as_text=True)
    response = post_json(phone, '/mark_attendance')
    assert response.status_code == 403
    assert 'already registered to another student' in response.get_json()['message']
    assert Attendance.query.filter_by(session_id=class_session.id).count() == 1


def test_registered_student_cannot_use_another_device(app, data, client_for):
    class_session = create_session(client_for(data.teacher), data)
    assert scan_and_mark(client_for(data.student), class_session).get_json()['success'] is True

    second_session = create_session(client_for(data.teacher), data, subject_id=data.subject2_id)
    other_phone = client_for(data.student)
    response = scan_and_mark(other_phone, second_session)
    assert response.status_code == 403
    assert 'locked to a different device' in response.get_json()['message']


def test_teacher_can_reset_device_lock(data, client_for):
    student = db.session.get(Student, data.student_id)
    student.device_id = '11111111-2222-3333-4444-555555555555'
    db.session.commit()
    post(client_for(data.teacher), f'/students/reset-device/{data.student_id}')
    assert db.session.get(Student, data.student_id).device_id is None


def test_expired_qr_token_is_rejected(monkeypatch, data, client_for):
    class_session = create_session(client_for(data.teacher), data)
    monkeypatch.setattr(TimestampSigner, 'get_timestamp', lambda self: int(time.time()) - 120)
    stale = make_qr_token(class_session.id)
    monkeypatch.undo()

    response = client_for(data.student).get(f'/scan/{stale}')
    assert response.status_code == 410
    assert 'QR Expired' in response.get_data(as_text=True)


def test_permanent_session_token_no_longer_works(data, client_for):
    class_session = create_session(client_for(data.teacher), data)
    student = client_for(data.student)
    assert student.get(f'/scan/{class_session.token}').status_code == 404
    assert post_json(student, '/mark_attendance', {'token': class_session.token}).status_code == 410


def test_marking_requires_a_fresh_scan(data, client_for):
    create_session(client_for(data.teacher), data)
    response = post_json(client_for(data.student), '/mark_attendance')
    assert response.status_code == 410


def test_wrong_class_is_refused(data, client_for):
    class_session = create_session(client_for(data.teacher), data)
    late = client_for(data.sem2_student)
    page = late.get(f'/scan/{make_qr_token(class_session.id)}', follow_redirects=True)
    assert 'Wrong Class' in page.get_data(as_text=True)
    assert post_json(late, '/mark_attendance').status_code == 403


def test_deactivated_session_stops_accepting_scans(data, client_for):
    teacher = client_for(data.teacher)
    class_session = create_session(teacher, data)
    token = make_qr_token(class_session.id)
    post(teacher, f'/deactivate_session/{class_session.id}')
    response = client_for(data.student).get(f'/scan/{token}')
    assert response.status_code == 410
    assert 'QR Deactivated' in response.get_data(as_text=True)


def test_session_qr_endpoint(data, client_for):
    teacher = client_for(data.teacher)
    class_session = create_session(teacher, data)

    body = teacher.get(f'/api/session/{class_session.id}/qr').get_json()
    assert body['image'].startswith('data:image/png;base64,')
    assert body['scan_url'].startswith('http://attendqr.test/scan/')

    assert client_for(data.student).get(f'/api/session/{class_session.id}/qr').status_code == 403
    assert client_for(data.other_teacher).get(f'/api/session/{class_session.id}/qr').status_code == 403

    class_session.expires_at = ist_now() - timedelta(minutes=1)
    db.session.commit()
    assert teacher.get(f'/api/session/{class_session.id}/qr').status_code == 410


def test_arbitrary_qr_image_endpoint_is_gone(data, client_for):
    assert client_for(data.teacher).get('/qr-code-image?text=hello').status_code == 404


def test_generate_qr_validation_shows_message_instead_of_bare_400(data, client_for):
    teacher = client_for(data.teacher)
    response = post(teacher, '/generate_qr', {
        'class_mode': 'regular', 'department_id': data.mca_id, 'semester_id': data.sem1_id,
        'subject_id': data.subject_id, 'duration': 999,
    }, follow_redirects=True)
    assert response.status_code == 200
    assert 'Duration must be between 1 and 180 minutes.' in response.get_data(as_text=True)
    assert ClassSession.query.count() == 0


def test_no_qr_png_files_are_written(app, data, client_for):
    import os
    qr_dir = os.path.join(app.static_folder, 'qrcodes')
    before = set(os.listdir(qr_dir)) if os.path.isdir(qr_dir) else set()
    create_session(client_for(data.teacher), data)
    after = set(os.listdir(qr_dir)) if os.path.isdir(qr_dir) else set()
    assert after == before


def test_teacher_names_are_not_injected_as_html(data, client_for):
    teacher = db.session.get(Teacher, data.teacher_id)
    teacher.name = '<img src=x onerror=alert(1)>'
    db.session.commit()
    principal = client_for(data.principal)
    subjects = principal.get(f'/api/subjects/{data.sem1_id}').get_json()
    assert subjects[0]['teacher_name'] == '<img src=x onerror=alert(1)>'  # data only; rendered with new Option()
    html = principal.get('/generate_qr').get_data(as_text=True)
    assert 'innerHTML = `<option' not in html
    assert '<img src=x' not in principal.get('/teachers').get_data(as_text=True)


def test_special_class_without_teacher(data, client_for):
    class_session = create_session(client_for(data.hod), data, class_mode='special',
                                   special_title='Aptitude <Workshop> & Q&A', subject_id='')
    assert class_session.special_title == 'Aptitude <Workshop> & Q&A'
    assert class_session.teacher_id is None
