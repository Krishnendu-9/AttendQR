"""QR attendance, student side: the scan link in the QR, the confirmation page, and marking."""

from flask import (
    current_app,
    jsonify,
    make_response,
    redirect,
    render_template,
    url_for,
)
from flask_login import current_user, login_required
from itsdangerous import BadSignature, SignatureExpired
from sqlalchemy.exc import IntegrityError

from ...access import (
    session_display_name,
    user_role,
)
from ...extensions import db
from ...models import Attendance, ClassSession
from ...observability import count_scan
from ...services.groups import describe_group, may_attend
from ...services.locks import session_lock_error
from ...services.network import client_ip, scan_network_error
from ...services.qr import (
    DEVICE_TAKEN_MESSAGE,
    clear_scan_grant,
    current_scan_grant,
    device_binding_error,
    ensure_device_cookie,
    grant_scan,
    new_device_id,
    read_device_cookie,
    read_qr_token,
    scan_grant_ip,
    set_device_cookie,
)
from ...services.retention import scan_ip_recording_enabled
from ...utils import format_12h, utc_now
from . import bp

# ── Student side ─────────────────────────────────────────────

def _session_info(class_session):
    return {
        'subject': session_display_name(class_session),
        'department': class_session.subject.department.display_name,
        'semester': class_session.subject.semester.name,
        'created_at': format_12h(class_session.created_at),
        'expires_at': format_12h(class_session.expires_at),
    }


def _status_page(title, message, status=200, session_info=None, student=None):
    response = make_response(render_template(
        'scan_qr.html',
        status_title=title,
        status_message=message,
        session_info=session_info,
        can_mark_attendance=False,
        student=student,
    ), status)
    return ensure_device_cookie(response)


def _ended_status(class_session):
    if utc_now() > class_session.expires_at:
        return 'QR Expired', 'This QR session has ended. Please ask your teacher for a new QR code.'
    return 'QR Deactivated', 'This QR session has been deactivated by the teacher.'


NETWORK_REQUIRED_TITLE = 'Campus Network Required'
NETWORK_REQUIRED_MESSAGE = (
    'Attendance can only be marked from the campus network. Connect to the campus Wi-Fi and scan the code again.'
)
OTHER_BROWSER_TITLE = 'Scan Again on This Phone'
OTHER_BROWSER_MESSAGE = (
    'This scan was opened in a different browser. Scan the QR code again with the phone you are marking attendance on.'
)
GRANT_USED_TITLE = 'Scan Already Used'
GRANT_USED_MESSAGE = 'This scan has already been used to mark attendance. Scan the QR code shown in class again.'
# Their login is turned off when they leave; this holds even if one is somehow signed in.
LEFT_COLLEGE_TITLE = 'Not a Current Student'
LEFT_COLLEGE_MESSAGE = 'This account belongs to a student who has left the college, so it cannot mark attendance.'


def _network_refusal(stage):
    """None if this request comes from an allowed network, else (title, message, status) after logging it."""
    network_error = scan_network_error()
    if not network_error:
        return None
    current_app.logger.warning("Refused QR %s: %s", stage, network_error)
    return NETWORK_REQUIRED_TITLE, NETWORK_REQUIRED_MESSAGE, 403


def _grant_refusal(grant):
    """Why this scan grant cannot be used from this request, as (title, message, status), or None.

    The grant lives in a cookie, so it could have been copied to another browser
    off campus; check the network again, the phone it was issued to, and that it
    has not already produced an attendance entry.
    """
    refusal = _network_refusal('attendance confirmation')
    if refusal:
        return refusal
    if read_device_cookie() != grant['device']:
        return OTHER_BROWSER_TITLE, OTHER_BROWSER_MESSAGE, 403
    if db.session.query(Attendance.query.filter_by(scan_grant=grant['id']).exists()).scalar():
        return GRANT_USED_TITLE, GRANT_USED_MESSAGE, 409
    return None


@bp.route('/scan/<token>')
def scan(token):
    """Entry point encoded in the QR. Validates the rotating token once, then hands over to /attend."""
    # Checked before the token, so off-network clients learn nothing about it.
    refusal = _network_refusal('scan')
    if refusal:
        count_scan('network_refused')
        return _status_page(*refusal)

    try:
        class_session_id = read_qr_token(token)
    except SignatureExpired:
        count_scan('token_expired')
        return _status_page('QR Expired', 'This QR code has expired. Scan the code currently shown on the screen.', 410)
    except BadSignature:
        count_scan('token_invalid')
        return _status_page('Invalid QR', 'This QR code is not valid. Scan the code shown by your teacher.', 404)

    class_session = db.session.get(ClassSession, class_session_id)
    if not class_session or not class_session.subject:
        return _status_page('Invalid QR', 'This QR session no longer exists.', 404)
    if session_lock_error(class_session):
        count_scan('session_locked')
        return _status_page('Attendance Locked', session_lock_error(class_session), 403, _session_info(class_session))
    if not class_session.is_live():
        count_scan('session_ended')
        title, message = _ended_status(class_session)
        return _status_page(title, message, 410, _session_info(class_session))

    existing_device = read_device_cookie()
    device_id = existing_device or new_device_id()
    grant_scan(class_session.id, device_id, client_ip() if scan_ip_recording_enabled() else None)
    count_scan('granted')
    response = redirect(url_for('attendance.attend'))
    if device_id != existing_device:
        set_device_cookie(response, device_id)
    return response


