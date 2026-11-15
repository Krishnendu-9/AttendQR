"""Sample data for demonstrations: `flask seed-demo`.

Builds a small college (two departments, three classes, about forty students)
with several weeks of past QR sessions, so the dashboard, reports, exports and
shortage list have something to show. Every account shares one demo password
and needs no password change, so anyone trying the demo can sign in at once.

Never run this on a database with real records: `--reset` deletes everything.
"""
import random
import secrets
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta

from flask import current_app
from sqlalchemy.schema import sort_tables
from werkzeug.security import generate_password_hash

from ..access import SPECIAL_CLASS_SUBJECT_NAME
from ..extensions import db
from ..models import (
    ActivityLog,
    Attendance,
    BellPeriod,
    BellSchedule,
    BellScheduleDay,
    ClassGroup,
    ClassGroupMember,
    ClassSession,
    Department,
    Holiday,
    LeaveRequest,
    RoutineChange,
    Semester,
    SpotCheck,
    Student,
    Subject,
    Teacher,
    Timetable,
    TimetableEntry,
    User,
)
from ..schema import discard_used_initial_password
from ..utils import local_now, local_to_utc, normalize_email, to_local, utc_now
from .audit import seal_pending
from .settings import forget_cached

DEFAULT_DEMO_PASSWORD = 'AttendQR@demo1'  # noqa: S105 - published on purpose; demo accounts only
STAFF_DOMAIN = 'college.example'  # .example is reserved (RFC 2606), so no real address is ever used
STUDENT_DOMAIN = 'students.college.example'

# Class periods; a subject's sessions start at one of these.
PERIODS = (time(9, 30), time(10, 30), time(11, 30), time(13, 30), time(14, 30))
SESSION_MINUTES = 15

MANUAL_REASONS = (
    'Phone battery died; was in class',
    'Scanned after the QR expired; was in class',
    'Device lock reset after a phone change',
)

# (department name, branch, HOD name, teachers, classes)
# Each class: (semester, roll prefix, student count, subjects as (code, name, teacher index)).
COLLEGE = (
    (
        'Computer Science and Engineering (CSE)', 'B.TECH', 'Partha Sarkar',
        ('Ananya Mukherjee', 'Sourav Banerjee', 'Ritika Sharma', 'Arindam Ghosh'),
        (
            ('Semester 5', 'CSE21', 18, (
                ('CS501', 'Operating Systems', 0),
                ('CS502', 'Computer Networks', 1),
                ('CS503', 'Database Management Systems', 2),
                ('CS504', 'Software Engineering', 3),
            )),
            ('Semester 3', 'CSE22', 10, (
                ('CS301', 'Data Structures and Algorithms', 0),
                ('CS302', 'Digital Logic Design', 3),
                ('CS303', 'Discrete Mathematics', 1),
            )),
        ),
    ),
    (
        'Master of Computer Applications (MCA)', '', 'Sudipta Bose',
        ('Debashis Roy', 'Moumita Paul'),
        (
            ('Semester 1', 'MCA24', 12, (
                ('MCA101', 'Programming in Python', 0),
                ('MCA102', 'Computer Organisation', 1),
                ('MCA103', 'Mathematical Foundations', 0),
            )),
        ),
    ),
)

FIRST_NAMES = (
    'Aarav', 'Aditi', 'Akash', 'Ankita', 'Arjun', 'Ayesha', 'Debjani', 'Dev', 'Diya', 'Farhan',
    'Gargi', 'Ishaan', 'Kabir', 'Kavya', 'Meera', 'Mihir', 'Nandini', 'Neel', 'Nikita', 'Pooja',
    'Rahul', 'Riya', 'Rohan', 'Sagnik', 'Sana', 'Shreya', 'Soham', 'Sneha', 'Tanvir', 'Trisha',
    'Ujjwal', 'Vikram', 'Yash', 'Zoya', 'Anirban', 'Payel', 'Rupam', 'Sayani', 'Tuhin', 'Madhurima',
)
LAST_NAMES = (
    'Das', 'Sen', 'Roy', 'Paul', 'Dutta', 'Ghosh', 'Saha', 'Kundu', 'Mondal', 'Basu',
    'Chatterjee', 'Bhattacharya', 'Khan', 'Gupta', 'Singh', 'Nandi', 'Pal', 'Biswas', 'Halder', 'Sarkar',
)


