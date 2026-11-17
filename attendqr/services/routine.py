"""The class routine (timetable): bell schedules, holidays, versions, entries and clash checks.

- **Bell schedules** give a day its periods. A department's own schedule for a weekday is
  used before the institution's.
- **Versions.** A class has published versions with dates that never overlap, and at most one
  draft. Publishing a draft from date D ends the previous version on D - 1. Published
  versions are never edited: "Edit" makes a draft copy. So every past week keeps the routine
  it really had.
- **Week numbers** for odd/even entries: week 1 is the week holding the version's first day;
  weeks begin on the "First day of the week" setting.
- **Clash checks** run when an entry is saved and again when a draft is published.
"""
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from itertools import pairwise

from sqlalchemy import or_

from ..access import SPECIAL_CLASS_SUBJECT_NAME, teacher_short_name
from ..extensions import db
from ..models import (
    BellPeriod,
    BellSchedule,
    BellScheduleDay,
    ClassGroup,
    ClassGroupMember,
    Holiday,
    Subject,
    Teacher,
    Timetable,
    TimetableEntry,
)
from ..utils import local_now, normalize_text, utc_now
from .settings import WEEKDAYS, get_setting

MAX_PERIODS = 16
MAX_TITLE_LENGTH = 100
MAX_ROOM_LENGTH = 40
MAX_NOTE_LENGTH = 200
PATTERNS = {'every': 'Every week', 'odd': 'Odd weeks', 'even': 'Even weeks'}
DEFAULT_DAY_START, DEFAULT_DAY_END = time(9, 0), time(17, 0)


class RoutineError(ValueError):
    pass


def minutes(value):
    return value.hour * 60 + value.minute


def clock(value):
    """"10:50" for a time (24-hour, as the time inputs use)."""
    return value.strftime('%H:%M') if value else ''


def clock_12h(value):
    return value.strftime('%I:%M %p').lstrip('0') if value else ''


def parse_clock(value):
    """A time from "HH:MM" (or "H:MM"), or None."""
    text = (value or '').strip()
    if not re.fullmatch(r'\d{1,2}:\d{2}', text):
        return None
    hours, mins = (int(part) for part in text.split(':'))
    if hours > 23 or mins > 59:
        return None
    return time(hours, mins)


def parse_day(value):
    text = (value or '').strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, '%Y-%m-%d').date()
    except ValueError:
        raise RoutineError(f"{text} is not a date.") from None


# ── Bell schedules ───────────────────────────────────────────

def visible_schedules(department_id):
    """The institution's schedules, then the department's own (or every department's for None and all=True)."""
    query = BellSchedule.query.filter(or_(BellSchedule.department_id.is_(None),
                                          BellSchedule.department_id == department_id))
    return sorted(query.all(), key=lambda s: (s.department_id is not None, s.name.lower()))


def schedule_for_day(department_id, weekday):
    """The schedule a class of the department uses on a weekday: its own, else the institution's, else None."""
    rows = (
        db.session.query(BellSchedule)
        .join(BellScheduleDay, BellScheduleDay.schedule_id == BellSchedule.id)
        .filter(BellScheduleDay.weekday == weekday,
                or_(BellSchedule.department_id.is_(None), BellSchedule.department_id == department_id))
        .all()
    )
    own = [s for s in rows if s.department_id is not None]
    return (own or rows or [None])[0]


def week_periods(department_id):
    """{weekday: [periods]} for the department, for all seven days (empty where no schedule is set)."""
    result = {}
    for weekday in range(7):
        schedule = schedule_for_day(department_id, weekday)
        result[weekday] = list(schedule.periods) if schedule else []
    return result


def parse_periods(labels, starts, ends, breaks):
    """Validated periods from the schedule form's parallel lists; empty rows are skipped.

    Returns [(label, start, end, is_break)] sorted by start. Raises RoutineError.
    """
    periods = []
    for index, (label, start_text, end_text) in enumerate(zip(labels, starts, ends, strict=False)):
        label = normalize_text(label)
        if not (label or start_text.strip() or end_text.strip()):
            continue
        start, end = parse_clock(start_text), parse_clock(end_text)
        if not start or not end:
            raise RoutineError(f"Row {index + 1}: enter a start and an end time.")
        if start >= end:
            raise RoutineError(f"Row {index + 1}: the period must end after it starts.")
        periods.append((label[:20] or str(len(periods) + 1), start, end, str(index) in breaks))
    if not periods:
        raise RoutineError("Add at least one period.")
    if len(periods) > MAX_PERIODS:
        raise RoutineError(f"A schedule can have at most {MAX_PERIODS} periods.")
    periods.sort(key=lambda p: p[1])
    for before, after in pairwise(periods):
        if after[1] < before[2]:
            raise RoutineError(f"{before[0]} ({clock(before[1])}-{clock(before[2])}) overlaps {after[0]} "
                               f"({clock(after[1])}-{clock(after[2])}).")
    return periods


