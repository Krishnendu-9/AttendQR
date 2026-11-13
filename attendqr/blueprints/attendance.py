import uuid
from datetime import timedelta

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required
from itsdangerous import BadSignature, SignatureExpired
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from ..access import (
    SPECIAL_CLASS_SUBJECT_NAME,
    can_access_department,
    can_access_session,
    can_access_subject,
    can_access_teacher,
    current_teacher_id,
    log_activity,
    ordered_departments_query,
    role_required,
    session_display_name,
    subject_display_name,
    user_role,
    visible_sessions_query,
)
from ..extensions import db
from ..models import Attendance, ClassSession, Semester, Student, Subject, Teacher
from ..observability import count_scan
from ..security import is_safe_redirect_target
from ..services.enrollment import class_roster, in_class_at, was_in_class
from ..services.network import client_ip, scan_network_error
from ..services.qr import (
    DEVICE_TAKEN_MESSAGE,
    build_scan_url,
    clear_scan_grant,
    current_scan_grant,
    device_binding_error,
    ensure_device_cookie,
    grant_scan,
    make_qr_token,
    new_device_id,
    qr_data_uri,
    read_device_cookie,
    read_qr_token,
    scan_grant_ip,
    set_device_cookie,
)
from ..services.reports import attendance_counts
from ..services.retention import scan_ip_recording_enabled
from ..utils import format_12h, normalize_text, parse_int_value, utc_now

bp = Blueprint('attendance', __name__)

# Deleting a session that has attendance erases those records, so it is kept from teachers.
SESSION_DELETE_WITH_ATTENDANCE_ROLES = ('principal', 'hod')


def get_or_create_special_subject(department_id, semester_id):
    subject = Subject.query.filter_by(
        department_id=department_id,
        semester_id=semester_id,
        name=SPECIAL_CLASS_SUBJECT_NAME,
    ).first()
    if subject:
        return subject
    subject = Subject(name=SPECIAL_CLASS_SUBJECT_NAME, paper_code=None,
                      department_id=department_id, semester_id=semester_id)
    db.session.add(subject)
    db.session.flush()
    return subject


def _redirect_after_session_action():
    target = request.form.get('next')
    if is_safe_redirect_target(target):
        return redirect(target)
    return redirect(url_for('attendance.generate_qr'))


def _form_error(message):
    flash(message, 'danger')
    return redirect(url_for('attendance.generate_qr'))


# ── Teacher side ─────────────────────────────────────────────

@bp.route('/generate_qr', methods=['GET', 'POST'])
@login_required
@role_required('principal', 'hod', 'teacher')
def generate_qr():
    if request.method == 'POST':
        return _create_session()

    view_session_id = request.args.get('session_id', type=int)
    if view_session_id:
        return _render_session(view_session_id)

    recent_sessions = (
        visible_sessions_query()
        .options(
            joinedload(ClassSession.subject).joinedload(Subject.department),
            joinedload(ClassSession.subject).joinedload(Subject.semester),
            joinedload(ClassSession.teacher),
        )
        .order_by(ClassSession.created_at.desc())
        .limit(10)
        .all()
    )
    return render_template(
        'generate_qr.html',
        view_session=None,
        departments=ordered_departments_query().all(),
        recent_sessions=recent_sessions,
        session_counts=attendance_counts([s.id for s in recent_sessions]),
        current_teacher=current_user.teacher if user_role() == 'teacher' else None,
    )


