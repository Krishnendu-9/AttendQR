"""Sample data for demonstrations: `flask seed-demo`.

Builds a small college with several weeks of past QR sessions, so the dashboard, reports,
exports, shortage list and class routine have something to show. The department it shows
first is MCA: one class whose routine follows a real college's printed routine (two lab
batches side by side, a non-credit course, a tutorial slot), with its sessions generated
from that routine. Its HOD, Dr. Manas Ghosh, also teaches (RDBMS). A smaller B.TECH - CSE
department has two classes without a routine. Every account shares one demo password and
needs no password change, so anyone trying the demo can sign in at once.

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
from ..utils import local_now, local_to_utc, normalize_email, utc_now
from .audit import seal_pending
from .leave import approve as approve_leave
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

# The other departments: (department name, branch, HOD name, teachers, classes). Each class:
# (semester, roll prefix, student count, subjects as (code, name, teacher index)). Their subjects
# meet on alternating weekdays in one of PERIODS; they have no routine.
COLLEGE = (
    (
        'Computer Science and Engineering (CSE)', 'B.TECH', 'Partha Sarkar',
        ('Debashis Roy', 'Moumita Paul', 'Rupam Saha'),
        (
            ('Semester 5', 'CSE21', 18, (
                ('CS501', 'Operating Systems', 0),
                ('CS502', 'Computer Networks', 1),
                ('CS503', 'Database Management Systems', 2),
            )),
            ('Semester 3', 'CSE22', 10, (
                ('CS301', 'Data Structures and Algorithms', 0),
                ('CS303', 'Discrete Mathematics', 1),
            )),
        ),
    ),
)

# ── The MCA class the demo shows ─────────────────────────────

MCA_DEPARTMENT = 'Department of Computer Application (MCA)'  # shown as "MCA"
MCA_HOD = 'Manas Ghosh'  # the HOD, who also teaches RDBMS and its lab
MCA_TEACHERS = ('Ananya Mukherjee', 'Sourav Banerjee', 'Ritika Sharma', 'Arindam Ghosh')
MCA_SEMESTER, MCA_ROLL_PREFIX, MCA_SIZE = 'Semester 1', 'MCA2024', 40
EXAMPLE_ROLL, EXAMPLE_NAME = 3, 'Chocolate'  # MCA2024003: the student demo login
# (paper code, name, teacher: 'hod' or an index of MCA_TEACHERS, co-teacher, counts towards attendance)
MCA_SUBJECTS = (
    ('MCA C101', 'Programming Concept with C', 0, None, True),
    ('MCA C102', 'RDBMS', 'hod', None, True),
    ('MCA C103', 'Discrete Mathematics', 1, None, True),
    ('MCA C104', 'Computer Organisation and Architecture', 2, None, True),
    ('MCA C105', 'Operating Systems', 3, None, True),
    ('MCA C191', 'C Programming Lab', 0, None, True),
    ('MCA C192', 'RDBMS Lab', 'hod', 3, True),
    ('INTEL AI', 'Intel AI Foundation (non-credit)', 1, None, False),
)
LAB_CODES = ('MCA C191', 'MCA C192')
TUTORIAL = 'Special Tutorial Class'  # a slot in the routine that is not a subject
# The bell schedule, as on the printed routine: (label, start, end, is a break).
BELL_PERIODS = (
    ('1', time(10, 0), time(10, 50), False), ('2', time(10, 50), time(11, 40), False),
    ('3', time(11, 40), time(12, 30), False), ('4', time(12, 30), time(13, 20), False),
    ('Break', time(13, 20), time(14, 10), True), ('5', time(14, 10), time(15, 0), False),
    ('6', time(15, 0), time(15, 50), False), ('7', time(15, 50), time(16, 40), False),
    ('8', time(16, 40), time(17, 30), False),
)
# The week: (weekday, start, end, paper code or a title, group, room). The two lab batches run side
# by side on Monday afternoon and Tuesday morning, each in a different lab.
MCA_ROUTINE = (
    (0, time(10, 0), time(11, 40), 'MCA C102', None, '416'),
    (0, time(11, 40), time(13, 20), 'MCA C103', None, '416'),
    (0, time(14, 10), time(15, 50), 'MCA C191', 'Gr-1', 'Lab 1'),
    (0, time(14, 10), time(15, 50), 'MCA C192', 'Gr-2', 'Lab 2'),
    (1, time(10, 0), time(12, 30), 'MCA C192', 'Gr-1', 'Lab 2'),
    (1, time(10, 0), time(12, 30), 'MCA C191', 'Gr-2', 'Lab 1'),
    (1, time(14, 10), time(15, 50), 'MCA C104', None, '416'),
    (1, time(15, 50), time(17, 30), 'MCA C105', None, '416'),
    (2, time(10, 0), time(10, 50), 'INTEL AI', None, '416'),
    (2, time(10, 50), time(12, 30), 'MCA C101', None, '416'),
    (2, time(12, 30), time(13, 20), 'MCA C105', None, '416'),
    (2, time(14, 10), time(15, 50), 'MCA C103', None, '416'),
    (2, time(15, 50), time(17, 30), TUTORIAL, None, '416'),
    (3, time(10, 0), time(11, 40), 'MCA C104', None, '416'),
    (3, time(11, 40), time(13, 20), 'MCA C102', None, '416'),
    (3, time(14, 10), time(15, 50), 'MCA C101', None, '416'),
    (4, time(10, 0), time(11, 40), 'MCA C105', None, '416'),
    (4, time(11, 40), time(13, 20), 'MCA C104', None, '416'),
    (4, time(14, 10), time(15, 50), 'MCA C102', None, '416'),
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
    first_day -= timedelta(days=first_day.weekday())  # a Monday, so the routine's first week is whole
    totals = {'sessions': 0, 'attendance': 0, 'manual': 0}
    student_number = _add_mca_department(rng, password_hash, summary, first_day, today, totals)
    names_used = student_number

    for dept_name, branch, hod_name, teacher_names, classes in COLLEGE:
        department = Department(name=dept_name, branch=branch or None)
        db.session.add(department)
        db.session.flush()
        _staff_account(_staff_email(hod_name, prefix='hod'), 'hod', password_hash, department_id=department.id)

        teachers = []
        for name in teacher_names:
            teacher = Teacher(name=f'Dr. {name}', email=_staff_email(name), department_id=department.id)
            db.session.add(teacher)
            db.session.flush()
            teachers.append((teacher, _staff_account(teacher.email, 'teacher', password_hash,
                                                     department_id=department.id, teacher_id=teacher.id)))

        for semester_name, roll_prefix, size, subject_specs in classes:
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
                name = _student_name(names_used)
                names_used += 1
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
            student_number += size
            _add_sessions(rng, subjects, students, first_day, today, totals)

    db.session.add(ActivityLog(
        actor_username='System', action='demo_data_loaded', created_at=utc_now(),
        description=f"Loaded demo data: {student_number} students and {totals['sessions']} sessions.",
    ))
    db.session.commit()
    seal_pending()
    discard_used_initial_password()

    summary.counts = {
        'departments': len(COLLEGE) + 1,
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


def _add_leave_requests(students, hod, today):
    """One approved (for the weakest attender), one waiting for the HOD, one rejected."""
    by_habit = sorted(students, key=lambda item: item[1])
    weakest, waiting, rejected = by_habit[0][0], by_habit[-1][0], by_habit[len(by_habit) // 2][0]
    ten_days_ago = today - timedelta(days=10)
    requests = [
        LeaveRequest(student_id=weakest.id, kind='medical', start_date=ten_days_ago,
                     end_date=ten_days_ago + timedelta(days=2), reason="Viral fever; doctor's certificate emailed",
                     decided_by_user_id=hod.id,
                     decided_at=utc_now() - timedelta(days=8), decision_note='Certificate received'),
        LeaveRequest(student_id=waiting.id, kind='duty', start_date=today + timedelta(days=2),
                     end_date=today + timedelta(days=3), reason='Representing the college at the inter-college hackathon'),
        LeaveRequest(student_id=rejected.id, kind='personal', start_date=today - timedelta(days=20),
                     end_date=today - timedelta(days=20), reason='Family function',
                     status=LeaveRequest.REJECTED, decided_by_user_id=hod.id,
                     decided_at=utc_now() - timedelta(days=21), decision_note='Not covered by the leave policy'),
    ]
    approve_leave(requests[0])
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
    special = _special_subject(department, semester)
    last_friday = today - timedelta(days=(today.weekday() - 4) % 7 or 7)
    _add_session(rng, special, None, None, datetime.combine(last_friday, time(16, 0)), students, totals,
                 special_title='Placement aptitude workshop', turnout=0.7)

    subject, teacher, account = subjects[0]
    void_day = today - timedelta(days=(today.weekday() - 1) % 7 or 7)  # last Tuesday
    _add_session(rng, subject, teacher, account, datetime.combine(void_day, time(16, 0)), students, totals,
                 turnout=0.0, active=False)


# ── The MCA department ───────────────────────────────────────

def _add_mca_department(rng, password_hash, summary, first_day, today, totals):
    """The department the demo shows: its HOD (who also teaches), teachers, the MCA class with its
    groups and routine, and a history of sessions held from that routine. Returns the number of students."""
    department = Department(name=MCA_DEPARTMENT, branch=None)
    db.session.add(department)
    db.session.flush()

    hod_teacher = Teacher(name=f'Dr. {MCA_HOD}', email=_staff_email(MCA_HOD), department_id=department.id)
    db.session.add(hod_teacher)
    db.session.flush()
    hod = _staff_account(hod_teacher.email, 'hod', password_hash, department_id=department.id,
                         teacher_id=hod_teacher.id)
    summary.logins.append(('HOD', hod.username, 'MCA, and teaches RDBMS: integrity, eligibility, analytics, leave'))
    staff = []
    for index, name in enumerate(MCA_TEACHERS):
        teacher = Teacher(name=f'Dr. {name}', email=_staff_email(name), department_id=department.id)
        db.session.add(teacher)
        db.session.flush()
        account = _staff_account(teacher.email, 'teacher', password_hash, department_id=department.id,
                                 teacher_id=teacher.id)
        staff.append((teacher, account))
        if index == 0:
            summary.logins.append(('Teacher', account.username, "start today's class from the routine, view reports"))

    def who(ref):
        return (hod_teacher, hod) if ref == 'hod' else staff[ref]
    accounts = {teacher.id: account for teacher, account in [who('hod'), *staff]}

    semester = Semester(name=MCA_SEMESTER, department_id=department.id)
    db.session.add(semester)
    db.session.flush()
    subjects = {}
    for code, name, ref, co_ref, counts in MCA_SUBJECTS:
        subject = Subject(name=name, paper_code=code, department_id=department.id, semester_id=semester.id,
                          teacher_id=who(ref)[0].id, counts_towards_attendance=counts)
        if co_ref is not None:
            subject.co_teachers = [who(co_ref)[0]]
        db.session.add(subject)
        subjects[code] = subject
    db.session.flush()

    students = []
    for roll in range(1, MCA_SIZE + 1):
        roll_no = f'{MCA_ROLL_PREFIX}{roll:03d}'
        if roll == EXAMPLE_ROLL:
            name, habit = EXAMPLE_NAME, 0.94  # a steady attender, so the student dashboard looks typical
        else:
            name, habit = _student_name(roll), _attendance_habit(rng)
        student = Student(name=name, roll_no=roll_no, email=f'{roll_no.lower()}@{STAFF_DOMAIN}',
                          department_id=department.id, semester_id=semester.id)
        db.session.add(student)
        students.append((student, habit))
    db.session.flush()
    for student, _ in students:
        db.session.add(_student_account(student, password_hash))
    example = students[EXAMPLE_ROLL - 1][0]
    summary.logins.append(('Student', example.email, f'{example.name}: own attendance, applying for leave'))

    # Two lab batches by roll number, members since before the first class.
    joined = local_to_utc(datetime.combine(first_day - timedelta(days=1), time(9, 0)))
    half = len(students) // 2
    groups, members = {}, {}
    for name, batch in (('Gr-1', students[:half]), ('Gr-2', students[half:])):
        group = ClassGroup(department_id=department.id, semester_id=semester.id, name=name, created_at=joined)
        db.session.add(group)
        db.session.flush()
        for student, _ in batch:
            db.session.add(ClassGroupMember(group_id=group.id, student_id=student.id, joined_at=joined))
        groups[name], members[name] = group, batch
    totals['groups'] = len(groups)

    entries = _add_mca_routine(department, semester, subjects, groups, hod, first_day)
    totals['routine classes'] = len(entries)
    _add_routine_history(rng, department, semester, entries, students, members, accounts, first_day, today, totals)

    theory = [(subjects[code], who(ref)[0], who(ref)[1]) for code, _, ref, _, counts in MCA_SUBJECTS
              if counts and code not in LAB_CODES]
    _add_special_and_void_sessions(rng, department, semester, theory, students, today, totals)
    others = [item for item in students if item[0] is not example]  # Chocolate stays out of the stories
    totals['leave'] = _add_leave_requests(others, hod, today)
    # The pair the integrity report should find: two of Gr-2 (same labs as each other, Chocolate is in
    # Gr-1), paired in every class they share, labs and the non-credit course too, so they scan
    # together in well over half their scans.
    every_subject = [(subjects[code], who(ref)[0], who(ref)[1]) for code, _, ref, _, _ in MCA_SUBJECTS]
    totals['spot checks'] = _add_integrity_story(rng, every_subject, members['Gr-2'], today)
    _add_routine_changes(department, semester, entries, subjects, staff, hod, today)
    return len(students)


def _add_mca_routine(department, semester, subjects, groups, hod, first_day):
    """The bell schedule and the class's published routine (MCA_ROUTINE). Returns its entries."""
    schedule = BellSchedule(name='Weekday')
    schedule.periods = [BellPeriod(position=i + 1, label=label, start_time=start, end_time=end, is_break=is_break)
                        for i, (label, start, end, is_break) in enumerate(BELL_PERIODS)]
    schedule.days = [BellScheduleDay(weekday=day) for day in range(5)]
    db.session.add(schedule)
    routine = Timetable(department_id=department.id, semester_id=semester.id, status=Timetable.PUBLISHED,
                        effective_from=first_day, published_at=utc_now(), created_by_user_id=hod.id,
                        published_by_user_id=hod.id)
    db.session.add(routine)
    entries = []
    for weekday, start, end, what, group, room in MCA_ROUTINE:
        subject = subjects.get(what)
        entry = TimetableEntry(timetable=routine, weekday=weekday, start_time=start, end_time=end, subject=subject,
                               title=None if subject else what, group=groups.get(group), room=room)
        db.session.add(entry)
        entry.teachers = [t for t in [subject.teacher, *list(subject.co_teachers)] if t] if subject else []
        entries.append(entry)
    db.session.flush()
    return entries