def save_schedule(schedule, name, periods, weekdays):
    """Replace the schedule's periods and days. A weekday given here leaves any other schedule of the same scope."""
    name = normalize_text(name)
    if not name:
        raise RoutineError("Give the schedule a name, such as Weekday or Saturday half-day.")
    schedule.name = name[:60]
    schedule.periods = [BellPeriod(position=i + 1, label=label, start_time=start, end_time=end, is_break=is_break)
                        for i, (label, start, end, is_break) in enumerate(periods)]
    db.session.flush()
    same_scope = BellSchedule.query.filter(
        BellSchedule.id != schedule.id,
        BellSchedule.department_id.is_(None) if schedule.department_id is None
        else BellSchedule.department_id == schedule.department_id,
    ).with_entities(BellSchedule.id)
    BellScheduleDay.query.filter(BellScheduleDay.schedule_id.in_(same_scope),
                                 BellScheduleDay.weekday.in_(sorted(weekdays))).delete(synchronize_session=False)
    schedule.days = [BellScheduleDay(weekday=day) for day in sorted(weekdays)]


# ── Holidays ─────────────────────────────────────────────────

def holidays_between(department_id, start, end):
    """Holidays for everyone and for the department that touch start..end."""
    return Holiday.query.filter(
        or_(Holiday.department_id.is_(None), Holiday.department_id == department_id),
        Holiday.start_date <= end, Holiday.end_date >= start,
    ).order_by(Holiday.start_date).all()


def holiday_on(holidays, day):
    return next((h for h in holidays if h.start_date <= day <= h.end_date), None)


# ── Week numbers ─────────────────────────────────────────────

def week_start(day):
    return day - timedelta(days=(day.weekday() - get_setting('week_starts_on')) % 7)


def week_number(first_day, day):
    """Week 1 is the week holding the version's first day."""
    return (week_start(day) - week_start(first_day)).days // 7 + 1


def runs_in_week(pattern, number):
    return pattern == 'every' or (number % 2 == 1) == (pattern == 'odd')


# ── Versions ─────────────────────────────────────────────────

def class_name(semester):
    return f"{semester.department.display_name} - {semester.name}"


def published_versions(semester_id):
    """Newest first."""
    return (Timetable.query.filter_by(semester_id=semester_id, status=Timetable.PUBLISHED)
            .order_by(Timetable.effective_from.desc()).all())


def draft_for(semester_id):
    return Timetable.query.filter_by(semester_id=semester_id, status=Timetable.DRAFT).first()


def version_on(semester_id, day):
    """The published version in force on `day`, if any."""
    return Timetable.query.filter(
        Timetable.semester_id == semester_id, Timetable.status == Timetable.PUBLISHED,
        Timetable.effective_from <= day,
        or_(Timetable.effective_to.is_(None), Timetable.effective_to >= day),
    ).first()


def copy_entry(entry, timetable, subject=..., group=..., teachers=None):
    """A copy of `entry` in `timetable`; subject, group and teachers can be replaced (None clears them)."""
    copied = TimetableEntry(
        timetable=timetable, weekday=entry.weekday, start_time=entry.start_time, end_time=entry.end_time,
        subject=entry.subject if subject is ... else subject, title=entry.title,
        group=entry.group if group is ... else group, room=entry.room, week_pattern=entry.week_pattern,
        valid_from=entry.valid_from, valid_to=entry.valid_to, note=entry.note,
    )
    db.session.add(copied)  # before loading the teachers, which flushes
    copied.teachers = list(entry.teachers if teachers is None else teachers)
    return copied


def start_draft(semester, user):
    """The class's draft: the one being edited, or a new copy of the latest published version (or empty)."""
    draft = draft_for(semester.id)
    if draft:
        return draft, False
    draft = Timetable(department_id=semester.department_id, semester_id=semester.id, status=Timetable.DRAFT,
                      created_by_user_id=user.id, created_at=utc_now())
    db.session.add(draft)
    latest = (published_versions(semester.id) or [None])[0]
    if latest:
        for entry in latest.entries:
            copy_entry(entry, draft)
    db.session.flush()
    return draft, True


