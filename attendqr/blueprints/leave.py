"""Leave requests: students apply, the department HOD or the Principal decides."""
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.orm import joinedload

from ..access import can_access_student, department_filter, is_principal, log_activity, role_required, user_role
from ..extensions import db
from ..models import LeaveRequest, Student
from ..services.leave import MAX_NOTE_LENGTH, describe_range, parse_leave_form
from ..utils import normalize_text, utc_now

bp = Blueprint('leave', __name__)

DECIDERS = ('principal', 'hod')
RECENT_DECISIONS = 50


@bp.route('/leave', methods=['GET', 'POST'])
@login_required
@role_required('student', *DECIDERS)
def index():
    if user_role() == 'student':
        return _student_page()
    return _staff_page()


# ── Students ─────────────────────────────────────────────────

def _student_page():
    student = current_user.student
    if student is None:
        abort(403)
    form_error = None
    if request.method == 'POST':
        values, form_error = parse_leave_form(request.form, student)
        if values is not None:
            leave = LeaveRequest(student_id=student.id, **values)
            db.session.add(leave)
            db.session.flush()
            log_activity('leave_requested', f"{student.name} ({student.roll_no}) asked for {leave.kind_label.lower()} "
                         f"leave: {describe_range(leave)}.", 'leave_request', leave.id, student.department_id)
            db.session.commit()
            flash("Leave request sent. Your HOD will review it.", 'success')
            return redirect(url_for('leave.index'))

    requests = (
        LeaveRequest.query.options(joinedload(LeaveRequest.decided_by))
        .filter_by(student_id=student.id)
        .order_by(LeaveRequest.start_date.desc())
        .all()
    )
    return render_template('leave.html', student=student, requests=requests, form_error=form_error,
                           form=request.form if form_error else {}, kinds=LeaveRequest.KINDS,
                           max_note=MAX_NOTE_LENGTH), 400 if form_error else 200


@bp.route('/leave/<int:id>/withdraw', methods=['POST'])
@login_required
@role_required('student')
def withdraw(id):
    leave = db.get_or_404(LeaveRequest, id)
    if leave.student_id != current_user.student_id:
        abort(404)
    if leave.status != LeaveRequest.PENDING:
        flash("Only a request that has not been decided yet can be withdrawn.", 'warning')
        return redirect(url_for('leave.index'))
    log_activity('leave_withdrawn', f"{leave.student.name} withdrew a leave request for {describe_range(leave)}.",
                 'leave_request', leave.id, leave.student.department_id)
    db.session.delete(leave)
    db.session.commit()
    flash("Leave request withdrawn.", 'success')
    return redirect(url_for('leave.index'))


# ── HOD and Principal ────────────────────────────────────────

def _scoped(query):
    query = query.join(Student, Student.id == LeaveRequest.student_id)
    return query if is_principal() else query.filter(department_filter(Student.department_id))


def _staff_page():
    if request.method == 'POST':
        abort(405)
    base = LeaveRequest.query.options(joinedload(LeaveRequest.student).joinedload(Student.semester),
                                      joinedload(LeaveRequest.decided_by))
    pending = _scoped(base.filter(LeaveRequest.status == LeaveRequest.PENDING)).order_by(
        LeaveRequest.start_date, LeaveRequest.id).all()
    decided = _scoped(base.filter(LeaveRequest.status != LeaveRequest.PENDING)).order_by(
        LeaveRequest.decided_at.desc()).limit(RECENT_DECISIONS).all()
    return render_template('leave.html', student=None, pending=pending, decided=decided,
                           kinds=LeaveRequest.KINDS, max_note=MAX_NOTE_LENGTH)


@bp.route('/leave/<int:id>/decide', methods=['POST'])
@login_required
@role_required(*DECIDERS)
def decide(id):
    leave = db.get_or_404(LeaveRequest, id)
    if not can_access_student(leave.student):
        abort(403)
    decision = request.form.get('decision')
    note = normalize_text(request.form.get('note'))
    if decision not in ('approve', 'reject'):
        abort(400)
    if leave.status != LeaveRequest.PENDING:
        flash("This request has already been decided.", 'warning')
        return redirect(url_for('leave.index'))
    if decision == 'reject' and not note:
        flash("Give the student a short reason for rejecting the request.", 'danger')
        return redirect(url_for('leave.index'))
    if len(note) > MAX_NOTE_LENGTH:
        flash(f"Keep the note under {MAX_NOTE_LENGTH} characters.", 'danger')
        return redirect(url_for('leave.index'))

    leave.status = LeaveRequest.APPROVED if decision == 'approve' else LeaveRequest.REJECTED
    leave.decided_by_user_id = current_user.id
    leave.decided_at = utc_now()
    leave.decision_note = note or None
    student = leave.student
    log_activity(f'leave_{leave.status}',
                 f"{'Approved' if decision == 'approve' else 'Rejected'} {leave.kind_label.lower()} leave for "
                 f"{student.name} ({student.roll_no}): {describe_range(leave)}." + (f" Note: {note}" if note else ''),
                 'leave_request', leave.id, student.department_id)
    db.session.commit()
    flash(f"Leave for {student.name} {leave.status}.", 'success')
    return redirect(url_for('leave.index'))
