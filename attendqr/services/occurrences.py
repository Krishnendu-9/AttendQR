"""Dated classes from the routine, and the sessions that hold them.

occurrences() is the one function every routine view uses. It expands the published versions
of a class's routine into dated classes between two days, applying the working days,
holidays, odd and even weeks, each entry's own date range, and the changes for one date
(cancelled, moved, taken by a substitute, extra classes). For a student it keeps only
whole-class classes and the groups they belonged to at the time; for a teacher, the classes
they teach that day (including as a substitute).

The rule that never changes: a class in the routine that nobody recorded a session for never
enters any student's percentage. Only sessions count (services/reports.py).
"""
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy import or_, update

from ..extensions import db
from ..models import ClassSession, Holiday, RoutineChange, Timetable, TimetableEntry
from ..utils import local_now, local_to_utc, to_local
from .groups import GroupRosters
from .reports import attendance_counts
from .routine import runs_in_week, week_number
from .settings import get_setting

# A session can be started from the routine this long before the class begins.
START_EARLY = timedelta(minutes=15)
# Moves are looked for this far around the days asked for, so a class moved in from outside them shows.
MOVE_REACH = timedelta(days=14)

UPCOMING, NOW, HELD, NOT_RECORDED, CANCELLED = 'upcoming', 'now', 'held', 'not_recorded', 'cancelled'
STATUS_LABELS = {UPCOMING: 'Upcoming', NOW: 'Now', HELD: 'Held', NOT_RECORDED: 'Not recorded', CANCELLED: 'Cancelled'}
STATUS_TONES = {UPCOMING: 'neutral', NOW: 'info', HELD: 'success', NOT_RECORDED: 'warning', CANCELLED: 'neutral'}


@dataclass
class Occurrence:
    """One class on one date. It reads like a TimetableEntry, so the week grid can draw it."""
    entry: TimetableEntry | None   # None for an extra class
    day: date                      # when it happens (a moved class: its new date)
    start_time: time
    end_time: time
    original_day: date             # its date in the routine; sessions and changes are keyed by it
    teachers: list = field(default_factory=list)
    room: str | None = None
    status: str = UPCOMING
    session: ClassSession | None = None
    change: Any = None             # the RoutineChange for this date, if any
    holiday: Holiday | None = None

    week_pattern = 'every'
    is_unassigned = False

    @property
    def extra(self):
        return self.change if self.entry is None else None

    def _source(self) -> Any:
        return self.entry if self.entry is not None else self.change

    @property
    def subject(self):
        return self._source().subject

    @property
    def title(self):
        return self._source().title

    @property
    def group(self):
        return self._source().group

    @property
    def semester(self):
        return self.entry.timetable.semester if self.entry is not None else self.change.semester

    @property
    def timetable(self):
        """For the grid's class line: something with a .semester."""
        return self.entry.timetable if self.entry is not None else self.change

    @property
    def weekday(self):
        return self.day.weekday()

    @property
    def starts_at(self):
        return datetime.combine(self.day, self.start_time)

    @property
    def ends_at(self):
        return datetime.combine(self.day, self.end_time)

    @property
    def starts_at_utc(self):
        return local_to_utc(self.starts_at)

    @property
    def status_label(self):
        if self.change is not None and self.change.kind == RoutineChange.MOVE and self.status != CANCELLED:
            return f"Moved from {self.original_day:%a %d %b}" if self.day != self.original_day else "Moved"
        if self.change is not None and self.change.kind == RoutineChange.SUBSTITUTE:
            return f"{STATUS_LABELS[self.status]} · substitute"
        if self.change is not None and self.change.kind == RoutineChange.EXTRA:
            return f"{STATUS_LABELS[self.status]} · extra class"
        if self.holiday is not None:
            return f"Holiday: {self.holiday.title}"
        return STATUS_LABELS.get(self.status, '')

    @property
    def status_tone(self):
        return STATUS_TONES.get(self.status, 'neutral')

    @property
    def key(self):
        """A stable id for forms: "entry-<entry id>-<yyyy-mm-dd>" or "extra-<change id>"."""
        if self.entry is None:
            return f"extra-{self.change.id}"
        return f"entry-{self.entry.id}-{self.original_day.isoformat()}"

    def can_start(self, now=None):
        now = now or local_now()
        return (self.session is None and self.status != CANCELLED
                and self.starts_at - START_EARLY <= now < self.ends_at)


def _days(start, end):
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)