def latest_start(semester_id):
    latest = (published_versions(semester_id) or [None])[0]
    return latest.effective_from if latest else None


def publish(draft, effective_from, user):
    """Publish the draft from `effective_from`. Raises RoutineError (with every problem) and changes nothing on failure.

    Returns the version it replaced, if any (now ending the day before).
    """
    problems = []
    if not draft.entries:
        problems.append("The draft has no classes yet.")
    unassigned = [e for e in draft.entries if e.is_unassigned]
    if unassigned:
        problems.append(f"{len(unassigned)} class(es) have no subject or title yet (copied from another class). "
                        "Assign or delete them first.")
    previous = (published_versions(draft.semester_id) or [None])[0]
    if previous and effective_from <= previous.effective_from:
        problems.append(f"The new routine must start after the current one ({previous.effective_from:%d %b %Y}).")
    elif previous and effective_from < local_now().date():
        # It would end the current version in the past and rewrite weeks that already happened:
        # classes held under the old routine would show as not recorded. The first routine may
        # start in the past, since no earlier routine is replaced.
        problems.append("A new version of a published routine cannot start in the past: weeks already "
                        "taught keep the routine they had. Choose today or a later date.")
    # Checked with its real first week, so odd and even weeks line up with other classes' routines.
    # On failure the caller rolls back, so this never sticks.
    draft.effective_from = effective_from
    for entry in draft.entries:
        if entry.is_unassigned:
            continue
        errors, _ = clashes(draft, entry_values_of(entry), exclude_id=entry.id)
        problems += [f"{describe_entry(entry)}: {error}" for error in errors]
    if problems:
        raise RoutineError(' '.join(dict.fromkeys(problems)))
    if previous:
        previous.effective_to = effective_from - timedelta(days=1)
    draft.status = Timetable.PUBLISHED
    draft.effective_from = effective_from
    draft.published_at = utc_now()
    draft.published_by_user_id = user.id
    return previous


# ── Entries ──────────────────────────────────────────────────

@dataclass
class EntryValues:
    weekday: int
    start: time
    end: time
    subject: Subject | None = None
    title: str | None = None
    group: ClassGroup | None = None
    teachers: list = field(default_factory=list)
    room: str | None = None
    pattern: str = 'every'
    valid_from: date | None = None
    valid_to: date | None = None
    note: str | None = None


def entry_values_of(entry):
    return EntryValues(entry.weekday, entry.start_time, entry.end_time, entry.subject, entry.title, entry.group,
                       list(entry.teachers), entry.room, entry.week_pattern, entry.valid_from, entry.valid_to,
                       entry.note)


def entry_label(subject, title, group):
    name = (subject.paper_code or subject.name) if subject else (title or 'Unassigned')
    return f"{name} ({group.name})" if group else name


def describe_entry(entry, with_class=False):
    text = (f"{WEEKDAYS[entry.weekday][:3]} {clock(entry.start_time)}-{clock(entry.end_time)} "
            f"{entry_label(entry.subject, entry.title, entry.group)}")
    if with_class:
        text += f" in {class_name(entry.timetable.semester)}"
    return text


def entry_teachers_label(entry):
    """"MG+SC" for the grid."""
    return '+'.join(teacher_short_name(t) for t in entry.teachers)


