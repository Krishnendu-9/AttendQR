"""Who belongs to a class (department and semester), and when: late joiners and students moved out of it.

A student row only holds the current class and when they joined it. When a student is
moved (for example promoted to the next semester), the class they left is recorded with
when they had joined it and when they left, so that class's reports still list them for
the sessions held while they were in it, and only those.
"""
from dataclasses import dataclass, field
from datetime import datetime

from ..extensions import db
from ..models import Student, StudentClassHistory
from ..utils import utc_now
from .groups import end_memberships_in_class


@dataclass
class ClassMember:
    """A student of a class, with the spans of time they were in it.

    Each span is (joined_at, left_at) in stored UTC; None at either end means "open": from
    the start, or still in the class. A student can have several spans if they left and came back.
    """
    student: Student
    spans: list = field(default_factory=list)

    @property
    def is_current(self):
        return any(left is None for _, left in self.spans)

    @property
    def left_at(self) -> datetime | None:
        """None for a current member; for a former one, when they last left."""
        if self.is_current:
            return None
        return max(left for _, left in self.spans)

    @property
    def joined_at(self) -> datetime | None:
        """When the current span started (None: from the start). For a former member, their last span's."""
        current = [joined for joined, left in self.spans if left is None]
        if current:
            return current[0]
        return max(self.spans, key=lambda span: span[1])[0] if self.spans else None

    def in_class(self, when):
        """Whether the student was in the class at `when` (stored UTC)."""
        if when is None:
            return True
        return any((joined is None or joined <= when) and (left is None or when < left)
                   for joined, left in self.spans)


def record_class_change(student, old_department_id, old_semester_id):
    """Call after updating the student row when their class may have changed.

    The class they left is recorded with when they had joined it; they join the new one now,
    so its earlier sessions are not theirs. Their groups in the old class end at the same
    moment (a group belongs to one class).
    """
    if (old_department_id, old_semester_id) == (student.department_id, student.semester_id):
        return
    now = utc_now()
    db.session.add(StudentClassHistory(
        student_id=student.id,
        department_id=old_department_id,
        semester_id=old_semester_id,
        joined_at=student.class_joined_at,
        left_at=now,
    ))
    student.class_joined_at = now
    end_memberships_in_class(student.id, old_department_id, old_semester_id, now)


def _history_spans(department_id, semester_id, student_ids=None):
    query = db.session.query(
        StudentClassHistory.student_id, StudentClassHistory.joined_at, StudentClassHistory.left_at,
    ).filter(StudentClassHistory.department_id == department_id, StudentClassHistory.semester_id == semester_id)
    if student_ids is not None:
        query = query.filter(StudentClassHistory.student_id.in_(list(student_ids)))
    spans = {}
    for student_id, joined_at, left_at in query:
        spans.setdefault(student_id, []).append((joined_at, left_at))
    return spans


def class_roster(department_id, semester_id):
    """The students of a class as ClassMembers, current and former, sorted by roll number."""
    current = Student.query.filter_by(department_id=department_id, semester_id=semester_id).all()
    history = _history_spans(department_id, semester_id)
    current_ids = {student.id for student in current}
    former_ids = [student_id for student_id in history if student_id not in current_ids]
    former = Student.query.filter(Student.id.in_(former_ids)).all() if former_ids else []
    members = [ClassMember(student, [(student.class_joined_at, None), *history.get(student.id, [])])
               for student in current]
    members += [ClassMember(student, history[student.id]) for student in former]
    members.sort(key=lambda member: (member.student.roll_no, member.student.id))
    return members


def current_class_member(student):
    """The student as a member of their current class."""
    history = _history_spans(student.department_id, student.semester_id, [student.id])
    return ClassMember(student, [(student.class_joined_at, None), *history.get(student.id, [])])


def judged_elsewhere(left_at, semester):
    """Whether a member who left the class at `left_at` (None: still in it) drops out of its eligibility
    and analytics: they moved to another class while it was open.

    A locked semester is final, so it keeps everyone who was in it when it was locked: promoting a
    class after its exams does not empty its eligibility report.
    """
    if left_at is None:
        return False
    locked_at = semester.attendance_locked_at if semester is not None else None
    return locked_at is None or left_at <= locked_at


def was_in_class(student, department_id, semester_id, when):
    """Whether staff may mark `student` present in a session of this class held at `when`: a current
    member (a late joiner too, for a class they attended before their account existed), or moved out after it."""
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
