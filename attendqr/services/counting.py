"""The one rule for how a session counts for a student. Every percentage uses it.

For each (student, session) pair the mark is, in this order:

1. no attendance entry, and the student was not in the class when the session was held
   (they joined later, or had moved to another class): NOT_IN_CLASS, not counted
2. no attendance entry, and the session was for a group the student was not in then
   (another lab batch): NOT_IN_GROUP, not counted
3. the student was on approved leave that day: ON_LEAVE, not counted, even if marked present
4. otherwise the attendance source ('scan' or 'manual'), or None for absent

An attendance entry always makes the session one of the student's classes (steps 1 and 2):
staff may mark a late joiner present for a class they attended before their account existed.

The student dashboard, the report matrix (and with it exports, eligibility and analytics)
and the shortage list all call SessionMarks.mark(), so they cannot drift apart.
"""
from .groups import GroupRosters
from .leave import ON_LEAVE, LeaveWindows

# A session held while the student was not in the class: before they joined, or after they moved out.
NOT_IN_CLASS = 'not_in_class'
# A group's session (a lab batch) held for a group the student was not in.
NOT_IN_GROUP = 'not_in_group'
# Marks that leave a session out of a student's classes held.
NOT_COUNTED_MARKS = (NOT_IN_CLASS, NOT_IN_GROUP, ON_LEAVE)


def is_counted(mark):
    return mark not in NOT_COUNTED_MARKS


class SessionMarks:
    """Group membership and approved leave for some students and sessions, loaded once."""

    def __init__(self, student_ids, group_ids):
        self.leave = LeaveWindows(student_ids)
        self.rosters = GroupRosters(group_ids)

    def mark(self, member, created_at, group_id, source):
        """The mark of one session (held at `created_at`, for `group_id` or the whole class) for a
        ClassMember (services/enrollment.py) whose attendance entry has `source` (None: none)."""
        student_id = member.student.id
        if not source and not member.in_class(created_at):
            return NOT_IN_CLASS
        if not source and not self.rosters.is_member(student_id, group_id, created_at):
            return NOT_IN_GROUP
        if self.leave.excused(student_id, created_at):
            return ON_LEAVE
        return source

    def has_leave(self, student_id):
        return self.leave.has_leave(student_id)