def parse_entry_form(form, timetable):
    """Validated EntryValues from the entry dialog. Raises RoutineError."""
    weekday = form.get('weekday', type=int)
    if weekday is None or not 0 <= weekday <= 6:
        raise RoutineError("Choose a day.")
    start, end = parse_clock(form.get('start_time')), parse_clock(form.get('end_time'))
    if not start or not end:
        raise RoutineError("Enter a start and an end time.")
    if start >= end:
        raise RoutineError("The class must end after it starts.")

    subject = None
    subject_id = form.get('subject_id', type=int)
    if subject_id:
        subject = db.session.get(Subject, subject_id)
        if (not subject or subject.semester_id != timetable.semester_id
                or subject.name == SPECIAL_CLASS_SUBJECT_NAME):
            raise RoutineError("Choose a subject of this class.")
    title = normalize_text(form.get('title')) or None
    if not subject and not title:
        raise RoutineError('Choose a subject, or enter a title such as "Special Tutorial Class".')
    if title and len(title) > MAX_TITLE_LENGTH:
        raise RoutineError(f"Keep the title under {MAX_TITLE_LENGTH} characters.")

    group = None
    group_id = form.get('group_id', type=int)
    if group_id:
        group = db.session.get(ClassGroup, group_id)
        if not group or group.semester_id != timetable.semester_id:
            raise RoutineError("Choose a group of this class.")

    teacher_ids = set(form.getlist('teacher_ids', type=int))
    teachers = Teacher.query.filter(Teacher.id.in_(sorted(teacher_ids))).order_by(Teacher.name).all()
    if len(teachers) != len(teacher_ids) or any(t.department_id != timetable.department_id for t in teachers):
        raise RoutineError("Choose teachers of this department.")
    if not teacher_ids and subject:
        teachers = [t for t in [subject.teacher, *list(subject.co_teachers)] if t]

    room = normalize_text(form.get('room')) or None
    if room and len(room) > MAX_ROOM_LENGTH:
        raise RoutineError(f"Keep the room under {MAX_ROOM_LENGTH} characters.")
    pattern = form.get('week_pattern') or 'every'
    if pattern not in PATTERNS:
        raise RoutineError("Choose how often it repeats.")
    valid_from, valid_to = parse_day(form.get('valid_from')), parse_day(form.get('valid_to'))
    if valid_from and valid_to and valid_from > valid_to:
        raise RoutineError("The last date cannot be before the first.")
    note = normalize_text(form.get('note')) or None
    if note and len(note) > MAX_NOTE_LENGTH:
        raise RoutineError(f"Keep the note under {MAX_NOTE_LENGTH} characters.")
    return EntryValues(weekday, start, end, subject, title if not subject else None, group, teachers, room, pattern,
                       valid_from, valid_to, note)


def apply_entry(entry, values):
    entry.weekday, entry.start_time, entry.end_time = values.weekday, values.start, values.end
    entry.subject, entry.title, entry.group = values.subject, values.title, values.group
    entry.teachers = list(values.teachers)
    entry.room, entry.week_pattern = values.room, values.pattern
    entry.valid_from, entry.valid_to, entry.note = values.valid_from, values.valid_to, values.note


# ── Clash checks ─────────────────────────────────────────────

def _times_overlap(a_start, a_end, b_start, b_end):
    return a_start < b_end and b_start < a_end


def _dates_overlap(a_from, a_to, b_from, b_to):
    return (a_from is None or b_to is None or a_from <= b_to) and (b_from is None or a_to is None or b_from <= a_to)


def _weeks_meet(pattern_a, first_a, pattern_b, first_b):
    """Whether two entries can fall in the same week. Within one version odd never meets even; across
    versions their week 1s may differ, so parity is compared only when both start dates are known."""
    if 'every' in (pattern_a, pattern_b):
        return True
    if first_a is None and first_b is None:
        return pattern_a == pattern_b  # both in the same draft: one week 1
    if first_a is None or first_b is None:
        return True  # a draft against a published routine: unknown until it is published, so assume they meet
    weeks_apart = (week_start(first_a) - week_start(first_b)).days // 7
    return (pattern_a == pattern_b) == (weeks_apart % 2 == 0)


def _active_versions_of_other_classes(timetable, today):
    """Published versions of other classes that run on or after today."""
    return Timetable.query.filter(
        Timetable.status == Timetable.PUBLISHED, Timetable.semester_id != timetable.semester_id,
        or_(Timetable.effective_to.is_(None), Timetable.effective_to >= today),
    ).all()


def _shared_members(group_a, group_b):
    members = db.session.query(ClassGroupMember.student_id).filter(
        ClassGroupMember.left_at.is_(None))
    a = {s for (s,) in members.filter(ClassGroupMember.group_id == group_a.id)}
    b = {s for (s,) in members.filter(ClassGroupMember.group_id == group_b.id)}
    return len(a & b)