def _routine_occurrences(versions, first_day, last_day, holidays, working):
    found = []
    for version in versions:
        by_weekday = defaultdict(list)
        for entry in version.entries:
            by_weekday[entry.weekday].append(entry)
        start = max(first_day, version.effective_from)
        end = min(last_day, version.effective_to or last_day)
        for day in _days(start, end):
            todays = by_weekday.get(day.weekday())
            if not todays or day.weekday() not in working:
                continue
            holiday = _holiday_for(holidays, day, version.department_id)
            number = week_number(version.effective_from, day)
            for entry in todays:
                if (entry.valid_from and day < entry.valid_from) or (entry.valid_to and day > entry.valid_to):
                    continue
                if not runs_in_week(entry.week_pattern, number):
                    continue
                occurrence = Occurrence(entry=entry, day=day, start_time=entry.start_time, end_time=entry.end_time,
                                        original_day=day, teachers=list(entry.teachers), room=entry.room)
                if holiday is not None:
                    occurrence.status, occurrence.holiday = CANCELLED, holiday
                found.append(occurrence)
    return found


def _holiday_for(holidays, day, department_id):
    return next((h for h in holidays if h.start_date <= day <= h.end_date
                 and h.department_id in (None, department_id)), None)


def _apply_changes(found, first_day, last_day, semester_ids, holidays):
    """Apply cancellations, moves and substitutes, and add extra classes.

    A class moved to another date takes that date's holiday, if one was declared after the move.
    """
    query = RoutineChange.query.filter(
        or_(RoutineChange.date.between(first_day, last_day), RoutineChange.new_date.between(first_day, last_day)))
    if semester_ids is not None:
        query = query.filter(RoutineChange.semester_id.in_(sorted(semester_ids)))
    changes = query.all()
    by_occurrence = {(c.timetable_entry_id, c.date): c for c in changes if c.kind != RoutineChange.EXTRA}
    for occurrence in found:
        change = by_occurrence.get((occurrence.entry.id, occurrence.original_day))
        if change is None:
            continue
        occurrence.change = change
        if change.kind == RoutineChange.CANCEL:
            occurrence.status = CANCELLED
        elif change.kind == RoutineChange.MOVE:
            occurrence.day = change.new_date or occurrence.day
            occurrence.start_time = change.new_start_time or occurrence.start_time
            occurrence.end_time = change.new_end_time or occurrence.end_time
            occurrence.room = change.room or occurrence.room
            if occurrence.day != occurrence.original_day:
                holiday = _holiday_for(holidays, occurrence.day, change.department_id)
                # Moved onto a holiday declared since: cancelled. Moved off one: due again.
                occurrence.status, occurrence.holiday = (CANCELLED, holiday) if holiday else (UPCOMING, None)
        elif change.kind == RoutineChange.SUBSTITUTE and change.substitute is not None:
            occurrence.teachers = [change.substitute]
            occurrence.room = change.room or occurrence.room
    for change in changes:
        if change.kind != RoutineChange.EXTRA:
            continue
        teachers = [change.substitute] if change.substitute else (
            [t for t in [change.subject.teacher, *list(change.subject.co_teachers)] if t] if change.subject else [])
        found.append(Occurrence(entry=None, day=change.date, start_time=change.new_start_time,
                                end_time=change.new_end_time, original_day=change.date, teachers=teachers,
                                room=change.room, change=change))
    return found


def occurrences(date_from, date_to, semester_ids=None, teacher_id=None, student=None, now=None,
                include_cancelled=True):
    """The routine's classes between date_from and date_to (both included), by date and start time.

    Narrow it with semester_ids (classes), teacher_id (classes they teach that day, as a
    substitute too) or student (their class's classes, only for their groups). Cancelled
    classes (also those on holidays) come back with status "cancelled" unless include_cancelled
    is False.
    """
    if student is not None:
        semester_ids = [student.semester_id]
    first_day, last_day = date_from - MOVE_REACH, date_to + MOVE_REACH
    query = Timetable.query.filter(
        Timetable.status == Timetable.PUBLISHED, Timetable.effective_from <= last_day,
        or_(Timetable.effective_to.is_(None), Timetable.effective_to >= first_day),
    )
    if semester_ids is not None:
        query = query.filter(Timetable.semester_id.in_(sorted(semester_ids)))
    versions = query.all()
    holidays = Holiday.query.filter(Holiday.start_date <= last_day, Holiday.end_date >= first_day).all()
    found = _routine_occurrences(versions, first_day, last_day, holidays, set(get_setting('working_days')))
    found = _apply_changes(found, first_day, last_day, semester_ids, holidays)
    found = [o for o in found if date_from <= o.day <= date_to]
    if not include_cancelled:
        found = [o for o in found if o.status != CANCELLED]
    if teacher_id is not None:
        found = [o for o in found if any(t.id == teacher_id for t in o.teachers)]
    if student is not None:
        rosters = GroupRosters(o.group.id for o in found if o.group is not None)
        found = [o for o in found if o.group is None or rosters.is_member(student.id, o.group.id, o.starts_at_utc)]
    _attach_sessions(found, now or local_now())
    found.sort(key=lambda o: (o.day, o.start_time, o.semester.name, o.key))
    return found


