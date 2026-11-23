"""QR sessions, teacher side: starting one, the session page, corrections and spot checks."""
import secrets

from flask import (
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from ...access import (
    can_access_session,
    log_activity,
    ordered_departments_query,
    role_required,
    session_display_name,
    user_role,
    visible_sessions_query,
)
from ...extensions import db
from ...models import Attendance, ClassSession, SpotCheck, Student, Subject
from ...security import is_safe_redirect_target
from ...services.enrollment import was_in_class
from ...services.groups import may_attend
from ...services.locks import session_lock_error
from ...services.occurrences import unscheduled_ids
from ...services.qr import (
    build_scan_url,
    make_qr_token,
    qr_data_uri,
)
from ...services.reports import attendance_counts
from ...services.sessions import SessionError, SessionForbidden, SessionRequest, absent_students, start_session
from ...services.spot_checks import (
    FAILED_CHECK_NOTE,
    SPOT_CHECK_SIZE,
    candidates,
    checks_for,
    resolve_spot_check,
    spot_check_open,
    start_spot_check,
)
from ...utils import normalize_text, parse_int_value, utc_now
from . import bp

# Deleting a session that has attendance erases those records, so it is kept from teachers.
SESSION_DELETE_WITH_ATTENDANCE_ROLES = ('principal', 'hod')


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
        unscheduled=unscheduled_ids(recent_sessions),
        current_teacher=current_user.teacher if user_role() == 'teacher' else None,
        create_token=secrets.token_hex(16),
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
        absent_students=absent_students(class_session),
        refresh_seconds=current_app.config['QR_REFRESH_SECONDS'],
        spot_checks=checks_for(class_session),
        spot_check_open=spot_check_open(class_session),
        spot_check_candidates=len(candidates(class_session)),
        spot_check_size=SPOT_CHECK_SIZE,
        lock_error=session_lock_error(class_session),
        unscheduled=unscheduled_ids([class_session]),
    )


def _create_session():
    try:
        class_session = start_session(SessionRequest.from_form(request.form))
    except SessionForbidden:
        abort(403)
    except SessionError as exc:
        return _form_error(str(exc))
    return redirect(url_for('attendance.generate_qr', session_id=class_session.id))


def _session_for_action(id):
    class_session = db.get_or_404(ClassSession, id)
    if not can_access_session(class_session):
        abort(403)
    return class_session


def _locked(class_session, back):
    """A redirect explaining the lock when the session's semester is locked, else None."""
    error = session_lock_error(class_session)
    if error:
        flash(error, 'warning')
        return back()
    return None


@bp.route('/deactivate_session/<int:id>', methods=['POST'])
@login_required
@role_required('principal', 'hod', 'teacher')
def deactivate_session(id):
    class_session = _session_for_action(id)
    if (refusal := _locked(class_session, lambda: _redirect_after_session_action())):
        return refusal
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
    if (refusal := _locked(class_session, lambda: _redirect_after_session_action())):
        return refusal
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
    if (refusal := _locked(class_session, lambda: _redirect_after_session_action())):
        return refusal
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
    if (refusal := _locked(class_session, lambda: _back_to_session_attendance(class_session))):
        return refusal
    note, error = _correction_reason()
    student = db.session.get(Student, parse_int_value(request.form.get('student_id')) or 0)
    subject = class_session.subject

    if error:
        flash(error, 'danger')
    elif not student or not was_in_class(student, subject.department_id, subject.semester_id, class_session.created_at):
        flash("Choose a student from this session's class.", 'danger')
    elif not may_attend(student, class_session):
        flash(f"Choose a student from this session's group ({class_session.group.name}).", 'danger')
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
    if (refusal := _locked(class_session, lambda: _back_to_session_attendance(class_session))):
        return refusal
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


# ── Spot checks ──────────────────────────────────────────────

def _back_to_spot_checks(class_session):
    return redirect(url_for('attendance.generate_qr', session_id=class_session.id) + '#spot-checks')


@bp.route('/sessions/<int:id>/spot-checks', methods=['POST'])
@login_required
@role_required('principal', 'hod', 'teacher')
def start_spot_checks(id):
    """Pick students marked present at random, for the teacher to look for in the room."""
    class_session = _session_for_action(id)
    if (refusal := _locked(class_session, lambda: _back_to_spot_checks(class_session))):
        return refusal
    if not spot_check_open(class_session):
        flash("Spot checks are only possible during the session and up to 2 hours after it.", 'warning')
        return _back_to_spot_checks(class_session)
    checks = start_spot_check(class_session)
    if not checks:
        flash("Nobody marked present by QR scan is left to check.", 'warning')
        return _back_to_spot_checks(class_session)
    log_activity(
        'spot_check_started',
        f"Started a spot check of {len(checks)} student(s) in '{session_display_name(class_session)}'.",
        'class_session', class_session.id, class_session.subject.department_id,
    )
    db.session.commit()
    return _back_to_spot_checks(class_session)


@bp.route('/sessions/<int:id>/spot-checks/<int:check_id>', methods=['POST'])
@login_required
@role_required('principal', 'hod', 'teacher')
def resolve_spot_checks(id, check_id):
    class_session = _session_for_action(id)
    if (refusal := _locked(class_session, lambda: _back_to_spot_checks(class_session))):
        return refusal
    check = db.get_or_404(SpotCheck, check_id)
    if check.session_id != class_session.id:
        abort(404)
    result = request.form.get('result')
    if result not in ('present', 'absent'):
        abort(400)
    if check.result != SpotCheck.PENDING:
        flash("That check has already been answered.", 'warning')
        return _back_to_spot_checks(class_session)
    if not spot_check_open(class_session):
        flash("Spot checks are only possible during the session and up to 2 hours after it.", 'warning')
        return _back_to_spot_checks(class_session)

    student = check.student
    removed = resolve_spot_check(check, result == 'present', current_user)
    department_id = class_session.subject.department_id
    if result == 'present':
        log_activity('spot_check_passed', f"{student.name} ({student.roll_no}) was in the room for "
                     f"'{session_display_name(class_session)}'.", 'class_session', class_session.id, department_id)
        flash(f"{student.name} confirmed in the room.", 'success')
    else:
        log_activity('spot_check_failed', f"{student.name} ({student.roll_no}) was not in the room for "
                     f"'{session_display_name(class_session)}'"
                     + ("; their attendance was removed." if removed else "."),
                     'class_session', class_session.id, department_id)
        if removed:
            log_activity('attendance_removed', f"Removed {student.name} ({student.roll_no}) from "
                         f"'{session_display_name(class_session)}' ({removed.source} entry). Reason: {FAILED_CHECK_NOTE}",
                         'class_session', class_session.id, department_id)
        flash(f"{student.name} was not in the room; their attendance for this session was removed.", 'warning')
    db.session.commit()
    return _back_to_spot_checks(class_session)
