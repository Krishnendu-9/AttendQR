"""Changes to the routine for one date: cancel or move a class, give it to a substitute, add an extra class.

Who: the HOD and the Principal can make any change. A teacher can cancel or move a class they
teach, if the "Teachers may change their own classes" setting allows it, and only until the
class ends: a class that passed without a session stays "not recorded" for the HOD to see,
unless the HOD or Principal records what happened. Every change needs a reason and is in the
activity log; a locked semester's routine cannot change; a class that was already held cannot
be cancelled or moved, and a class cannot be moved to a time that has passed or onto a holiday.
"""
from datetime import datetime, timedelta

from flask_login import current_user

from ..access import can_access_department, current_teacher_id, user_role
from ..extensions import db
from ..models import ClassGroup, ClassSession, RoutineChange, Subject, Teacher
from ..utils import local_now, normalize_text
from .locks import semester_lock_error
from .occurrences import CANCELLED, HELD, NOW, UPCOMING, occurrences
from .routine import (
    MAX_ROOM_LENGTH,
    RoutineError,
    clock,
    entry_label,
    holiday_on,
    holidays_between,
    parse_clock,
    parse_day,
)
from .settings import get_setting

MAX_REASON_LENGTH = 200
MAX_DAYS_AHEAD = 120


def is_manager_of(semester):
    return user_role() in ('principal', 'hod') and can_access_department(semester.department_id)


def may_run(occurrence):
    """Whether the current user may start or open the session of this class: the HOD or Principal
    of the class, or one of its teachers that day (a substitute included)."""
    if is_manager_of(occurrence.semester):
        return True
    teacher_id = current_teacher_id()
    return user_role() == 'teacher' and teacher_id is not None and any(t.id == teacher_id for t in occurrence.teachers)


def allowed_kinds(occurrence):
    """The changes the current user may make to this occurrence."""
    if occurrence.status in (HELD, CANCELLED) or occurrence.change is not None or occurrence.entry is None:
        return []
    if semester_lock_error(occurrence.semester):
        return []
    if is_manager_of(occurrence.semester):
        return [RoutineChange.CANCEL, RoutineChange.MOVE, RoutineChange.SUBSTITUTE]
    teacher_id = current_teacher_id()
    # Only until the class ends: cancelling a missed class afterwards would hide it from planned vs held.
    if (user_role() == 'teacher' and teacher_id and get_setting('teachers_change_own_classes')
            and occurrence.status in (UPCOMING, NOW) and any(t.id == teacher_id for t in occurrence.teachers)):
        return [RoutineChange.CANCEL, RoutineChange.MOVE]
    return []


def was_held(change):
    """Whether a session already holds the changed class (then the change stays, as history)."""
    if change.kind == RoutineChange.EXTRA:
        return change.session_id is not None
    return db.session.query(ClassSession.query.filter_by(
        timetable_entry_id=change.timetable_entry_id, occurrence_date=change.date).exists()).scalar()


def can_undo(change):
    """The HOD or Principal can undo any change; a teacher only their own. Never once the class was held."""
    if semester_lock_error(change.semester) or was_held(change):
        return False
    if is_manager_of(change.semester):
        return True
    return user_role() == 'teacher' and change.created_by_user_id == current_user.id


def _reason(form):
    reason = normalize_text(form.get('reason'))
    if not reason:
        raise RoutineError("Give a reason, such as \"teacher on leave\" or \"exam in the hall\".")
    if len(reason) > MAX_REASON_LENGTH:
        raise RoutineError(f"Keep the reason under {MAX_REASON_LENGTH} characters.")
    return reason


def _room(form):
    room = normalize_text(form.get('room')) or None
    if room and len(room) > MAX_ROOM_LENGTH:
        raise RoutineError(f"Keep the room under {MAX_ROOM_LENGTH} characters.")
    return room


def _date_in_reach(day):
    today = local_now().date()
    if day < today - timedelta(days=MAX_DAYS_AHEAD) or day > today + timedelta(days=MAX_DAYS_AHEAD):
        raise RoutineError(f"Choose a date within {MAX_DAYS_AHEAD} days of today.")


def date_clashes(day, start, end, teachers, room, semester, group, exclude_key=None):
    """Problems with a class at start..end on `day`: a teacher or room already in use then, or the
    class (or the same group) already having a class then. Checked against every class's routine that day."""
    problems = []
    teacher_ids = {t.id for t in teachers}
    for other in occurrences(day, day, include_cancelled=False):
        if other.key == exclude_key or not (start < other.end_time and other.start_time < end):
            continue
        where = (f"{clock(other.start_time)}-{clock(other.end_time)} {entry_label(other.subject, other.title, other.group)}"
                 f" ({other.semester.department.display_name} {other.semester.name})")
        busy = [t.name for t in other.teachers if t.id in teacher_ids]
        if busy:
            problems.append(f"{', '.join(busy)} already has {where}.")
        if room and (other.room or '').lower() == room.lower():
            problems.append(f"Room {room} is taken by {where}.")
        if other.semester.id == semester.id and (group is None or other.group is None or other.group.id == group.id):
            problems.append(f"The class already has {where}.")
    return list(dict.fromkeys(problems))


