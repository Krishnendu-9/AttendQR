"""Groups within a class: lab batches, electives, tutorial sets.

The rule: a session for a group counts for a student only if they were a member of
the group when it was held, or if they have an attendance entry for it. For everyone
else in the class it counts neither way, like a class held before they joined. Without
this, a Gr-1 lab marked every Gr-2 student absent.

Membership keeps its history, as class membership does (enrollment.py): leaving a group
sets left_at and never deletes the row, and joining again adds a new row. A student may
be in several groups (a lab batch and an elective).
"""
import re
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import func

from ..extensions import db
from ..models import ClassGroup, ClassGroupMember, ClassSession, Student, TimetableEntry
from ..utils import natural_key, normalize_text, utc_now

MAX_GROUP_NAME_LENGTH = 60
MAX_GROUPS_PER_SPLIT = 20


class GroupError(ValueError):
    pass


# ── Who was in a group when ──────────────────────────────────

class GroupRosters:
    """Membership spans of some groups, loaded with one query."""

    def __init__(self, group_ids):
        self._spans = defaultdict(list)
        ids = sorted({group_id for group_id in group_ids if group_id})
        if not ids:
            return
        rows = db.session.query(
            ClassGroupMember.group_id, ClassGroupMember.student_id, ClassGroupMember.joined_at, ClassGroupMember.left_at,
        ).filter(ClassGroupMember.group_id.in_(ids))
        for group_id, student_id, joined_at, left_at in rows:
            self._spans[(group_id, student_id)].append((joined_at, left_at))

    def is_member(self, student_id, group_id, when):
        """Whether the student belonged to the group at `when` (stored UTC). Always true for the whole class."""
        if group_id is None:
            return True
        return any(joined <= when and (left is None or when < left)
                   for joined, left in self._spans.get((group_id, student_id), ()))


def is_current_member(student_id, group_id):
    if group_id is None:
        return True
    return db.session.query(ClassGroupMember.query.filter(
        ClassGroupMember.group_id == group_id, ClassGroupMember.student_id == student_id,
        ClassGroupMember.left_at.is_(None),
    ).exists()).scalar()


def may_attend(student, class_session):
    """Whether a student of the session's class belongs to its group: when it was held, or now
    (added since, e.g. a late fix by the HOD). Always true for whole-class sessions."""
    group_id = class_session.group_id
    if group_id is None:
        return True
    return (GroupRosters([group_id]).is_member(student.id, group_id, class_session.created_at)
            or is_current_member(student.id, group_id))


def describe_group(class_session):
    """"Gr-1 of MCA - Semester 1" for messages, or the class alone for whole-class sessions."""
    subject = class_session.subject
    class_name = f"{subject.department.display_name} - {subject.semester.name}"
    return f"{class_session.group.name} of {class_name}" if class_session.group else class_name


# ── Listing ──────────────────────────────────────────────────

@dataclass
class GroupSummary:
    group: ClassGroup
    members: list      # current members, by roll number
    sessions: int      # sessions held for the group


def class_groups(semester_id):
    """The groups of a class, by name ("Gr-2" before "Gr-10")."""
    return sorted(ClassGroup.query.filter_by(semester_id=semester_id).all(), key=lambda g: natural_key(g.name))


def current_members(group_id):
    students = (
        Student.query.join(ClassGroupMember, ClassGroupMember.student_id == Student.id)
        .filter(ClassGroupMember.group_id == group_id, ClassGroupMember.left_at.is_(None))
        .all()
    )
    return sorted(students, key=lambda s: (natural_key(s.roll_no), s.id))


def summaries(semester_id):
    groups = class_groups(semester_id)
    counts = dict(
        db.session.query(ClassSession.group_id, func.count(ClassSession.id))
        .filter(ClassSession.group_id.in_([g.id for g in groups]))
        .group_by(ClassSession.group_id)
    ) if groups else {}
    return [GroupSummary(g, current_members(g.id), counts.get(g.id, 0)) for g in groups]


def student_groups(student):
    """The groups the student is in now, by name."""
    groups = (
        ClassGroup.query.join(ClassGroupMember, ClassGroupMember.group_id == ClassGroup.id)
        .filter(ClassGroupMember.student_id == student.id, ClassGroupMember.left_at.is_(None))
        .all()
    )
    return sorted(groups, key=lambda g: natural_key(g.name))


# ── Changing groups ──────────────────────────────────────────

def clean_name(semester, name, exclude_id=None):
    """The normalised group name, or GroupError if it is empty, too long or taken in the class."""
    name = normalize_text(name)
    if not name:
        raise GroupError("Give the group a name, such as Gr-1 or Elective A.")
    if len(name) > MAX_GROUP_NAME_LENGTH:
        raise GroupError(f"Keep the group name under {MAX_GROUP_NAME_LENGTH} characters.")
    clash = ClassGroup.query.filter(ClassGroup.semester_id == semester.id, func.lower(ClassGroup.name) == name.lower())
    if exclude_id is not None:
        clash = clash.filter(ClassGroup.id != exclude_id)
    existing = clash.first()
    if existing:
        raise GroupError(f"{semester.name} already has a group called {existing.name}.")
    return name


def create_group(semester, name):
    group = ClassGroup(department_id=semester.department_id, semester_id=semester.id, name=clean_name(semester, name))
    db.session.add(group)
    db.session.flush()
    return group


