"""Acting on a dated class: starting its session, changing it for one date, extra classes."""

from flask import abort, flash, redirect, request, url_for
from flask_login import current_user, login_required

from ...access import (
    log_activity,
    role_required,
    user_role,
)
from ...extensions import db
from ...models import (
    RoutineChange,
)
from ...services.changes import (
    add_extra_class,
    can_undo,
    change_occurrence,
    describe_change,
    may_run,
)
from ...services.locks import semester_lock_error
from ...services.occurrences import find
from ...services.routine import (
    RoutineError,
    class_name,
    entry_label,
)
from ...services.sessions import SessionError, start_routine_session
from ...utils import local_now
from . import bp
from .common import (
    MANAGERS,
    STAFF,
    _back,
    _class,
)


@bp.route('/routine/occurrences/start', methods=['POST'])
@login_required
@role_required(*STAFF)
def start_occurrence():
    """Start (or open) the QR session for a class in the routine."""
    occurrence = find(request.form.get('key', ''))
    if occurrence is None:
        abort(404)
    if not may_run(occurrence):
        abort(403)
    if occurrence.session is not None:
        return redirect(url_for('attendance.generate_qr', session_id=occurrence.session.id))
    lock_error = semester_lock_error(occurrence.semester)
    if lock_error:
        flash(lock_error, 'warning')
        return _back(url_for('main.dashboard'))
    now = local_now()
    if not occurrence.can_start(now):
        flash("A session can be started from 15 minutes before the class until it ends.", 'warning')
        return _back(url_for('main.dashboard'))
    try:
        class_session = start_routine_session(occurrence)
    except SessionError as exc:
        flash(str(exc), 'warning')
        return _back(url_for('main.dashboard'))
    return redirect(url_for('attendance.generate_qr', session_id=class_session.id))


@bp.route('/routine/occurrences/change', methods=['POST'])
@login_required
@role_required(*STAFF)
def change_view():
    occurrence = find(request.form.get('key', ''))
    if occurrence is None:
        abort(404)
    back = url_for('routine.class_week', semester_id=occurrence.semester.id, week=occurrence.day.isoformat())
    if user_role() == 'teacher':
        back = url_for('routine.mine', week=occurrence.day.isoformat())
    try:
        change = change_occurrence(occurrence, request.form.get('kind'), request.form, current_user)
        db.session.flush()
    except RoutineError as exc:
        db.session.rollback()
        flash(f"Not changed: {exc}", 'danger')
        return _back(back)
    label = entry_label(occurrence.subject, occurrence.title, occurrence.group)
    log_activity(f'routine_{change.kind}', describe_change(change, f"{label} ({class_name(occurrence.semester)})"),
                 'routine_change', change.id, occurrence.semester.department_id)
    db.session.commit()
    flash(f"{change.kind_label}: {label} on {occurrence.original_day:%d %b}.", 'success')
    return _back(back)


@bp.route('/routine/changes/<int:id>/undo', methods=['POST'])
@login_required
@role_required(*STAFF)
def undo_change(id):
    change = db.get_or_404(RoutineChange, id)
    if not can_undo(change):
        abort(403)
    label = entry_label(change.subject, change.title, change.group)
    semester_id, day = change.semester_id, change.date
    log_activity('routine_change_undone', f"Undid the change '{change.kind_label}' to {label} "
                 f"({class_name(change.semester)}) on {change.date:%d %b %Y}.", 'routine_change', change.id,
                 change.department_id)
    db.session.delete(change)
    db.session.commit()
    flash("Change undone.", 'success')
    return _back(url_for('routine.class_week', semester_id=semester_id, week=day.isoformat()))


@bp.route('/routine/class/<int:semester_id>/extra', methods=['POST'])
@login_required
@role_required(*MANAGERS)
def add_extra(semester_id):
    semester = _class(semester_id, manage=True)
    try:
        change = add_extra_class(semester, request.form, current_user)
        db.session.flush()
    except RoutineError as exc:
        db.session.rollback()
        flash(f"Not added: {exc}", 'danger')
        return _back(url_for('routine.class_week', semester_id=semester.id))
    label = entry_label(change.subject, change.title, change.group)
    log_activity('routine_extra', describe_change(change, f"{label} ({class_name(semester)})"),
                 'routine_change', change.id, semester.department_id)
    db.session.commit()
    flash(f"Extra class added on {change.date:%d %b}.", 'success')
    return redirect(url_for('routine.class_week', semester_id=semester.id, week=change.date.isoformat()))
