"""Locked semesters: once attendance is final, nothing in the semester can change.

The HOD or the Principal locks a semester (typically after exams); only the Principal
can unlock it, with a reason. Both are in the tamper-evident activity log. While it is
locked, no session can be started, changed or deleted, nobody can scan into one, no
attendance can be added or removed (spot checks included), no leave can be decided
that falls in it, and none of its students (current or former) can be deleted.

Two layers enforce it. Routes check first, with the helpers below, so people get a
message that fits what they tried. Underneath, a guard on every database flush
(refuse_locked_changes) refuses any change to a locked semester's records that reaches
the database, so a route that forgets the check still cannot change them. The guard
sees changes made through the ORM. Bulk statements (query.update(), table.delete()) and
foreign key cascades bypass it; the app uses those only where a lock does not apply
(clearing old scan IP addresses, deleting a student's login, the demo reset).
"""
from urllib.parse import urlsplit

from flask import flash, redirect, request, url_for
from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from ..extensions import db
from ..models import (
    Attendance,
    ClassSession,
    LeaveRequest,
    Semester,
    SpotCheck,
    Student,
    StudentClassHistory,
    Subject,
)
from ..utils import local_day_start_utc


class SemesterLocked(Exception):
    """A change that would alter a locked semester's records. The message says which semester."""


def semester_lock_error(semester):
    """Why nothing in this semester may change, or None if it is open."""
    if semester is None or not semester.attendance_locked:
        return None
    return (f"Attendance for {semester.department.display_name} {semester.name} is locked. "
            "Ask the Principal to unlock it first.")


def session_lock_error(class_session):
    subject = class_session.subject
    return semester_lock_error(subject.semester if subject else None)


def student_semester_ids(student, since=None):
    """The semesters whose records involve `student`: their current one, and each one they moved out of.

    With `since` (stored UTC), a class they left before then is left out: none of its sessions
    from `since` on are theirs. The current class is always included, since staff may mark a
    late joiner present for a session held before they joined.
    """
    query = db.session.query(StudentClassHistory.semester_id).filter(StudentClassHistory.student_id == student.id)
    if since is not None:
        query = query.filter(StudentClassHistory.left_at > since)
    return {student.semester_id, *(semester_id for (semester_id,) in query)}


def student_lock_error(student, since=None):
    """Why a change to `student`'s records may not go ahead because one of their semesters is locked, or None.

    Deleting a student erases their records in every class they were in; approving leave
    from `since` changes every class they were in from then on (see student_semester_ids).
    """
    ids = student_semester_ids(student, since)
    for semester in Semester.query.filter(Semester.id.in_(ids)).order_by(Semester.id):
        error = semester_lock_error(semester)
        if error:
            return error
    return None


def leave_lock_error(leave):
    """Why a decision on this leave would change a locked semester, or None (see student_lock_error)."""
    return student_lock_error(leave.student, since=local_day_start_utc(leave.start_date))


# ── The guard on every flush ─────────────────────────────────

# Attendance columns that may change in a locked semester: only the scan address, which
# the retention period clears (services/retention.py). Nothing a percentage depends on.
ATTENDANCE_COLUMNS_ALWAYS_ALLOWED = frozenset({'scan_ip'})


def _changed(obj):
    """The names of the column attributes changed on `obj` since it was loaded."""
    state = inspect(obj)
    return {attr.key for attr in state.mapper.column_attrs if state.attrs[attr.key].history.has_changes()}


def _old_value(obj, key):
    history = inspect(obj).attrs[key].history
    return history.deleted[0] if history.deleted else getattr(obj, key)


def _subject_semester(subject_id):
    subject = db.session.get(Subject, subject_id) if subject_id else None
    return subject.semester if subject else None


def _session_semester(session_id):
    class_session = db.session.get(ClassSession, session_id) if session_id else None
    return _subject_semester(class_session.subject_id) if class_session else None


def _semester_error(semester_id):
    return semester_lock_error(db.session.get(Semester, semester_id)) if semester_id else None


def _attendance_error(entry, new, deleted):
    if not (new or deleted) and _changed(entry) <= ATTENDANCE_COLUMNS_ALWAYS_ALLOWED:
        return None
    return semester_lock_error(_session_semester(entry.session_id))


def _leave_error(leave, new, deleted):
    """Only an approved request changes anyone's classes: approving, reopening or deleting one, or moving its days."""
    if new or deleted:
        counts = leave.status == LeaveRequest.APPROVED
    else:
        changed = _changed(leave)
        counts = (
            ('status' in changed and LeaveRequest.APPROVED in (leave.status, _old_value(leave, 'status')))
            or (leave.status == LeaveRequest.APPROVED and changed & {'start_date', 'end_date', 'student_id'})
        )
    if not counts:
        return None
    start = min(leave.start_date, _old_value(leave, 'start_date')) if not new else leave.start_date
    student = leave.student or db.session.get(Student, leave.student_id)
    return student_lock_error(student, since=local_day_start_utc(start)) if student else None


def _student_error(student, new, deleted):
    if deleted:
        return student_lock_error(student)
    return None


def _subject_error(subject, new, deleted):
    if deleted or 'counts_towards_attendance' in _changed(subject):
        return _semester_error(_old_value(subject, 'semester_id') if not new else subject.semester_id)
    return None


def _session_error(class_session, new, deleted):
    if not (new or deleted) and not _changed(class_session):
        return None
    return semester_lock_error(_subject_semester(class_session.subject_id))


def _spot_check_error(check, new, deleted):
    return semester_lock_error(_session_semester(check.session_id))


GUARDED = (
    (Attendance, _attendance_error),
    (ClassSession, _session_error),
    (SpotCheck, _spot_check_error),
    (LeaveRequest, _leave_error),
    (Student, _student_error),
    (Subject, _subject_error),
)


def locked_change_error(session):
    """Why the session's pending changes may not be written, or None. Looks only, never writes."""
    with session.no_autoflush:
        for obj, new, deleted in (
            *((obj, True, False) for obj in session.new),
            *((obj, False, True) for obj in session.deleted),
            *((obj, False, False) for obj in session.dirty if session.is_modified(obj)),
        ):
            for model, check in GUARDED:
                if isinstance(obj, model):
                    error = check(obj, new, deleted)
                    if error:
                        return error
    return None


@event.listens_for(Session, 'before_flush')
def refuse_locked_changes(session, flush_context, instances):
    error = locked_change_error(session)
    if error:
        raise SemesterLocked(error)


def init_app(app):
    @app.errorhandler(SemesterLocked)
    def semester_locked(error):
        """A change that only the guard caught: undo it and say why, like the routes' own checks."""
        from ..security import is_safe_redirect_target, wants_json_response
        db.session.rollback()
        message = str(error)
        if wants_json_response():
            return {'success': False, 'message': message}, 403
        flash(message, 'warning')
        # Back to the page the change was made from, when it is one of ours.
        referrer = urlsplit(request.referrer or '')
        back = referrer.path + (f'?{referrer.query}' if referrer.query else '')
        same_site = referrer.netloc == request.host and is_safe_redirect_target(back)
        return redirect(back if same_site else url_for('main.dashboard'))