def _add_routine_history(rng, department, semester, entries, students, members, accounts, first_day, today, totals):
    """A session for every class of the routine held so far, linked to it. The tutorial slot was
    recorded only last week, so the other weeks show it as not recorded."""
    last_week = today - timedelta(days=7)
    for day in _class_days(first_day, today):
        for entry in (e for e in entries if e.weekday == day.weekday()):
            start = datetime.combine(day, entry.start_time) + timedelta(minutes=rng.randint(0, 6))
            if entry.subject is None:
                if day < last_week:
                    continue
                session = _add_session(rng, _special_subject(department, semester), None, None, start, [], totals,
                                       special_title=entry.title, turnout=0)
            else:
                teacher = entry.teachers[0]
                batch = members[entry.group.name] if entry.group else students
                turnout = None if entry.subject.counts_towards_attendance else 0.6
                session = _add_session(rng, entry.subject, teacher, accounts.get(teacher.id), start, batch, totals,
                                       turnout=turnout, group_id=entry.group_id)
            session.timetable_entry_id, session.occurrence_date = entry.id, day


def _add_routine_changes(department, semester, entries, subjects, staff, hod, today):
    """Coming changes for one date: a class cancelled, one taken by a substitute; and a holiday."""
    def next_day(weekday, after=1):
        return today + timedelta(days=(weekday - today.weekday()) % 7 or 7) + timedelta(days=7 * (after - 1))
    cancelled = next(e for e in entries if e.subject is subjects['MCA C101'])
    substituted = next(e for e in entries if e.subject is subjects['MCA C103'])
    db.session.add_all([
        RoutineChange(kind=RoutineChange.CANCEL, timetable_entry_id=cancelled.id, date=next_day(cancelled.weekday),
                      department_id=department.id, semester_id=semester.id, subject_id=cancelled.subject_id,
                      reason='Teacher at a university meeting', created_by_user_id=hod.id),
        RoutineChange(kind=RoutineChange.SUBSTITUTE, timetable_entry_id=substituted.id,
                      date=next_day(substituted.weekday, 2), substitute_teacher_id=staff[2][0].id,
                      department_id=department.id, semester_id=semester.id, subject_id=substituted.subject_id,
                      reason='Teacher on leave', created_by_user_id=hod.id),
        Holiday(start_date=today + timedelta(days=17), end_date=today + timedelta(days=18), title='College foundation day'),
    ])


def _special_subject(department, semester):
    special = Subject.query.filter_by(department_id=department.id, semester_id=semester.id,
                                      name=SPECIAL_CLASS_SUBJECT_NAME).first()
    if special is None:
        special = Subject(name=SPECIAL_CLASS_SUBJECT_NAME, paper_code=None, department_id=department.id,
                          semester_id=semester.id)
        db.session.add(special)
        db.session.flush()
    return special
