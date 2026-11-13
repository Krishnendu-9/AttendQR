"""Locked semesters: once attendance is final, nothing in the semester can change.

The HOD or the Principal locks a semester (typically after exams); only the Principal
can unlock it, with a reason. Both are in the tamper-evident activity log. While it is
locked, no session can be started, changed or deleted, nobody can scan into one, no
attendance can be added or removed (spot checks included), and no leave can be decided
for its students.
"""


def semester_lock_error(semester):
    """Why nothing in this semester may change, or None if it is open."""
    if semester is None or not semester.attendance_locked:
        return None
    return (f"Attendance for {semester.department.display_name} {semester.name} is locked. "
            "Ask the Principal to unlock it first.")


def session_lock_error(class_session):
    subject = class_session.subject
    return semester_lock_error(subject.semester if subject else None)