def delete_block(group):
    """Why the group cannot be deleted, or None."""
    held = ClassSession.query.filter_by(group_id=group.id).count()
    if held:
        return (f"{group.name} has {held} session(s), which would lose who they counted for. "
                "Rename it instead, or remove its members.")
    in_routine = TimetableEntry.query.filter_by(group_id=group.id).count()
    if in_routine:
        return f"{group.name} has {in_routine} class(es) in a routine. Remove them from the routine first."
    return None


def class_students(semester):
    """The current students of the class, by roll number."""
    students = Student.query.filter_by(department_id=semester.department_id, semester_id=semester.id).all()
    return sorted(students, key=lambda s: (natural_key(s.roll_no), s.id))


def set_members(group, student_ids, now=None):
    """Make exactly these current students of the group's class its members. Returns (added, removed).

    Ids that are not current students of the class are ignored.
    """
    now = now or utc_now()
    allowed = {s.id: s for s in Student.query.filter_by(department_id=group.department_id, semester_id=group.semester_id)}
    wanted = {student_id for student_id in student_ids if student_id in allowed}
    open_rows = {row.student_id: row for row in ClassGroupMember.query.filter_by(group_id=group.id, left_at=None)}
    added = [allowed[student_id] for student_id in sorted(wanted - set(open_rows))]
    removed = [row.student for student_id, row in open_rows.items() if student_id not in wanted]
    for student in added:
        db.session.add(ClassGroupMember(group_id=group.id, student_id=student.id, joined_at=now))
    for student_id, row in open_rows.items():
        if student_id not in wanted:
            row.left_at = now
    return added, removed


def add_members(group, students, now=None):
    """Add students who are not already members. Returns those added."""
    current = {row.student_id for row in ClassGroupMember.query.filter_by(group_id=group.id, left_at=None)}
    return set_members(group, current | {s.id for s in students}, now)[0]


def end_memberships_in_class(student_id, department_id, semester_id, when=None):
    """End the student's open memberships in a class's groups (they moved to another class)."""
    group_ids = db.session.query(ClassGroup.id).filter(
        ClassGroup.department_id == department_id, ClassGroup.semester_id == semester_id,
    )
    ClassGroupMember.query.filter(
        ClassGroupMember.student_id == student_id,
        ClassGroupMember.left_at.is_(None),
        ClassGroupMember.group_id.in_(group_ids),
    ).update({ClassGroupMember.left_at: when or utc_now()}, synchronize_session=False)


# ── Splitting a class by roll number ─────────────────────────

# A hyphen or an en dash (U+2013) between the bounds.
_RANGE_LINE = re.compile(r'^\s*(?P<name>[^:]+?)\s*:\s*(?P<low>[^\s\-\u2013]+)\s*[-\u2013]\s*(?P<high>\S+)\s*$')
_TRAILING_NUMBER = re.compile(r'(\d+)\D*$')


def parse_ranges(text):
    """Lines like "Gr-1: 1-37" or "Gr-2: MCA038-MCA075" as [(name, low, high)]. Raises GroupError."""
    ranges = []
    for number, line in enumerate((text or '').splitlines(), 1):
        if not line.strip():
            continue
        match = _RANGE_LINE.match(line)
        if not match:
            raise GroupError(f'Line {number} should look like "Gr-1: 1-37" (a name, a colon, then a range).')
        ranges.append((normalize_text(match['name']), match['low'], match['high']))
    if not ranges:
        raise GroupError('Enter at least one line, such as "Gr-1: 1-37".')
    if len(ranges) > MAX_GROUPS_PER_SPLIT:
        raise GroupError(f"Split into at most {MAX_GROUPS_PER_SPLIT} groups at once.")
    return ranges


def roll_in_range(roll_no, low, high):
    """Whether a roll number falls in low..high, both included.

    Plain numbers compare with the number at the end of the roll ("37" matches "MCA037");
    anything else compares in natural order ("MCA9" before "MCA10").
    """
    if low.isdigit() and high.isdigit():
        match = _TRAILING_NUMBER.search(roll_no or '')
        return bool(match) and int(low) <= int(match.group(1)) <= int(high)
    try:
        return natural_key(low) <= natural_key(roll_no) <= natural_key(high)
    except TypeError:  # a text part against a number part: not comparable, so not in range
        return False


def equal_ranges(students, count, prefix='Gr-'):
    """Ranges that split the students (by roll number) into `count` groups of nearly equal size."""
    if not 2 <= count <= MAX_GROUPS_PER_SPLIT:
        raise GroupError(f"Split into 2 to {MAX_GROUPS_PER_SPLIT} groups.")
    if len(students) < count:
        raise GroupError(f"The class has {len(students)} student(s), too few for {count} groups.")
    size, extra = divmod(len(students), count)
    ranges, start = [], 0
    for index in range(count):
        end = start + size + (1 if index < extra else 0)
        ranges.append((f"{prefix}{index + 1}", students[start].roll_no, students[end - 1].roll_no, students[start:end]))
        start = end
    return ranges


def split_by_roll(semester, ranges, now=None):
    """Create (or fill) a group for each (name, low, high) with the class's students in that range.

    Returns [(group, added students)]. Existing members stay; a student can land in two
    groups only if the ranges overlap.
    """
    now = now or utc_now()
    students = class_students(semester)
    existing = {g.name.lower(): g for g in class_groups(semester.id)}
    result = []
    for name, low, high, *chosen in ranges:
        members = chosen[0] if chosen else [s for s in students if roll_in_range(s.roll_no, low, high)]
        group = existing.get(name.lower())
        if group is None:
            group = create_group(semester, name)
            existing[name.lower()] = group
        result.append((group, add_members(group, members, now)))
    return result