def clashes(timetable, values, exclude_id=None, today=None):
    """(errors, warnings) for placing `values` in `timetable`.

    Errors: the same teacher or room twice at once (here or in another class's current routine),
    a whole-class entry beside anything else of the class, or one group twice. Warning: two
    different groups that share members (the HOD may know they never both attend).
    """
    today = today or local_now().date()
    errors, warnings = [], []
    teacher_ids = {t.id for t in values.teachers}
    room = (values.room or '').lower()
    candidates = [(e, timetable.effective_from) for e in timetable.entries if e.id != exclude_id]
    for version in _active_versions_of_other_classes(timetable, today):
        candidates += [(e, version.effective_from) for e in version.entries]

    for other, other_first in candidates:
        if other.weekday != values.weekday or not _times_overlap(values.start, values.end, other.start_time,
                                                                 other.end_time):
            continue
        if not _dates_overlap(values.valid_from, values.valid_to, other.valid_from, other.valid_to):
            continue
        if not _weeks_meet(values.pattern, timetable.effective_from, other.week_pattern, other_first):
            continue
        same_class = other.timetable_id == timetable.id
        where = describe_entry(other, with_class=not same_class)
        shared_teachers = [t for t in other.teachers if t.id in teacher_ids]
        if shared_teachers:
            errors.append(f"{', '.join(t.name for t in shared_teachers)} also teaches {where}.")
        if room and (other.room or '').lower() == room:
            errors.append(f"Room {values.room} is taken by {where}.")
        if not same_class:
            continue
        if values.group is None or other.group is None:
            errors.append(f"It overlaps {where}, and a whole-class class cannot share its time.")
        elif values.group.id == other.group.id:
            errors.append(f"{values.group.name} already has {where}.")
        else:
            shared = _shared_members(values.group, other.group)
            if shared:
                warnings.append(f"{shared} student(s) are in both {values.group.name} and {other.group.name}, "
                                f"who has {where}.")
    return list(dict.fromkeys(errors)), list(dict.fromkeys(warnings))


# ── Copying ──────────────────────────────────────────────────

def copy_day(draft, from_day, to_day):
    """Copy one day's classes to another day. Returns (copied, skipped because they clash there)."""
    copied = skipped = 0
    for entry in [e for e in draft.entries if e.weekday == from_day]:
        values = entry_values_of(entry)
        values.weekday = to_day
        errors, _ = clashes(draft, values)
        if errors:
            skipped += 1
            continue
        new = copy_entry(entry, draft)
        new.weekday = to_day
        db.session.flush()
        copied += 1
    return copied, skipped


def copy_from(draft, source):
    """Replace the draft's classes with `source`'s.

    From the same class: an exact copy. From another class: subjects are matched by paper code and
    groups by name; classes with no match stay as unassigned entries (times, room and repeats kept).
    Returns the number of unassigned entries.
    """
    for entry in list(draft.entries):
        draft.entries.remove(entry)
    db.session.flush()
    same_class = source.semester_id == draft.semester_id
    subjects = {(s.paper_code or '').upper(): s for s in Subject.query.filter_by(semester_id=draft.semester_id)
                if s.paper_code}
    groups = {g.name.lower(): g for g in ClassGroup.query.filter_by(semester_id=draft.semester_id)}
    unassigned = 0
    for entry in source.entries:
        if same_class:
            copy_entry(entry, draft)
            continue
        subject = subjects.get((entry.subject.paper_code or '').upper()) if entry.subject else None
        group = groups.get(entry.group.name.lower()) if entry.group else None
        if entry.subject and not subject:
            new = copy_entry(entry, draft, subject=None, group=group, teachers=[])
            new.title = None
            unassigned += 1
        else:
            teachers = [t for t in [subject.teacher, *subject.co_teachers] if t] if subject else []
            copy_entry(entry, draft, subject=subject, group=group, teachers=teachers)
    db.session.flush()
    return unassigned


# ── The week grid ────────────────────────────────────────────

def _lanes(items):
    """Side-by-side lanes for overlapping items (start, end, payload): [(payload, lane, lanes)]."""
    cluster, cluster_end, lane_ends = [], -1, []
    result = []

    def close():
        width = max((lane for _, lane in cluster), default=-1) + 1
        result.extend((payload, lane, width) for payload, lane in cluster)

    for start, end, payload in sorted(items, key=lambda i: (i[0], i[1])):
        if start >= cluster_end and cluster:
            close()
            cluster, lane_ends = [], []
        lane = next((i for i, lane_end in enumerate(lane_ends) if lane_end <= start), None)
        if lane is None:
            lane = len(lane_ends)
            lane_ends.append(end)
        else:
            lane_ends[lane] = end
        cluster.append((payload, lane))
        cluster_end = max(cluster_end, end)
    if cluster:
        close()
    return result