def _attach_sessions(found, now):
    """Link each occurrence to the session that held it, and work out its status."""
    entry_ids = sorted({o.entry.id for o in found if o.entry is not None})
    days = sorted({o.original_day for o in found if o.entry is not None})
    sessions = {}
    if entry_ids:
        for session in ClassSession.query.filter(ClassSession.timetable_entry_id.in_(entry_ids),
                                                 ClassSession.occurrence_date.in_(days)):
            sessions[(session.timetable_entry_id, session.occurrence_date)] = session
    for occurrence in found:
        if occurrence.entry is not None:
            occurrence.session = sessions.get((occurrence.entry.id, occurrence.original_day))
        else:
            occurrence.session = occurrence.change.session
    counts = attendance_counts([o.session.id for o in found if o.session is not None])
    for occurrence in found:
        if occurrence.status == CANCELLED:
            continue
        session = occurrence.session
        if session is not None and (session.is_active or counts.get(session.id)):
            occurrence.status = HELD  # a session deactivated before anyone marked is void, not held
        elif now < occurrence.starts_at:
            occurrence.status = UPCOMING
        elif now < occurrence.ends_at:
            occurrence.status = NOW
        else:
            occurrence.status = NOT_RECORDED


def find(key, now=None):
    """The occurrence a form names by its key, or None."""
    try:
        kind, rest = key.split('-', 1)
        if kind == 'extra':
            change = db.session.get(RoutineChange, int(rest))
            if change is None or change.kind != RoutineChange.EXTRA:
                return None
            matches = occurrences(change.date, change.date, semester_ids=[change.semester_id], now=now)
        elif kind == 'entry':
            entry_id, day_text = rest.split('-', 1)
            entry = db.session.get(TimetableEntry, int(entry_id))
            if entry is None:
                return None
            original = date.fromisoformat(day_text)
            matches = occurrences(original - MOVE_REACH, original + MOVE_REACH,
                                  semester_ids=[entry.timetable.semester_id], now=now)
        else:
            return None
    except (ValueError, AttributeError):
        return None
    return next((o for o in matches if o.key == key), None)


def matching_occurrence(semester_id, subject_id, group_id, now=None):
    """Today's routine class that a session started by hand holds: same class, subject and group,
    under way (from START_EARLY before it until its end), not cancelled and not yet held."""
    now = now or local_now()
    for occurrence in occurrences(now.date(), now.date(), semester_ids=[semester_id], now=now):
        if (occurrence.subject is not None and occurrence.subject.id == subject_id
                and (occurrence.group.id if occurrence.group else None) == group_id
                and occurrence.session is None and occurrence.can_start(now)):
            return occurrence
    return None


def link_session(class_session, occurrence):
    """Record that the session holds the occurrence. Returns False if another session already does.

    A routine class is held once by its unique (entry, date) index: a second link fails on commit.
    An extra class keeps its session on the change, so it is claimed only while that is still
    empty; otherwise two sessions started at the same moment would each overwrite the other.
    """
    if occurrence.entry is not None:
        class_session.timetable_entry_id = occurrence.entry.id
        class_session.occurrence_date = occurrence.original_day
        return True
    claimed = db.session.execute(
        update(RoutineChange)
        .where(RoutineChange.id == occurrence.change.id, RoutineChange.session_id.is_(None))
        .values(session_id=class_session.id)
        .execution_options(synchronize_session=False)
    ).rowcount
    db.session.expire(occurrence.change, ['session_id'])
    return bool(claimed)


def unscheduled_ids(sessions):
    """Ids of the sessions (from a list) held outside the routine in a class that had a published
    routine that day: they show as "Unscheduled". Classes without a routine never do."""
    unlinked = [s for s in sessions if s.timetable_entry_id is None and s.subject is not None]
    if not unlinked:
        return set()
    extra_held = {session_id for (session_id,) in db.session.query(RoutineChange.session_id).filter(
        RoutineChange.session_id.in_([s.id for s in unlinked]))}
    versions = Timetable.query.filter(Timetable.status == Timetable.PUBLISHED,
                                      Timetable.semester_id.in_(sorted({s.subject.semester_id for s in unlinked}))).all()
    result = set()
    for session in unlinked:
        if session.id in extra_held:
            continue
        day = to_local(session.created_at).date()
        if any(v.semester_id == session.subject.semester_id and v.effective_from <= day
               and (v.effective_to is None or day <= v.effective_to) for v in versions):
            result.add(session.id)
    return result