class DemoSeedError(Exception):
    """The database is not in a state the demo data can be loaded into."""


@dataclass
class DemoSummary:
    password: str
    logins: list = field(default_factory=list)  # (role label, username, what to try)
    counts: dict = field(default_factory=dict)


def has_records():
    """True if the database holds anything beyond the Principal account created at first start."""
    for model in (Department, Teacher, Student, ClassSession):
        if db.session.query(model.query.exists()).scalar():
            return True
    return User.query.count() > 1


# Foreign keys that set themselves to NULL when their row is deleted, and would otherwise
# make a cycle (user_account -> student -> semester -> user_account) that has no delete order.
_NULLABLE_BACK_REFERENCES = {'fk_semester_locked_by_user'}


def clear_all_data():
    """Delete every row of every table (the schema and migration history stay)."""
    tables = sort_tables(db.metadata.tables.values(),
                         skip_fn=lambda fk: fk.constraint.name in _NULLABLE_BACK_REFERENCES)
    for table in reversed(tables):
        db.session.execute(table.delete())
    db.session.flush()
    forget_cached()  # saved settings went too: back to the server defaults


def seed_demo(password=DEFAULT_DEMO_PASSWORD, weeks=6, seed=2026, reset=False):
    """Load the demo college and return a DemoSummary. Commits on success."""
    if reset:
        clear_all_data()
    elif has_records():
        raise DemoSeedError("The database already has records. Use --reset to delete everything first.")

    rng = random.Random(seed)  # noqa: S311 - sample data, not security
    # One hash for every account: computing scrypt for each of ~50 users would take seconds.
    password_hash = generate_password_hash(password)
    summary = DemoSummary(password=password)

    principal = _principal_account(password_hash)
    summary.logins.append(('Principal', principal.username, 'everything: all departments, accounts, reports'))

    today = local_now().date()
    first_day = today - timedelta(weeks=weeks)
    totals = {'sessions': 0, 'attendance': 0, 'manual': 0}
    student_number = 0

    for dept_index, (dept_name, branch, hod_name, teacher_names, classes) in enumerate(COLLEGE):
        department = Department(name=dept_name, branch=branch or None)
        db.session.add(department)
        db.session.flush()

        hod = _staff_account(_staff_email(hod_name, prefix='hod'), 'hod', password_hash, department_id=department.id)
        if dept_index == 0:
            summary.logins.append(('HOD', hod.username, f'{department.display_name}: teachers, students, corrections'))

        teachers = []
        for teacher_index, name in enumerate(teacher_names):
            teacher = Teacher(name=f'Dr. {name}', email=_staff_email(name), department_id=department.id)
            db.session.add(teacher)
            db.session.flush()
            account = _staff_account(teacher.email, 'teacher', password_hash,
                                     department_id=department.id, teacher_id=teacher.id)
            teachers.append((teacher, account))
            if dept_index == 0 and teacher_index == 0:
                summary.logins.append(('Teacher', account.username, 'start a QR session, view reports'))

        for class_index, (semester_name, roll_prefix, size, subject_specs) in enumerate(classes):
            semester = Semester(name=semester_name, department_id=department.id)
            db.session.add(semester)
            db.session.flush()

            subjects = []
            for code, name, teacher_index in subject_specs:
                teacher, account = teachers[teacher_index]
                subject = Subject(name=name, paper_code=code, department_id=department.id,
                                  semester_id=semester.id, teacher_id=teacher.id)
                db.session.add(subject)
                subjects.append((subject, teacher, account))
            db.session.flush()

            students = []
            for roll in range(1, size + 1):
                name = _student_name(student_number)
                student_number += 1
                student = Student(
                    name=name, roll_no=f'{roll_prefix}{roll:03d}',
                    email=f"{name.lower().replace(' ', '.')}@{STUDENT_DOMAIN}",
                    department_id=department.id, semester_id=semester.id,
                )
                db.session.add(student)
                students.append((student, _attendance_habit(rng)))
            db.session.flush()
            for student, _ in students:
                db.session.add(_student_account(student, password_hash))
            if dept_index == 0 and class_index == 0:
                # The steadiest attender of the class, so the student dashboard looks typical.
                example = max(students, key=lambda item: item[1])[0]
                summary.logins.append(('Student', example.email, 'own attendance, scan a QR'))

            _add_sessions(rng, subjects, students, first_day, today, totals)
            if dept_index == 0 and class_index == 0:
                totals['groups'] = _add_lab_groups(rng, department, semester, teachers, students, first_day, today,
                                                   totals)
                _add_uncounted_course(rng, department, semester, teachers, students, first_day, today, totals)
                totals['routine classes'] = _add_routine(rng, department, semester, subjects, teachers, hod, first_day,
                                                         today)
                _add_special_and_void_sessions(rng, department, semester, subjects, students, today, totals)
                totals['leave'] = _add_leave_requests(students, hod, today)
                totals['spot checks'] = _add_integrity_story(rng, subjects, students, today)

    db.session.add(ActivityLog(
        actor_username='System', action='demo_data_loaded', created_at=utc_now(),
        description=f"Loaded demo data: {student_number} students and {totals['sessions']} sessions.",
    ))
    db.session.commit()
    seal_pending()
    discard_used_initial_password()

    summary.counts = {
        'departments': len(COLLEGE),
        'students': student_number,
        'sessions': totals['sessions'],
        'attendance entries': totals['attendance'],
        'manual corrections': totals['manual'],
        'leave requests': totals.get('leave', 0),
        'spot checks': totals.get('spot checks', 0),
        'lab groups': totals.get('groups', 0),
        'classes in the routine': totals.get('routine classes', 0),
    }
    return summary