@bp.route('/attend')
@login_required
def attend():
    grant = current_scan_grant()
    if not grant:
        return _status_page('Scan Required', 'Scan the QR code shown in class to mark your attendance.')

    class_session = db.session.get(ClassSession, grant['session_id'])
    if not class_session or not class_session.subject:
        clear_scan_grant()
        return _status_page('Invalid QR', 'This QR session no longer exists.', 404)

    info = _session_info(class_session)
    if user_role() != 'student' or not current_user.student:
        return _status_page('Student Login Required', 'Attendance marking is only available for student accounts.', 403, info)
    if not class_session.is_live():
        title, message = _ended_status(class_session)
        return _status_page(title, message, 410, info)
    # The semester may have been locked since the scan; say so now, not after the student confirms.
    lock_error = session_lock_error(class_session)
    if lock_error:
        clear_scan_grant()
        return _status_page('Attendance Locked', lock_error, 403, info)

    student = current_user.student
    if student.left_at is not None:
        clear_scan_grant()
        return _status_page(LEFT_COLLEGE_TITLE, LEFT_COLLEGE_MESSAGE, 403, info)
    subject = class_session.subject
    if student.department_id != subject.department_id or student.semester_id != subject.semester_id:
        return _status_page(
            'Wrong Class',
            f'This QR code is only for {subject.department.display_name} - {subject.semester.name}.',
            403, info, student=student,
        )
    if not may_attend(student, class_session):
        return _status_page('Wrong Group', f'This QR code is only for {describe_group(class_session)}.',
                            403, info, student=student)

    device_error = device_binding_error(student, read_device_cookie())
    if device_error:
        return _status_page('Device Verification Failed', device_error, 403, info, student=student)

    if Attendance.query.filter_by(student_id=student.id, session_id=class_session.id).first():
        return _status_page('Attendance Already Marked', 'Your attendance has already been marked for this QR session.',
                            session_info=info, student=student)

    refusal = _grant_refusal(grant)
    if refusal:
        title, message, status = refusal
        return _status_page(title, message, status, info, student=student)

    response = make_response(render_template(
        'scan_qr.html',
        status_title='Mark Attendance',
        status_message='Confirm your student account to register attendance for this session.',
        session_info=info,
        student=student,
        can_mark_attendance=True,
    ))
    return ensure_device_cookie(response)


def _json_error(message, status=400):
    return jsonify({'success': False, 'message': message}), status


@bp.route('/mark_attendance', methods=['POST'])
def mark_attendance():
    if not current_user.is_authenticated:
        return _json_error('Please log in with your student account before marking attendance.', 401)
    if user_role() != 'student' or not current_user.student:
        return _json_error('Attendance marking is only available for student accounts.', 403)

    grant = current_scan_grant()
    if not grant:
        return _json_error('Your scan has expired. Scan the QR code shown in class again.', 410)

    class_session = db.session.get(ClassSession, grant['session_id'])
    if not class_session or not class_session.subject or not class_session.is_live():
        clear_scan_grant()
        return _json_error('This QR session has ended.', 410)
    if session_lock_error(class_session):
        clear_scan_grant()
        return _json_error(session_lock_error(class_session), 403)

    student = current_user.student
    if student.left_at is not None:
        clear_scan_grant()
        count_scan('student_left')
        return _json_error(LEFT_COLLEGE_MESSAGE, 403)
    subject = class_session.subject
    if student.department_id != subject.department_id or student.semester_id != subject.semester_id:
        count_scan('wrong_class')
        return _json_error(f'This QR code is only for {subject.department.display_name} - {subject.semester.name}.', 403)
    if not may_attend(student, class_session):
        count_scan('wrong_group')
        return _json_error(f'This QR code is only for {describe_group(class_session)}.', 403)

    device_id = read_device_cookie()
    device_error = device_binding_error(student, device_id)
    if device_error:
        count_scan('device_rejected')
        return _json_error(device_error, 403)

    if Attendance.query.filter_by(student_id=student.id, session_id=class_session.id).first():
        clear_scan_grant()
        return _json_error('Attendance already marked')

    # Also guarantees the device cookie is present: it must match the grant's.
    refusal = _grant_refusal(grant)
    if refusal:
        _, message, status = refusal
        count_scan('grant_refused')
        return _json_error(message, status)

    registers_device = not student.device_id
    if registers_device:
        student.device_id = device_id

    db.session.add(Attendance(
        student_id=student.id, session_id=class_session.id, timestamp=utc_now(),
        scan_ip=scan_grant_ip(grant) if scan_ip_recording_enabled() else None,
        scan_grant=grant['id'],
        new_device=registers_device,
    ))
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        if Attendance.query.filter_by(student_id=student.id, session_id=class_session.id).first():
            clear_scan_grant()
            return _json_error('Attendance already marked')
        if Attendance.query.filter_by(scan_grant=grant['id']).first():
            # The same grant, copied to another browser, was used at the same moment.
            count_scan('grant_refused')
            return _json_error(GRANT_USED_MESSAGE, 409)
        # Another student registered this device at the same moment.
        return _json_error(DEVICE_TAKEN_MESSAGE, 403)

    clear_scan_grant()
    count_scan('marked')
    return jsonify({'success': True, 'message': f'Attendance marked for {student.name}'})