def _render_session(class_session_id):
    class_session = db.get_or_404(ClassSession, class_session_id)
    if not can_access_session(class_session):
        abort(403)

    live = class_session.is_live()
    scan_url = qr_image = None
    if live:
        scan_url = build_scan_url(make_qr_token(class_session.id))
        qr_image = qr_data_uri(scan_url)

    attendance_records = (
        Attendance.query.options(joinedload(Attendance.student), joinedload(Attendance.marked_by))
        .filter_by(session_id=class_session.id)
        .order_by(Attendance.timestamp.desc())
        .all()
    )
    return render_template(
        'generate_qr.html',
        view_session=class_session,
        live=live,
        qr_image=qr_image,
        scan_url=scan_url,
        duration=int((class_session.expires_at - class_session.created_at).total_seconds() // 60),
        attendance_records=attendance_records,
        absent_students=_absent_students(class_session),
        refresh_seconds=current_app.config['QR_REFRESH_SECONDS'],
    )


def _absent_students(class_session):
    """Students who were in the session's class when it was held and have no attendance entry for it.

    Includes students moved to another class since, so an old session can still be corrected.
    """
    subject = class_session.subject
    marked = {
        student_id for (student_id,) in
        db.session.query(Attendance.student_id).filter(Attendance.session_id == class_session.id)
    }
    return [
        student for student, left_at in class_roster(subject.department_id, subject.semester_id)
        if student.id not in marked and in_class_at(left_at, class_session.created_at)
    ]


def _create_session():
    cfg = current_app.config
    class_mode = request.form.get('class_mode', 'regular')
    department_id = parse_int_value(request.form.get('department_id'))
    semester_id = parse_int_value(request.form.get('semester_id'))
    subject_id = parse_int_value(request.form.get('subject_id'))
    teacher_id = parse_int_value(request.form.get('teacher_id'))
    special_title = normalize_text(request.form.get('special_title'))
    duration = parse_int_value(request.form.get('duration'))

    if duration is None or not cfg['MIN_QR_DURATION'] <= duration <= cfg['MAX_QR_DURATION']:
        return _form_error(f"Duration must be between {cfg['MIN_QR_DURATION']} and {cfg['MAX_QR_DURATION']} minutes.")
    if not department_id or not semester_id:
        return _form_error("Department and semester are required.")
    if not can_access_department(department_id):
        abort(403)
    semester = db.session.get(Semester, semester_id)
    if not semester or semester.department_id != department_id:
        return _form_error("The selected semester does not belong to the selected department.")

    if class_mode == 'special':
        if not special_title:
            return _form_error("Enter the special class details.")
        if len(special_title) > 200:
            return _form_error("Special class details must be at most 200 characters.")
        subject = get_or_create_special_subject(department_id, semester_id)
    else:
        if not subject_id:
            return _form_error("Select a subject.")
        subject = db.session.get(Subject, subject_id)
        if not subject or subject.department_id != department_id or subject.semester_id != semester_id:
            return _form_error("The selected subject does not belong to the selected semester.")
        if not can_access_subject(subject):
            abort(403)
        special_title = None
        if user_role() != 'teacher' and subject.teacher_id and not teacher_id:
            teacher_id = subject.teacher_id

    if user_role() == 'teacher':
        teacher_id = current_teacher_id()
    elif teacher_id:
        teacher = db.session.get(Teacher, teacher_id)
        if not teacher or teacher.department_id != subject.department_id:
            return _form_error("The selected teacher does not belong to this department.")
        if not can_access_teacher(teacher):
            abort(403)
    elif class_mode != 'special':
        return _form_error("Select a teacher for regular classes.")

    now = utc_now()
    class_session = ClassSession(
        subject_id=subject.id,
        special_title=special_title,
        # Internal identifier only; scanning uses rotating signed tokens.
        token=str(uuid.uuid4()),
        teacher_id=teacher_id,
        created_at=now,
        expires_at=now + timedelta(minutes=duration),
    )
    db.session.add(class_session)
    db.session.flush()
    log_activity(
        'qr_session_created',
        f"Generated QR session for '{special_title or subject_display_name(subject)}'.",
        'class_session', class_session.id, department_id,
    )
    db.session.commit()
    return redirect(url_for('attendance.generate_qr', session_id=class_session.id))


def _session_for_action(id):
    class_session = db.get_or_404(ClassSession, id)
    if not can_access_session(class_session):
        abort(403)
    return class_session


@bp.route('/deactivate_session/<int:id>', methods=['POST'])
@login_required
@role_required('principal', 'hod', 'teacher')
def deactivate_session(id):
    class_session = _session_for_action(id)
    if class_session.is_live():
        class_session.is_active = False
        log_activity(
            'qr_session_deactivated',
            f"Deactivated QR session for '{session_display_name(class_session)}'.",
            'class_session', class_session.id, class_session.subject.department_id,
        )
        db.session.commit()
        flash("Session deactivated successfully.", "success")
    return _redirect_after_session_action()


@bp.route('/reactivate_session/<int:id>', methods=['POST'])
@login_required
@role_required('principal', 'hod', 'teacher')
def reactivate_session(id):
    class_session = _session_for_action(id)
    if not class_session.is_active and class_session.expires_at > utc_now():
        class_session.is_active = True
        log_activity(
            'qr_session_reactivated',
            f"Reactivated QR session for '{session_display_name(class_session)}'.",
            'class_session', class_session.id, class_session.subject.department_id,
        )
        db.session.commit()
        flash("Session reactivated successfully.", "success")
    return _redirect_after_session_action()


@bp.route('/delete_session/<int:id>', methods=['POST'])
@login_required
@role_required('principal', 'hod', 'teacher')
def delete_session(id):
    """Delete a session. One that has attendance takes its records with it, so only
    the HOD or Principal may do that, with a reason; a teacher can deactivate it or
    remove single entries instead."""
    class_session = _session_for_action(id)
    present = Attendance.query.filter_by(session_id=class_session.id).count()
    description = f"Deleted QR session for '{session_display_name(class_session)}'"

    if present:
        if user_role() not in SESSION_DELETE_WITH_ATTENDANCE_ROLES:
            flash(
                f"This session has {present} attendance record(s), so only the HOD or Principal can delete it. "
                "Deactivate it, or remove single entries from the session view, instead.",
                'warning',
            )
            return _redirect_after_session_action()
        note, error = _correction_reason()
        if error:
            flash(error, 'danger')
            return _redirect_after_session_action()
        description += f" with {present} attendance record(s). Reason: {note}"

    log_activity(
        'qr_session_deleted', description + ('' if present else '.'),
        'class_session', class_session.id, class_session.subject.department_id,
    )
    db.session.delete(class_session)  # attendance rows cascade
    db.session.commit()
    flash("Session deleted.", "success")
    return redirect(url_for('attendance.generate_qr'))


# ── Manual corrections ───────────────────────────────────────

MAX_NOTE_LENGTH = 200


def _back_to_session_attendance(class_session):
    return redirect(url_for('attendance.generate_qr', session_id=class_session.id) + '#qr-attendance')


def _correction_reason():
    """The required reason for a manual change, or (None, error message)."""
    note = normalize_text(request.form.get('note'))
    if not note:
        return None, "Give a short reason for the change, for example \"phone battery died\"."
    if len(note) > MAX_NOTE_LENGTH:
        return None, f"Keep the reason under {MAX_NOTE_LENGTH} characters."
    return note, None


@bp.route('/sessions/<int:id>/attendance', methods=['POST'])
@login_required
@role_required('principal', 'hod', 'teacher')
def mark_present(id):
    """Staff mark a student present by hand, e.g. when their phone could not scan."""
    class_session = _session_for_action(id)
    note, error = _correction_reason()
    student = db.session.get(Student, parse_int_value(request.form.get('student_id')) or 0)
    subject = class_session.subject

    if error:
        flash(error, 'danger')
    elif not student or not was_in_class(student, subject.department_id, subject.semester_id, class_session.created_at):
        flash("Choose a student from this session's class.", 'danger')
    elif Attendance.query.filter_by(student_id=student.id, session_id=class_session.id).first():
        flash(f"{student.name} is already marked present for this session.", 'warning')
    else:
        db.session.add(Attendance(
            student_id=student.id,
            session_id=class_session.id,
            timestamp=utc_now(),
            source=Attendance.SOURCE_MANUAL,
            marked_by_user_id=current_user.id,
            note=note,
        ))
        log_activity(
            'attendance_marked_manually',
            f"Marked {student.name} ({student.roll_no}) present in '{session_display_name(class_session)}' "
            f"manually. Reason: {note}",
            'class_session', class_session.id, subject.department_id,
        )
        try:
            db.session.commit()
            flash(f"{student.name} marked present.", 'success')
        except IntegrityError:
            db.session.rollback()
            flash(f"{student.name} is already marked present for this session.", 'warning')
    return _back_to_session_attendance(class_session)


@bp.route('/sessions/<int:id>/attendance/<int:attendance_id>/remove', methods=['POST'])
@login_required
@role_required('principal', 'hod', 'teacher')
def remove_attendance(id, attendance_id):
    """Staff remove an attendance entry, e.g. one marked by mistake."""
    class_session = _session_for_action(id)
    entry = db.get_or_404(Attendance, attendance_id)
    if entry.session_id != class_session.id:
        abort(404)
    note, error = _correction_reason()
    if error:
        flash(error, 'danger')
        return _back_to_session_attendance(class_session)

    student = entry.student
    log_activity(
        'attendance_removed',
        f"Removed {student.name} ({student.roll_no}) from '{session_display_name(class_session)}' "
        f"({entry.source} entry). Reason: {note}",
        'class_session', class_session.id, class_session.subject.department_id,
    )
    db.session.delete(entry)
    db.session.commit()
    flash(f"Removed {student.name}'s attendance for this session.", 'success')
    return _back_to_session_attendance(class_session)


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

    student = current_user.student
    subject = class_session.subject
    if student.department_id != subject.department_id or student.semester_id != subject.semester_id:
        return _status_page(
            'Wrong Class',
            f'This QR code is only for {subject.department.display_name} - {subject.semester.name}.',
            403, info, student=student,
        )

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

    student = current_user.student
    subject = class_session.subject
    if student.department_id != subject.department_id or student.semester_id != subject.semester_id:
        count_scan('wrong_class')
        return _json_error(f'This QR code is only for {subject.department.display_name} - {subject.semester.name}.', 403)

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