def change_occurrence(occurrence, kind, form, user):
    """Make a change to one occurrence. Returns the RoutineChange (added, not committed). Raises RoutineError."""
    if kind not in allowed_kinds(occurrence):
        raise RoutineError("This class cannot be changed that way (it may have been held, changed already, or "
                           "be outside what you may change).")
    reason = _reason(form)
    entry = occurrence.entry
    change = RoutineChange(kind=kind, timetable_entry_id=entry.id, date=occurrence.original_day, reason=reason,
                           department_id=occurrence.semester.department_id, semester_id=occurrence.semester.id,
                           subject_id=entry.subject_id, title=entry.title, group_id=entry.group_id,
                           created_by_user_id=user.id)
    if kind == RoutineChange.MOVE:
        new_day = parse_day(form.get('new_date')) or occurrence.original_day
        start, end = parse_clock(form.get('new_start_time')), parse_clock(form.get('new_end_time'))
        if not start or not end or start >= end:
            raise RoutineError("Enter the new start and end times; it must end after it starts.")
        _date_in_reach(new_day)
        if datetime.combine(new_day, end) <= local_now():
            raise RoutineError("That time has already passed. Choose a time that is still to come.")
        holiday = holiday_on(holidays_between(occurrence.semester.department_id, new_day, new_day), new_day)
        if holiday is not None:
            raise RoutineError(f"{new_day:%d %b %Y} is a holiday ({holiday.title}). Choose another date.")
        change.new_date, change.new_start_time, change.new_end_time = new_day, start, end
        change.room = _room(form)
        problems = date_clashes(new_day, start, end, occurrence.teachers, change.room or occurrence.room,
                                occurrence.semester, occurrence.group, exclude_key=occurrence.key)
        if problems:
            raise RoutineError(' '.join(problems))
    elif kind == RoutineChange.SUBSTITUTE:
        substitute = db.session.get(Teacher, form.get('substitute_teacher_id', type=int) or 0)
        if substitute is None or not can_access_department(substitute.department_id):
            raise RoutineError("Choose the teacher who takes the class.")
        if any(t.id == substitute.id for t in occurrence.teachers):
            raise RoutineError(f"{substitute.name} already teaches this class.")
        change.substitute_teacher_id = substitute.id
        change.room = _room(form)
        problems = date_clashes(occurrence.day, occurrence.start_time, occurrence.end_time, [substitute], None,
                                occurrence.semester, occurrence.group, exclude_key=occurrence.key)
        problems = [p for p in problems if substitute.name in p]
        if problems:
            raise RoutineError(' '.join(problems))
    db.session.add(change)
    return change


def add_extra_class(semester, form, user):
    """An extra class on one date (not in the routine). Returns the RoutineChange. Raises RoutineError."""
    if not is_manager_of(semester):
        raise RoutineError("Only the HOD or Principal can add an extra class.")
    if semester_lock_error(semester):
        raise RoutineError(semester_lock_error(semester))
    reason = _reason(form)
    day = parse_day(form.get('date'))
    start, end = parse_clock(form.get('start_time')), parse_clock(form.get('end_time'))
    if not day or not start or not end or start >= end:
        raise RoutineError("Enter the date and the start and end times; it must end after it starts.")
    _date_in_reach(day)
    subject = db.session.get(Subject, form.get('subject_id', type=int) or 0)
    if subject is not None and subject.semester_id != semester.id:
        raise RoutineError("Choose a subject of this class.")
    title = normalize_text(form.get('title')) or None
    if subject is None and not title:
        raise RoutineError("Choose a subject, or enter a title.")
    group = db.session.get(ClassGroup, form.get('group_id', type=int) or 0)
    if group is not None and group.semester_id != semester.id:
        raise RoutineError("Choose a group of this class.")
    teacher = db.session.get(Teacher, form.get('teacher_id', type=int) or 0)
    if teacher is not None and teacher.department_id != semester.department_id:
        raise RoutineError("Choose a teacher of this department.")
    teachers = [teacher] if teacher else ([t for t in [subject.teacher, *list(subject.co_teachers)] if t] if subject else [])
    room = _room(form)
    problems = date_clashes(day, start, end, teachers, room, semester, group)
    if problems:
        raise RoutineError(' '.join(problems))
    change = RoutineChange(kind=RoutineChange.EXTRA, date=day, new_start_time=start, new_end_time=end,
                           substitute_teacher_id=teacher.id if teacher else None, department_id=semester.department_id,
                           semester_id=semester.id, subject_id=subject.id if subject else None,
                           title=None if subject else title, group_id=group.id if group else None, room=room,
                           reason=reason, created_by_user_id=user.id)
    db.session.add(change)
    return change


def describe_change(change, occurrence_label):
    if change.kind == RoutineChange.CANCEL:
        return f"Cancelled {occurrence_label} on {change.date:%d %b %Y}. Reason: {change.reason}"
    if change.kind == RoutineChange.MOVE:
        return (f"Moved {occurrence_label} of {change.date:%d %b %Y} to {change.new_date:%d %b %Y} "
                f"{clock(change.new_start_time)}-{clock(change.new_end_time)}. Reason: {change.reason}")
    if change.kind == RoutineChange.SUBSTITUTE:
        name = change.substitute.name if change.substitute else 'a substitute'
        return f"{name} takes {occurrence_label} on {change.date:%d %b %Y}. Reason: {change.reason}"
    return (f"Added an extra class {occurrence_label} on {change.date:%d %b %Y} "
            f"{clock(change.new_start_time)}-{clock(change.new_end_time)}. Reason: {change.reason}")

