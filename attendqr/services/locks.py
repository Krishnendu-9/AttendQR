"""Locked semesters: once attendance is final, nothing in the semester can change.

The HOD or the Principal locks a semester (typically after exams); only the Principal
can unlock it, with a reason. Both are in the tamper-evident activity log. While it is
locked, no session can be started, changed or deleted, nobody can scan into one, no
attendance can be added or removed (spot checks included), no leave can be decided
that falls in it, and none of its students (current or former) can be deleted.
"""
from ..extensions import db
from ..models import Semester, StudentClassHistory
from ..utils import local_day_start_utc


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