def week_grid(entries, department_id, days=None, keep_empty_days=False):
    """Positions for drawing a week: a row per day, time from left to right, like a printed routine.

    `start`/`length` are percentages of the day along the time axis. Parallel classes (two lab
    batches) share the slot in lanes: `lane_start`/`lane_size` are percentages of the row's height,
    and each day's `lanes` (its most parallel classes) lets the page give that row more height.

    `entries` are TimetableEntry-like objects with weekday, start_time and end_time. Days default
    to the working days plus any day that has entries; a working day with neither periods nor
    classes is left out unless keep_empty_days (the editor keeps it, to add classes there).
    """
    periods = week_periods(department_id)
    if days is None:
        days = sorted(set(get_setting('working_days')) | {e.weekday for e in entries})
    if not keep_empty_days:
        used = {e.weekday for e in entries}
        days = [d for d in days if periods[d] or d in used] or list(days)
    starts = [p.start_time for d in days for p in periods[d]] + [e.start_time for e in entries]
    ends = [p.end_time for d in days for p in periods[d]] + [e.end_time for e in entries]
    first = (min(minutes(t) for t in starts) // 30) * 30 if starts else minutes(DEFAULT_DAY_START)
    last = -(-max(minutes(t) for t in ends) // 30) * 30 if ends else minutes(DEFAULT_DAY_END)
    last = max(last, first + 60)
    span = last - first

    def pct(value):
        return round((minutes(value) - first) / span * 100, 3)

    rows = []
    by_day = defaultdict(list)
    for entry in entries:
        by_day[entry.weekday].append(entry)
    for day in days:
        bands = [{'period': p, 'start': pct(p.start_time), 'length': round(pct(p.end_time) - pct(p.start_time), 3)}
                 for p in periods[day]]
        placed = _lanes([(minutes(e.start_time), minutes(e.end_time), e) for e in by_day[day]])
        blocks = [{'entry': entry, 'start': pct(entry.start_time),
                   'length': round(pct(entry.end_time) - pct(entry.start_time), 3),
                   'lane_start': round(lane / lanes * 100, 3), 'lane_size': round(100 / lanes, 3)}
                  for entry, lane, lanes in placed]
        rows.append({'weekday': day, 'name': WEEKDAYS[day], 'bands': bands,
                     'lanes': max((lanes for _, _, lanes in placed), default=1),
                     'blocks': sorted(blocks, key=lambda b: (b['start'], b['lane_start']))})
    hours = [{'label': f"{(m // 60) % 12 or 12}{'am' if m < 720 else 'pm'}", 'at': round((m - first) / span * 100, 3)}
             for m in range(-(-first // 60) * 60, last + 1, 60)]
    # Minutes covered, so the page can give a long day more width.
    return {'rows': rows, 'hours': hours, 'span': span}


def print_table(entries, department_id, days):
    """The printed layout: days as rows, the periods of a shared schedule as columns.

    Returns None when the days do not all use the same periods (print the week grid instead).
    Each row is a list of cells: {'entries': [...], 'span': n} (span 0 = covered by a cell to its left).
    """
    periods = week_periods(department_id)
    used = {e.weekday for e in entries}
    days = [d for d in days if periods[d] or d in used]  # a working day without periods or classes says nothing
    if not days:
        return None
    shapes = {tuple((p.start_time, p.end_time) for p in periods[d]) for d in days}
    if len(shapes) != 1 or not next(iter(shapes)):
        return None
    columns = periods[days[0]]
    rows = []
    for day in days:
        todays = [e for e in entries if e.weekday == day]
        cells, skip = [], 0
        for index, period in enumerate(columns):
            if skip:
                cells.append({'entries': [], 'span': 0})
                skip -= 1
                continue
            starting = [e for e in todays if period.start_time <= e.start_time < period.end_time]
            covering = [e for e in todays if e.start_time < period.start_time < e.end_time]
            span = 1
            if starting and not covering:
                last_end = max(e.end_time for e in starting)
                span = sum(1 for p in columns[index:] if p.start_time < last_end) or 1
                inside = [e for e in todays if e not in starting
                          and any(p.start_time <= e.start_time < p.end_time for p in columns[index + 1:index + span])]
                if inside:
                    span = 1
            cells.append({'entries': starting or covering, 'span': span, 'period': period})
            skip = span - 1
        rows.append({'weekday': day, 'name': WEEKDAYS[day], 'cells': cells})
    return {'periods': columns, 'rows': rows}
