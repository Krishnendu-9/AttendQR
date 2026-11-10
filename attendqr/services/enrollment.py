"""Who belongs to a class (department and semester), including students moved out of it.

A student row only holds the current class. When a student is moved (for example
promoted to the next semester), the class they left is recorded with the time,
so that class's reports still list them for the sessions held while they were in it.
"""
from sqlalchemy import func

from ..extensions import db
from ..models import Student, StudentClassHistory
from ..utils import ist_now


def record_class_change(student, old_department_id, old_semester_id):
    """Call when a student's class changes, before or after updating the student row."""
    if (old_department_id, old_semester_id) == (student.department_id, student.semester_id):
        return
    db.session.add(StudentClassHistory(
        student_id=student.id,
        department_id=old_department_id,
        semester_id=old_semester_id,
        left_at=ist_now(),
    ))


def class_roster(department_id, semester_id):
    """Students of a class as (student, left_at), sorted by roll number.

    left_at is None for current members; for former members it is when they last
    left the class. A student is counted for a session only if they were in the
    class when it was created (see in_class_at).
    """
    current = Student.query.filter_by(department_id=department_id, semester_id=semester_id).all()
    current_ids = {student.id for student in current}
    left = {
        student_id: left_at
        for student_id, left_at in db.session.query(StudentClassHistory.student_id, func.max(StudentClassHistory.left_at))
        .filter(StudentClassHistory.department_id == department_id, StudentClassHistory.semester_id == semester_id)
        .group_by(StudentClassHistory.student_id)
        if student_id not in current_ids
    }
    former = Student.query.filter(Student.id.in_(list(left))).all() if left else []
    members = [(student, None) for student in current] + [(student, left[student.id]) for student in former]
    members.sort(key=lambda member: (member[0].roll_no, member[0].id))
    return members


def in_class_at(left_at, when):
    """Whether a member with this left_at (None = still in the class) belonged to it at `when`."""
    return left_at is None or when is None or when < left_at


def was_in_class(student, department_id, semester_id, when):
    """Whether `student` belonged to the class at `when`: a current member, or moved out after it."""
    if (student.department_id, student.semester_id) == (department_id, semester_id):
        return True
    return db.session.query(
        StudentClassHistory.query.filter(
            StudentClassHistory.student_id == student.id,
            StudentClassHistory.department_id == department_id,
            StudentClassHistory.semester_id == semester_id,
            StudentClassHistory.left_at > when,
        ).exists()
    ).scalar()