# ── Accounts ─────────────────────────────────────────────────

def _principal_account(password_hash):
    email = normalize_email(current_app.config['DEFAULT_PRINCIPAL_EMAIL'])
    principal = User.query.filter_by(username=email).first()
    if principal is None:
        principal = User(username=email, role='principal', is_active=True)
        db.session.add(principal)
    principal.password = password_hash
    principal.must_change_password = False
    principal.rotate_session_token()
    db.session.flush()
    return principal


def _staff_email(name, prefix=None):
    local = name.split()[0].lower() + '.' + name.split()[-1].lower()
    return f'{prefix}.{local}@{STAFF_DOMAIN}' if prefix else f'{local}@{STAFF_DOMAIN}'


def _staff_account(username, role, password_hash, **fields):
    account = User(username=username, password=password_hash, role=role,
                    is_active=True, must_change_password=False, **fields)
    db.session.add(account)
    db.session.flush()
    return account


def _student_account(student, password_hash):
    return User(username=student.email, password=password_hash, role='student',
                 department_id=student.department_id, student_id=student.id,
                 is_active=True, must_change_password=False)


def _student_name(number):
    first = FIRST_NAMES[number % len(FIRST_NAMES)]
    last = LAST_NAMES[(number * 7 + number // len(FIRST_NAMES)) % len(LAST_NAMES)]
    return f'{first} {last}'


# ── Attendance history ───────────────────────────────────────

def _attendance_habit(rng):
    """How likely a student is to attend a class: most are regular, some are borderline, a few are short."""
    roll = rng.random()
    if roll < 0.70:
        return rng.uniform(0.86, 0.97)
    if roll < 0.88:
        return rng.uniform(0.74, 0.84)
    return rng.uniform(0.55, 0.74)  # detained, or in the condonation band


def _class_days(first_day, today):
    day = first_day
    while day < today:
        if day.weekday() < 5:  # Monday to Friday
            yield day
        day += timedelta(days=1)


def _add_sessions(rng, subjects, students, first_day, today, totals):
    """Each subject meets on alternating weekdays, in its own period."""
    for day in _class_days(first_day, today):
        for index, (subject, teacher, account) in enumerate(subjects):
            if (day.weekday() + index) % 2:
                continue
            start = datetime.combine(day, PERIODS[index % len(PERIODS)]) + timedelta(minutes=rng.randint(0, 6))
            _add_session(rng, subject, teacher, account, start, students, totals)


def _add_session(rng, subject, teacher, account, start, students, totals,
                 special_title=None, turnout=None, active=True, group_id=None):
    start = local_to_utc(start)  # the timetable is in local time; timestamps are stored in UTC
    session = ClassSession(
        subject_id=subject.id, teacher_id=teacher.id if teacher else None, special_title=special_title,
        created_at=start,
        expires_at=start + timedelta(minutes=SESSION_MINUTES), is_active=active, group_id=group_id,
    )
    db.session.add(session)
    db.session.flush()
    totals['sessions'] += 1

    for student, habit in students:
        # A little variation per subject, so a student can be short in one subject only.
        chance = turnout if turnout is not None else min(0.99, habit + rng.uniform(-0.05, 0.05))
        if rng.random() >= chance:
            continue
        marked_at = start + timedelta(seconds=rng.randint(20, 8 * 60))
        if account is not None and rng.random() < 0.015:
            note = rng.choice(MANUAL_REASONS)
            db.session.add(Attendance(
                student_id=student.id, session_id=session.id, timestamp=start + timedelta(minutes=SESSION_MINUTES + 2),
                source=Attendance.SOURCE_MANUAL, marked_by_user_id=account.id, note=note,
            ))
            db.session.add(ActivityLog(
                actor_user_id=account.id, actor_username=account.username, department_id=subject.department_id,
                action='attendance_marked_manually', target_type='class_session', target_id=session.id,
                description=f"Marked {student.name} ({student.roll_no}) present in '{subject.name}' manually. "
                            f"Reason: {note}",
                created_at=start + timedelta(minutes=SESSION_MINUTES + 2),
            ))
            totals['manual'] += 1
        else:
            db.session.add(Attendance(
                student_id=student.id, session_id=session.id, timestamp=marked_at,
                source=Attendance.SOURCE_SCAN, scan_grant=secrets.token_hex(16),
                # Addresses are kept for SCAN_IP_RETENTION_DAYS (30), so only recent scans have one.
                scan_ip=f'10.20.{student.department_id}.{10 + student.id}' if start >= utc_now() - RECENT else None,
            ))
        totals['attendance'] += 1
    return session


# The lab of the first class runs in two batches, each once a week: (weekday, start time).
# Two teachers share it, like "MG + SC" on a printed routine.
LAB_CODE, LAB_NAME, LAB_TEACHER, LAB_CO_TEACHER = 'CS591', 'Computer Networks Lab', 1, 3
# A non-credit course taught weekly that never counts towards attendance.
EXTRA_CODE, EXTRA_NAME, EXTRA_TEACHER, EXTRA_DAY = 'CS580', 'Industry Readiness (non-credit)', 2, 4  # Fridays
LAB_BATCHES = (('Gr-1', 1, time(14, 30)), ('Gr-2', 3, time(14, 30)))  # Tuesday and Thursday afternoons


def _add_lab_groups(rng, department, semester, teachers, students, first_day, today, totals):
    """A lab subject taken in two roll-number batches. Each batch's sessions count only for its members."""
    teacher, account = teachers[LAB_TEACHER]
    lab = Subject(name=LAB_NAME, paper_code=LAB_CODE, department_id=department.id, semester_id=semester.id,
                  teacher_id=teacher.id, co_teachers=[teachers[LAB_CO_TEACHER][0]])
    db.session.add(lab)
    db.session.flush()
    half = (len(students) + 1) // 2
    joined = local_to_utc(datetime.combine(first_day - timedelta(days=1), time(9, 0)))
    for (name, weekday, start_time), batch in zip(LAB_BATCHES, (students[:half], students[half:]), strict=True):
        group = ClassGroup(department_id=department.id, semester_id=semester.id, name=name, created_at=joined)
        db.session.add(group)
        db.session.flush()
        for student, _ in batch:
            db.session.add(ClassGroupMember(group_id=group.id, student_id=student.id, joined_at=joined))
        for day in _class_days(first_day, today):
            if day.weekday() == weekday:
                _add_session(rng, lab, teacher, account, datetime.combine(day, start_time), batch, totals,
                             group_id=group.id)
    return len(LAB_BATCHES)


def _add_uncounted_course(rng, department, semester, teachers, students, first_day, today, totals):
    """A weekly non-credit course: listed in reports, counted in nobody's percentage."""
    teacher, account = teachers[EXTRA_TEACHER]
    course = Subject(name=EXTRA_NAME, paper_code=EXTRA_CODE, department_id=department.id, semester_id=semester.id,
                     teacher_id=teacher.id, counts_towards_attendance=False)
    db.session.add(course)
    db.session.flush()
    for day in _class_days(first_day, today):
        if day.weekday() == EXTRA_DAY:
            _add_session(rng, course, teacher, account, datetime.combine(day, time(16, 32)), students, totals,
                         turnout=0.6)


def _add_leave_requests(students, hod, today):
    """One approved (for the weakest attender), one waiting for the HOD, one rejected."""
    by_habit = sorted(students, key=lambda item: item[1])
    weakest, waiting, rejected = by_habit[0][0], by_habit[-1][0], by_habit[len(by_habit) // 2][0]
    ten_days_ago = today - timedelta(days=10)
    requests = [
        LeaveRequest(student_id=weakest.id, kind='medical', start_date=ten_days_ago,
                     end_date=ten_days_ago + timedelta(days=2), reason="Viral fever; doctor's certificate emailed",
                     status=LeaveRequest.APPROVED, decided_by_user_id=hod.id,
                     decided_at=utc_now() - timedelta(days=8), decision_note='Certificate received'),
        LeaveRequest(student_id=waiting.id, kind='duty', start_date=today + timedelta(days=2),
                     end_date=today + timedelta(days=3), reason='Representing the college at the inter-college hackathon'),
        LeaveRequest(student_id=rejected.id, kind='personal', start_date=today - timedelta(days=20),
                     end_date=today - timedelta(days=20), reason='Family function',
                     status=LeaveRequest.REJECTED, decided_by_user_id=hod.id,
                     decided_at=utc_now() - timedelta(days=21), decision_note='Not covered by the leave policy'),
    ]
    for leave in requests:
        leave.created_at = utc_now() - timedelta(days=22)
    db.session.add_all(requests)
    return len(requests)


RECENT = timedelta(days=28)


def _add_integrity_story(rng, subjects, students, today):
    """Something for the integrity report to find, and the spot checks that go with it.

    Two students always scan seconds apart from one address (one phone marking for both),
    and a spot check catches one of them; other recent checks pass. Returns the number of checks.
    """
    regular = sorted(students, key=lambda item: -item[1])
    first, second = regular[2][0], regular[3][0]
    teacher_account = subjects[0][2]
    sessions = (
        ClassSession.query.filter(ClassSession.subject_id.in_([s.id for s, _, _ in subjects]),
                                  ClassSession.created_at >= utc_now() - RECENT)
        .order_by(ClassSession.created_at).all()
    )
    shared_address = f'10.20.{first.department_id}.{10 + first.id}'
    both = []
    for session in sessions:
        entries = {a.student_id: a for a in Attendance.query.filter(
            Attendance.session_id == session.id, Attendance.student_id.in_([first.id, second.id]),
            Attendance.source == Attendance.SOURCE_SCAN)}
        if len(entries) == 2:
            entries[second.id].timestamp = entries[first.id].timestamp + timedelta(seconds=rng.randint(4, 12))
            entries[second.id].scan_ip = entries[first.id].scan_ip = shared_address
            both.append(session)

    checks = 0
    for session in sessions[-6:]:
        present = [a.student_id for a in Attendance.query.filter_by(session_id=session.id, source=Attendance.SOURCE_SCAN)
                   if a.student_id not in (first.id, second.id)]
        for student_id in rng.sample(present, min(3, len(present))):
            db.session.add(SpotCheck(session_id=session.id, student_id=student_id, result=SpotCheck.PRESENT,
                                     checked_by_user_id=teacher_account.id, created_at=session.expires_at,
                                     resolved_at=session.expires_at))
            checks += 1
    if both:
        caught = both[-1]
        db.session.add(SpotCheck(session_id=caught.id, student_id=second.id, result=SpotCheck.ABSENT,
                                 checked_by_user_id=teacher_account.id, created_at=caught.expires_at,
                                 resolved_at=caught.expires_at))
        Attendance.query.filter_by(session_id=caught.id, student_id=second.id).delete()
        checks += 1
    db.session.flush()
    return checks


def _add_special_and_void_sessions(rng, department, semester, subjects, students, today, totals):
    """A special class (not counted in percentages) and a session ended before anyone scanned (void)."""
    special = Subject(name=SPECIAL_CLASS_SUBJECT_NAME, paper_code=None,
                      department_id=department.id, semester_id=semester.id)
    db.session.add(special)
    db.session.flush()
    last_friday = today - timedelta(days=(today.weekday() - 4) % 7 or 7)
    _add_session(rng, special, None, None, datetime.combine(last_friday, time(15, 30)), students, totals,
                 special_title='Placement aptitude workshop', turnout=0.7)

    subject, teacher, account = subjects[0]
    void_day = today - timedelta(days=(today.weekday() - 1) % 7 or 7)  # last Tuesday
    _add_session(rng, subject, teacher, account, datetime.combine(void_day, time(16, 0)), students, totals,
                 turnout=0.0, active=False)


# ── The routine of the first class ───────────────────────────

# The bell schedule: (label, start, end, is_break). Sessions above start inside these periods.
BELL_PERIODS = (
    ('1', time(9, 30), time(10, 20), False), ('2', time(10, 30), time(11, 20), False),
    ('3', time(11, 30), time(12, 20), False), ('Lunch', time(12, 20), time(13, 30), True),
    ('4', time(13, 30), time(14, 20), False), ('5', time(14, 30), time(15, 20), False),
    ('6', time(15, 30), time(16, 20), False), ('7', time(16, 30), time(17, 20), False),
)


def _add_routine(rng, department, semester, subjects, teachers, hod, first_day, today):
    """A bell schedule, the class's published routine (matching the sessions above, which are then
    linked to it), a weekly seminar that is rarely recorded, and a few one-date changes."""
    schedule = BellSchedule(name='Weekday')
    schedule.periods = [BellPeriod(position=i + 1, label=label, start_time=start, end_time=end, is_break=is_break)
                        for i, (label, start, end, is_break) in enumerate(BELL_PERIODS)]
    schedule.days = [BellScheduleDay(weekday=day) for day in range(5)]
    db.session.add(schedule)
    starts_on = first_day - timedelta(days=first_day.weekday())
    routine = Timetable(department_id=department.id, semester_id=semester.id, status=Timetable.PUBLISHED,
                        effective_from=starts_on, published_at=utc_now(), created_by_user_id=hod.id,
                        published_by_user_id=hod.id)
    db.session.add(routine)

    def entry(weekday, start, end, room, subject=None, title=None, group=None, entry_teachers=()):
        added = TimetableEntry(timetable=routine, weekday=weekday, start_time=start, end_time=end, subject=subject,
                               title=title, group=group, room=room)
        db.session.add(added)
        added.teachers = list(entry_teachers)
        return added

    for index, (subject, teacher, _) in enumerate(subjects):
        start = PERIODS[index % len(PERIODS)]
        end = (datetime.combine(today, start) + timedelta(minutes=50)).time()
        for weekday in range(5):
            if (weekday + index) % 2 == 0:
                entry(weekday, start, end, 'Room 301', subject=subject, entry_teachers=[teacher])
    lab = Subject.query.filter_by(semester_id=semester.id, paper_code=LAB_CODE).one()
    lab_teachers = [lab.teacher, *list(lab.co_teachers)]
    for (_, weekday, start_time), group in zip(LAB_BATCHES, ClassGroup.query.filter_by(semester_id=semester.id)
                                                  .order_by(ClassGroup.name), strict=True):
        entry(weekday, start_time, time(16, 20), 'Lab 2', subject=lab, group=group, entry_teachers=lab_teachers)
    course = Subject.query.filter_by(semester_id=semester.id, paper_code=EXTRA_CODE).one()
    entry(EXTRA_DAY, time(16, 30), time(17, 20), 'Room 301', subject=course, entry_teachers=[course.teacher])
    seminar = entry(2, time(15, 30), time(16, 20), 'Seminar hall', title='Department seminar',
                    entry_teachers=[teachers[0][0]])
    db.session.flush()

    # Link each session held to the routine class it was.
    entries = list(routine.entries)
    for session in ClassSession.query.join(Subject).filter(Subject.semester_id == semester.id,
                                                            ClassSession.special_title.is_(None)):
        local = to_local(session.created_at)
        match = next((e for e in entries if e.subject_id == session.subject_id and e.group_id == session.group_id
                      and e.weekday == local.weekday() and e.start_time <= local.time() < e.end_time), None)
        if match is not None:
            session.timetable_entry_id, session.occurrence_date = match.id, local.date()

    # The seminar was recorded once (last week); the other weeks show as not recorded.
    last_wednesday = today - timedelta(days=(today.weekday() - 2) % 7 or 7)
    seminar_session = _add_session(rng, _special_subject(department, semester), teachers[0][0], teachers[0][1],
                                   datetime.combine(last_wednesday, time(15, 32)), [], {'sessions': 0, 'attendance': 0,
                                                                                         'manual': 0},
                                   special_title='Department seminar', turnout=0)
    seminar_session.timetable_entry_id, seminar_session.occurrence_date = seminar.id, last_wednesday

    # Coming changes: a class cancelled, one taken by a substitute, a holiday.
    def next_day(weekday, after=1):
        return today + timedelta(days=(weekday - today.weekday()) % 7 or 7) + timedelta(days=7 * (after - 1))
    first_entry = next(e for e in entries if e.subject_id == subjects[0][0].id)
    second_entry = next(e for e in entries if e.subject_id == subjects[1][0].id)
    db.session.add_all([
        RoutineChange(kind=RoutineChange.CANCEL, timetable_entry_id=first_entry.id, date=next_day(first_entry.weekday),
                      department_id=department.id, semester_id=semester.id, subject_id=first_entry.subject_id,
                      reason='Teacher at a university meeting', created_by_user_id=hod.id),
        RoutineChange(kind=RoutineChange.SUBSTITUTE, timetable_entry_id=second_entry.id,
                      date=next_day(second_entry.weekday, 2), substitute_teacher_id=teachers[3][0].id,
                      department_id=department.id, semester_id=semester.id, subject_id=second_entry.subject_id,
                      reason='Teacher on leave', created_by_user_id=hod.id),
        Holiday(start_date=today + timedelta(days=17), end_date=today + timedelta(days=18), title='College foundation day'),
    ])
    return len(entries)


def _special_subject(department, semester):
    special = Subject.query.filter_by(department_id=department.id, semester_id=semester.id,
                                      name=SPECIAL_CLASS_SUBJECT_NAME).first()
    if special is None:
        special = Subject(name=SPECIAL_CLASS_SUBJECT_NAME, paper_code=None, department_id=department.id,
                          semester_id=semester.id)
        db.session.add(special)
        db.session.flush()
    return special
