import re
import secrets
from typing import ClassVar

import sqlalchemy as sa
from flask_login import UserMixin

from .extensions import db
from .utils import utc_now


def new_session_token():
    return secrets.token_hex(16)


def one_of(column, values, name):
    """A CHECK constraint that `column` holds one of `values`. The app validates first; this guards the data."""
    allowed = ', '.join(f"'{value}'" for value in values)
    return db.CheckConstraint(f"{column} IN ({allowed})", name=name)


# Every role, and those that are staff. The one list: access.py labels these, and the database checks them.
STAFF_ROLES = ('principal', 'hod', 'teacher')
ROLES = (*STAFF_ROLES, 'student')


class User(UserMixin, db.Model):
    """Every login: Principal, HOD, Teacher and Student accounts (see `role`)."""
    # Not "user": that is a reserved word in PostgreSQL.
    __tablename__ = 'user_account'
    __table_args__ = (
        one_of('role', ROLES, 'ck_user_account_role'),
    )

    id = db.Column(db.Integer, primary_key=True)
    # Always stored lower case (normalize_email), so lookups compare it directly and use the unique index.
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), nullable=False)  # one of ROLES
    department_id = db.Column(db.Integer, db.ForeignKey('department.id', ondelete='SET NULL'), nullable=True)
    teacher_id = db.Column(db.Integer, db.ForeignKey('teacher.id', ondelete='SET NULL'), nullable=True)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id', ondelete='SET NULL'), nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    must_change_password = db.Column(db.Boolean, nullable=False, default=False)
    # Part of the login session identifier; rotating it signs out every existing session.
    session_token = db.Column(db.String(64), nullable=False, default=new_session_token)
    # Two-step sign-in (services/two_factor.py). The secret is set once the user has proved
    # their authenticator works; until then it waits in totp_pending_secret. Both are stored
    # encrypted (Fernet, keyed from SECRET_KEY), so a copy of the database cannot make codes.
    totp_secret = db.Column(db.String(255), nullable=True)
    totp_pending_secret = db.Column(db.String(255), nullable=True)
    totp_enabled_at = db.Column(db.DateTime, nullable=True)
    # The last 30-second step a code was accepted for: each code works once.
    totp_last_step = db.Column(db.Integer, nullable=True)
    # JSON list of SHA-256 hashes of the unused recovery codes.
    recovery_codes = db.Column(db.Text, nullable=True)

    department = db.relationship('Department', foreign_keys=[department_id])
    teacher = db.relationship('Teacher', foreign_keys=[teacher_id])
    student = db.relationship('Student', foreign_keys=[student_id])

    def get_id(self):
        return f"{self.id}:{self.session_token}"

    def rotate_session_token(self):
        self.session_token = new_session_token()


class LoginThrottle(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(255), unique=True, nullable=False)
    username = db.Column(db.String(120), nullable=False, index=True)
    failures = db.Column(db.Integer, nullable=False, default=0)
    window_started_at = db.Column(db.DateTime, nullable=True)
    locked_until = db.Column(db.DateTime, nullable=True)


class Department(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    branch = db.Column(db.String(20), nullable=True)
    semesters = db.relationship('Semester', backref='department', lazy=True)
    subjects = db.relationship('Subject', backref='department', lazy=True)
    teachers = db.relationship('Teacher', backref='department', lazy=True)
    students = db.relationship('Student', backref='department', lazy=True, foreign_keys='Student.department_id')

    @property
    def course_name(self):
        return self.name

    @property
    def branch_name(self):
        return (self.branch or '').strip().upper()

    @property
    def display_name(self):
        if self.branch_name == 'M.TECH':
            return f"{self.branch_name} - {self.course_name}"

        match = re.search(r'\((.*?)\)', self.course_name)
        if match:
            short_form = match.group(1).strip()
            if self.branch_name:
                return f"{self.branch_name} - {short_form}"
            return short_form

        if self.branch_name:
            return f"{self.branch_name} - {self.course_name}"
        return self.course_name


class Semester(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)
    department_id = db.Column(db.Integer, db.ForeignKey('department.id', ondelete='CASCADE'), nullable=False)
    # Set once the semester's attendance is final (after exams): nothing in it can change until unlocked.
    attendance_locked_at = db.Column(db.DateTime, nullable=True)
    attendance_locked_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey('user_account.id', ondelete='SET NULL', name='fk_semester_locked_by_user'),
        nullable=True,
    )
    # The exam eligibility percentages in force when it was locked, so changing the settings
    # afterwards does not change a final semester's eligibility. Cleared on unlock.
    locked_eligibility_percent = db.Column(db.Integer, nullable=True)
    locked_condonation_percent = db.Column(db.Integer, nullable=True)
    locked_by = db.relationship('User', foreign_keys=[attendance_locked_by_user_id])
    subjects = db.relationship('Subject', backref='semester', lazy=True)
    students = db.relationship('Student', backref='semester', lazy=True, foreign_keys='Student.semester_id')

    @property
    def attendance_locked(self):
        return self.attendance_locked_at is not None


class Subject(db.Model):
    __table_args__ = (
        db.Index('ix_subject_department_semester', 'department_id', 'semester_id'),
    )
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    paper_code = db.Column(db.String(50), nullable=True)
    department_id = db.Column(db.Integer, db.ForeignKey('department.id', ondelete='CASCADE'), nullable=False)
    semester_id = db.Column(db.Integer, db.ForeignKey('semester.id', ondelete='CASCADE'), nullable=False)
    teacher_id = db.Column(db.Integer, db.ForeignKey('teacher.id', ondelete='SET NULL'), nullable=True)
    # Off for something taught but never counted, such as a non-credit course: its sessions are
    # listed in reports but, like special classes, count in nobody's percentage.
    counts_towards_attendance = db.Column(db.Boolean, nullable=False, default=True, server_default=sa.true())
    # The group the subject is for (an elective, or a lab one batch takes); new sessions default to it.
    group_id = db.Column(db.Integer, db.ForeignKey('class_group.id', ondelete='SET NULL', name='fk_subject_group'),
                         nullable=True)
    # Its colour in the routine (a key of services/subject_colors.SUBJECT_COLORS). NULL: chosen
    # automatically, so a class's subjects differ without anyone setting them.
    color = db.Column(db.String(12), nullable=True)
    teacher = db.relationship('Teacher', backref=db.backref('assigned_subjects', lazy=True), foreign_keys=[teacher_id])
    group = db.relationship('ClassGroup', foreign_keys=[group_id])
    # Teachers besides the main one (subject.teacher) who run and correct its sessions.
    co_teachers = db.relationship('Teacher', secondary='subject_teacher', order_by='Teacher.name',
                                  backref=db.backref('co_taught_subjects', lazy=True))


class SubjectTeacher(db.Model):
    """A co-teacher of a subject, such as the second teacher of a lab (MG + SC)."""
    __table_args__ = (
        db.Index('uq_subject_teacher', 'subject_id', 'teacher_id', unique=True),
        db.Index('ix_subject_teacher_teacher_id', 'teacher_id'),
    )
    id = db.Column(db.Integer, primary_key=True)
    subject_id = db.Column(db.Integer, db.ForeignKey('subject.id', ondelete='CASCADE'), nullable=False)
    teacher_id = db.Column(db.Integer, db.ForeignKey('teacher.id', ondelete='CASCADE'), nullable=False)


class Teacher(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)  # lower case, like User.username
    department_id = db.Column(db.Integer, db.ForeignKey('department.id', ondelete='CASCADE'), nullable=False)
    # Initials shown in the routine grid ("SC", "MG"); access.teacher_short_name() fills a missing one.
    short_name = db.Column(db.String(10), nullable=True)


class Student(db.Model):
    __table_args__ = (
        db.UniqueConstraint('roll_no', 'department_id', 'semester_id', name='unique_roll_dept_sem'),
        # A registered device belongs to exactly one student (NULLs are allowed many times).
        db.Index('uq_student_device_id', 'device_id', unique=True),
        # Every report and the session view list the students of one class.
        db.Index('ix_student_department_semester', 'department_id', 'semester_id'),
    )
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    roll_no = db.Column(db.String(20), nullable=False)  # upper case (normalize_roll_no)
    email = db.Column(db.String(120), unique=True, nullable=False)  # lower case (normalize_email)
    department_id = db.Column(db.Integer, db.ForeignKey('department.id', ondelete='CASCADE'), nullable=False)
    semester_id = db.Column(db.Integer, db.ForeignKey('semester.id', ondelete='CASCADE'), nullable=False)
    device_id = db.Column(db.String(100), nullable=True)
    # When the student was added to, or moved into, their current class. Sessions held before
    # then are not theirs unless they have an attendance entry for one (services/counting.py).
    # NULL: in the class from the start (every student from before migration 0024).
    class_joined_at = db.Column(db.DateTime, nullable=True)
    # When the student left the college (graduated, dropped out, transferred), with why. Their time in
    # their class ends then, as if they had moved out (services/enrollment.py); their records stay.
    # NULL: still a student here.
    left_at = db.Column(db.DateTime, nullable=True)
    left_reason = db.Column(db.String(200), nullable=True)


class StudentClassHistory(db.Model):
    """A class (department and semester) a student was moved out of, and when.

    Reports select students by their current class, so without this a promoted
    student would vanish from their old semester's reports and shortage list.
    """
    __table_args__ = (
        db.Index('ix_student_class_history_class', 'department_id', 'semester_id'),
    )
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id', ondelete='CASCADE'), nullable=False, index=True)
    department_id = db.Column(db.Integer, db.ForeignKey('department.id', ondelete='CASCADE'), nullable=False)
    semester_id = db.Column(db.Integer, db.ForeignKey('semester.id', ondelete='CASCADE'), nullable=False)
    # When they had joined that class (NULL: from the start). Sessions before it were not theirs either.
    joined_at = db.Column(db.DateTime, nullable=True)
    # Sessions created from this moment on are not the student's.
    left_at = db.Column(db.DateTime, nullable=False)


class ClassGroup(db.Model):
    """A named part of one class (department and semester): a lab batch, an elective, a tutorial set.

    A session can be for one group; it then counts only for the students who were members
    when it was held (services/groups.py). A student may be in several groups.
    """
    __table_args__ = (
        db.Index('ix_class_group_semester_id', 'semester_id'),
    )
    id = db.Column(db.Integer, primary_key=True)
    department_id = db.Column(db.Integer, db.ForeignKey('department.id', ondelete='CASCADE'), nullable=False)
    semester_id = db.Column(db.Integer, db.ForeignKey('semester.id', ondelete='CASCADE'), nullable=False)
    name = db.Column(db.String(60), nullable=False)  # unique within the semester, ignoring case (see below)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)

    department = db.relationship('Department')
    semester = db.relationship('Semester')


# A group name is unique within its semester, ignoring case ("Gr-1" and "GR-1" are one group).
db.Index('uq_class_group_semester_name', ClassGroup.semester_id, sa.func.lower(ClassGroup.name), unique=True)


class ClassGroupMember(db.Model):
    """A student's time in a group. Leaving sets left_at; the row stays, so old sessions keep counting."""
    __table_args__ = (
        db.Index('ix_class_group_member_group_student', 'group_id', 'student_id'),
        db.Index('ix_class_group_member_student_id', 'student_id'),
    )
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('class_group.id', ondelete='CASCADE'), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id', ondelete='CASCADE'), nullable=False)
    joined_at = db.Column(db.DateTime, nullable=False, default=utc_now)
    left_at = db.Column(db.DateTime, nullable=True)

    group = db.relationship('ClassGroup', backref=db.backref('memberships', cascade='all, delete-orphan',
                                                             passive_deletes=True))
    student = db.relationship('Student', backref=db.backref('group_memberships', cascade='all, delete-orphan',
                                                            passive_deletes=True))


class ClassSession(db.Model):
    __table_args__ = (
        db.Index('ix_class_session_subject_id', 'subject_id'),
        db.Index('ix_class_session_teacher_id', 'teacher_id'),
        db.Index('ix_class_session_created_at', 'created_at'),
        db.Index('ix_class_session_group_id', 'group_id'),
        # One session per class occurrence in the routine (NULLs allowed many times).
        db.Index('uq_class_session_occurrence', 'timetable_entry_id', 'occurrence_date', unique=True),
        # One session per submitted Generate form, so a double click or a resent form opens the first.
        db.Index('uq_class_session_create_token', 'create_token', unique=True),
    )
    id = db.Column(db.Integer, primary_key=True)
    subject_id = db.Column(db.Integer, db.ForeignKey('subject.id', ondelete='CASCADE'), nullable=False)
    teacher_id = db.Column(db.Integer, db.ForeignKey('teacher.id', ondelete='SET NULL'), nullable=True)
    special_title = db.Column(db.String(200), nullable=True)
    created_at = db.Column(db.DateTime, default=utc_now)
    expires_at = db.Column(db.DateTime, nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    # The group the session is for; NULL means the whole class. A group with sessions cannot be
    # deleted (the app refuses; the key is NO ACTION so a department's cascade still works).
    group_id = db.Column(db.Integer, db.ForeignKey('class_group.id', name='fk_class_session_group'), nullable=True)
    # The routine class this session holds, and on which date (services/routine.py). NULL: started
    # outside the routine ("Unscheduled" where the class has a published routine).
    timetable_entry_id = db.Column(
        db.Integer, db.ForeignKey('timetable_entry.id', ondelete='SET NULL', name='fk_class_session_timetable_entry'),
        nullable=True,
    )
    occurrence_date = db.Column(db.Date, nullable=True)
    # The one-time token of the Generate form that created it (attendance._create_session); NULL otherwise.
    create_token = db.Column(db.String(32), nullable=True)
    subject = db.relationship('Subject', backref=db.backref('sessions', lazy=True))
    teacher = db.relationship('Teacher', backref=db.backref('sessions', lazy=True))
    group = db.relationship('ClassGroup')
    timetable_entry = db.relationship('TimetableEntry')

    def is_live(self, now=None):
        return bool(self.is_active and (now or utc_now()) <= self.expires_at)


class Attendance(db.Model):
    SOURCE_SCAN = 'scan'
    SOURCE_MANUAL = 'manual'

    __table_args__ = (
        one_of('source', (SOURCE_SCAN, SOURCE_MANUAL), 'ck_attendance_source'),
        db.Index('uq_attendance_student_session', 'student_id', 'session_id', unique=True),
        # One QR scan grant produces at most one attendance (NULLs allowed many times).
        db.Index('uq_attendance_scan_grant', 'scan_grant', unique=True),
        # The unique index above starts with student_id, so lookups by session need their own.
        db.Index('ix_attendance_session_id', 'session_id'),
    )
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id', ondelete='CASCADE'), nullable=False)
    session_id = db.Column(db.Integer, db.ForeignKey('class_session.id', ondelete='CASCADE'), nullable=False)
    timestamp = db.Column(db.DateTime, default=utc_now)
    # 'scan' when the student scanned the QR, 'manual' when staff entered it.
    source = db.Column(db.String(10), nullable=False, default=SOURCE_SCAN, server_default=SOURCE_SCAN)
    marked_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey('user_account.id', ondelete='SET NULL', name='fk_attendance_marked_by_user'),
        nullable=True,
    )
    note = db.Column(db.String(200), nullable=True)
    # Address the scan link was opened from. Cleared after SCAN_IP_RETENTION_DAYS; never set for manual entries.
    scan_ip = db.Column(db.String(45), nullable=True)
    # Id of the scan grant this entry was marked with; never set for manual entries.
    scan_grant = db.Column(db.String(32), nullable=True)
    # True when this scan registered the student's phone (their first scan, or the first after a device
    # reset). The device lock is a browser cookie, so many of these at once deserve a look.
    new_device = db.Column(db.Boolean, nullable=False, default=False, server_default=sa.false())
    student = db.relationship('Student', backref=db.backref('attendances', cascade='all, delete-orphan'))
    session = db.relationship('ClassSession', backref=db.backref('attendances', cascade='all, delete-orphan'))
    marked_by = db.relationship('User', foreign_keys=[marked_by_user_id])

    @property
    def is_manual(self):
        return self.source == self.SOURCE_MANUAL


class ActivityLog(db.Model):
    __table_args__ = (
        db.Index('ix_activity_log_created_at', 'created_at'),
        db.Index('ix_activity_log_actor_user_id', 'actor_user_id'),
        db.Index('uq_activity_log_chain_seq', 'chain_seq', unique=True),
    )
    id = db.Column(db.Integer, primary_key=True)
    actor_user_id = db.Column(db.Integer, db.ForeignKey('user_account.id', ondelete='SET NULL'), nullable=True)
    actor_username = db.Column(db.String(120), nullable=False)
    department_id = db.Column(db.Integer, db.ForeignKey('department.id', ondelete='SET NULL'), nullable=True)
    action = db.Column(db.String(80), nullable=False)
    target_type = db.Column(db.String(40), nullable=True)
    target_id = db.Column(db.Integer, nullable=True)
    # Text, not a short string: the reason for a correction comes last and must not be cut off.
    description = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=utc_now, nullable=False)
    # Tamper evidence (services/audit.py): position in the chain and a keyed hash over this
    # entry and the previous one. Empty until the entry is sealed, right after its request.
    chain_seq = db.Column(db.Integer, nullable=True)
    entry_hash = db.Column(db.String(64), nullable=True)

    actor = db.relationship('User', foreign_keys=[actor_user_id])
    department = db.relationship('Department', foreign_keys=[department_id])


class AuditChain(db.Model):
    """The one row that records the end of the activity log's hash chain.

    Sealing locks this row first, so concurrent requests extend the chain one at a time.
    """
    id = db.Column(db.Integer, primary_key=True)
    last_seq = db.Column(db.Integer, nullable=False, default=0, server_default='0')
    last_hash = db.Column(db.String(64), nullable=False, default='', server_default='')


class LeaveRequest(db.Model):
    """A student's request to be excused from classes on some days (medical, personal, college duty).

    Once approved, classes held on those days do not count for the student either way:
    they are left out of both classes held and classes attended.
    """
    PENDING = 'pending'
    APPROVED = 'approved'
    REJECTED = 'rejected'
    KINDS: ClassVar[dict[str, str]] = {'medical': 'Medical', 'personal': 'Personal', 'duty': 'College duty'}

    __table_args__ = (
        one_of('status', (PENDING, APPROVED, REJECTED), 'ck_leave_request_status'),
        db.CheckConstraint('end_date >= start_date', name='ck_leave_request_dates'),
        db.Index('ix_leave_request_student_status', 'student_id', 'status'),
    )
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id', ondelete='CASCADE'), nullable=False)
    # Calendar days in the college's time zone, both included.
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    kind = db.Column(db.String(20), nullable=False)
    reason = db.Column(db.String(300), nullable=False)
    status = db.Column(db.String(10), nullable=False, default=PENDING, server_default=PENDING)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)
    decided_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey('user_account.id', ondelete='SET NULL', name='fk_leave_request_decided_by_user'),
        nullable=True,
    )
    decided_at = db.Column(db.DateTime, nullable=True)
    decision_note = db.Column(db.String(200), nullable=True)

    student = db.relationship('Student', backref=db.backref('leave_requests', cascade='all, delete-orphan'))
    decided_by = db.relationship('User', foreign_keys=[decided_by_user_id])

    @property
    def kind_label(self) -> str:
        return self.KINDS.get(self.kind) or str(self.kind).title()

    @property
    def days(self):
        return (self.end_date - self.start_date).days + 1


class SpotCheck(db.Model):
    """A teacher checking that a randomly chosen student marked present is really in the room."""
    PENDING = 'pending'
    PRESENT = 'present'
    ABSENT = 'absent'

    __table_args__ = (
        one_of('result', (PENDING, PRESENT, ABSENT), 'ck_spot_check_result'),
        db.Index('uq_spot_check_session_student', 'session_id', 'student_id', unique=True),
        db.Index('ix_spot_check_student_id', 'student_id'),
    )
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey('class_session.id', ondelete='CASCADE'), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id', ondelete='CASCADE'), nullable=False)
    result = db.Column(db.String(10), nullable=False, default=PENDING, server_default=PENDING)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)
    checked_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey('user_account.id', ondelete='SET NULL', name='fk_spot_check_checked_by_user'),
        nullable=True,
    )
    resolved_at = db.Column(db.DateTime, nullable=True)

    session = db.relationship('ClassSession', backref=db.backref('spot_checks', cascade='all, delete-orphan'))
    student = db.relationship('Student', backref=db.backref('spot_checks', cascade='all, delete-orphan'))
    checked_by = db.relationship('User', foreign_keys=[checked_by_user_id])


class BellSchedule(db.Model):
    """The periods of a day ("Weekday", "Saturday half-day"). department_id NULL: the institution's;
    set: that department's own version, used before the institution's on the days it is assigned."""
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(60), nullable=False)
    department_id = db.Column(db.Integer, db.ForeignKey('department.id', ondelete='CASCADE'), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)

    department = db.relationship('Department')
    periods = db.relationship('BellPeriod', order_by='BellPeriod.position', cascade='all, delete-orphan',
                              passive_deletes=True, backref='schedule')
    days = db.relationship('BellScheduleDay', cascade='all, delete-orphan', passive_deletes=True, backref='schedule')


class BellPeriod(db.Model):
    __table_args__ = (
        db.CheckConstraint('end_time > start_time', name='ck_bell_period_times'),
        db.Index('ix_bell_period_schedule_id', 'schedule_id'),
    )
    id = db.Column(db.Integer, primary_key=True)
    schedule_id = db.Column(db.Integer, db.ForeignKey('bell_schedule.id', ondelete='CASCADE'), nullable=False)
    position = db.Column(db.Integer, nullable=False)
    label = db.Column(db.String(20), nullable=False)  # "1", "2", "Break"
    start_time = db.Column(db.Time, nullable=False)   # local wall-clock time
    end_time = db.Column(db.Time, nullable=False)
    is_break = db.Column(db.Boolean, nullable=False, default=False, server_default=sa.false())


class BellScheduleDay(db.Model):
    """A weekday (0 = Monday ... 6 = Sunday) that uses the schedule. One per weekday within a scope (checked in code)."""
    __table_args__ = (
        db.CheckConstraint('weekday BETWEEN 0 AND 6', name='ck_bell_schedule_day_weekday'),
        db.Index('ix_bell_schedule_day_schedule_id', 'schedule_id'),
    )
    id = db.Column(db.Integer, primary_key=True)
    schedule_id = db.Column(db.Integer, db.ForeignKey('bell_schedule.id', ondelete='CASCADE'), nullable=False)
    weekday = db.Column(db.Integer, nullable=False)


class Holiday(db.Model):
    """Days without classes. department_id NULL: for everyone; set: that department only."""
    __table_args__ = (
        db.CheckConstraint('end_date >= start_date', name='ck_holiday_dates'),
    )
    id = db.Column(db.Integer, primary_key=True)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    title = db.Column(db.String(100), nullable=False)
    department_id = db.Column(db.Integer, db.ForeignKey('department.id', ondelete='CASCADE'), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)

    department = db.relationship('Department')


class Timetable(db.Model):
    """One version of one class's routine. A draft is edited freely; a published version never changes.

    Publishing a draft from date D ends the version before it on D - 1, so versions never overlap
    and every past week keeps the routine it really had.
    """
    DRAFT = 'draft'
    PUBLISHED = 'published'

    __table_args__ = (
        one_of('status', (DRAFT, PUBLISHED), 'ck_timetable_status'),
        db.Index('ix_timetable_semester_id', 'semester_id'),
    )
    id = db.Column(db.Integer, primary_key=True)
    department_id = db.Column(db.Integer, db.ForeignKey('department.id', ondelete='CASCADE'), nullable=False)
    semester_id = db.Column(db.Integer, db.ForeignKey('semester.id', ondelete='CASCADE'), nullable=False)
    status = db.Column(db.String(10), nullable=False, default=DRAFT, server_default=DRAFT)
    effective_from = db.Column(db.Date, nullable=True)  # set on publishing
    effective_to = db.Column(db.Date, nullable=True)    # set when a newer version is published
    note = db.Column(db.String(200), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)
    created_by_user_id = db.Column(
        db.Integer, db.ForeignKey('user_account.id', ondelete='SET NULL', name='fk_timetable_created_by_user'),
        nullable=True,
    )
    published_at = db.Column(db.DateTime, nullable=True)
    published_by_user_id = db.Column(
        db.Integer, db.ForeignKey('user_account.id', ondelete='SET NULL', name='fk_timetable_published_by_user'),
        nullable=True,
    )

    department = db.relationship('Department')
    semester = db.relationship('Semester')
    created_by = db.relationship('User', foreign_keys=[created_by_user_id])
    published_by = db.relationship('User', foreign_keys=[published_by_user_id])
    entries = db.relationship('TimetableEntry', cascade='all, delete-orphan', passive_deletes=True,
                              backref='timetable', order_by='[TimetableEntry.weekday, TimetableEntry.start_time]')

    @property
    def is_draft(self):
        return self.status == self.DRAFT


class TimetableEntry(db.Model):
    """One class in the week: a weekday, local start and end times, a subject (or a title), a group, teachers, a room."""
    EVERY = 'every'
    ODD = 'odd'
    EVEN = 'even'

    __table_args__ = (
        db.CheckConstraint('weekday BETWEEN 0 AND 6', name='ck_timetable_entry_weekday'),
        db.CheckConstraint('end_time > start_time', name='ck_timetable_entry_times'),
        one_of('week_pattern', (EVERY, ODD, EVEN), 'ck_timetable_entry_week_pattern'),
        db.Index('ix_timetable_entry_timetable_id', 'timetable_id'),
        db.Index('ix_timetable_entry_subject_id', 'subject_id'),
        db.Index('ix_timetable_entry_group_id', 'group_id'),
    )
    id = db.Column(db.Integer, primary_key=True)
    timetable_id = db.Column(db.Integer, db.ForeignKey('timetable.id', ondelete='CASCADE'), nullable=False)
    weekday = db.Column(db.Integer, nullable=False)  # 0 = Monday ... 6 = Sunday
    start_time = db.Column(db.Time, nullable=False)
    end_time = db.Column(db.Time, nullable=False)
    # A subject, or a title for something that is not one ("Special Tutorial Class"). Neither: an
    # unassigned entry, left by copying another class's routine; a draft cannot be published with one.
    # The app refuses to delete a subject or group a routine uses, so these keys are NO ACTION.
    subject_id = db.Column(db.Integer, db.ForeignKey('subject.id', name='fk_timetable_entry_subject'), nullable=True)
    title = db.Column(db.String(100), nullable=True)
    group_id = db.Column(db.Integer, db.ForeignKey('class_group.id', name='fk_timetable_entry_group'), nullable=True)
    room = db.Column(db.String(40), nullable=True)
    week_pattern = db.Column(db.String(5), nullable=False, default=EVERY, server_default=EVERY)
    valid_from = db.Column(db.Date, nullable=True)  # optional limits within the version's dates
    valid_to = db.Column(db.Date, nullable=True)
    note = db.Column(db.String(200), nullable=True)

    subject = db.relationship('Subject')
    group = db.relationship('ClassGroup')
    teachers = db.relationship('Teacher', secondary='timetable_entry_teacher', order_by='Teacher.name')

    @property
    def is_unassigned(self):
        return self.subject_id is None and not self.title


class TimetableEntryTeacher(db.Model):
    __table_args__ = (
        db.Index('uq_timetable_entry_teacher', 'entry_id', 'teacher_id', unique=True),
        db.Index('ix_timetable_entry_teacher_teacher_id', 'teacher_id'),
    )
    id = db.Column(db.Integer, primary_key=True)
    entry_id = db.Column(db.Integer, db.ForeignKey('timetable_entry.id', ondelete='CASCADE'), nullable=False)
    teacher_id = db.Column(db.Integer, db.ForeignKey('teacher.id', ondelete='CASCADE'), nullable=False)


class RoutineChange(db.Model):
    """A change to the routine for one date only: a class cancelled, moved or taken by a substitute,
    or an extra class. The routine itself stays as published."""
    CANCEL = 'cancel'
    MOVE = 'move'
    SUBSTITUTE = 'substitute'
    EXTRA = 'extra'
    KINDS: ClassVar[dict[str, str]] = {CANCEL: 'Cancelled', MOVE: 'Moved', SUBSTITUTE: 'Substitute', EXTRA: 'Extra class'}

    __table_args__ = (
        one_of('kind', (CANCEL, MOVE, SUBSTITUTE, EXTRA), 'ck_routine_change_kind'),
        db.Index('ix_routine_change_entry_date', 'timetable_entry_id', 'date'),
        db.Index('ix_routine_change_semester_date', 'semester_id', 'date'),
    )
    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(12), nullable=False)
    # The routine class it changes, and that class's date in the routine (not set for an extra class).
    timetable_entry_id = db.Column(db.Integer, db.ForeignKey('timetable_entry.id', ondelete='CASCADE'), nullable=True)
    date = db.Column(db.Date, nullable=False)
    # Move: the new date and times. Extra class: its times (on `date`).
    new_date = db.Column(db.Date, nullable=True)
    new_start_time = db.Column(db.Time, nullable=True)
    new_end_time = db.Column(db.Time, nullable=True)
    # Substitute: who takes the class. Extra class: who teaches it.
    substitute_teacher_id = db.Column(db.Integer, db.ForeignKey('teacher.id', ondelete='SET NULL'), nullable=True)
    # The class: copied from the entry, or chosen for an extra class.
    department_id = db.Column(db.Integer, db.ForeignKey('department.id', ondelete='CASCADE'), nullable=False)
    semester_id = db.Column(db.Integer, db.ForeignKey('semester.id', ondelete='CASCADE'), nullable=False)
    subject_id = db.Column(db.Integer, db.ForeignKey('subject.id', ondelete='CASCADE'), nullable=True)
    title = db.Column(db.String(100), nullable=True)
    group_id = db.Column(db.Integer, db.ForeignKey('class_group.id', ondelete='CASCADE'), nullable=True)
    room = db.Column(db.String(40), nullable=True)
    reason = db.Column(db.String(200), nullable=False)
    # The session that held an extra class (a routine class finds its session by entry and date).
    session_id = db.Column(db.Integer, db.ForeignKey('class_session.id', ondelete='SET NULL'), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)
    created_by_user_id = db.Column(
        db.Integer, db.ForeignKey('user_account.id', ondelete='SET NULL', name='fk_routine_change_created_by_user'),
        nullable=True,
    )

    entry = db.relationship('TimetableEntry')
    substitute = db.relationship('Teacher')
    semester = db.relationship('Semester')
    subject = db.relationship('Subject')
    group = db.relationship('ClassGroup')
    session = db.relationship('ClassSession')
    created_by = db.relationship('User', foreign_keys=[created_by_user_id])

    @property
    def kind_label(self):
        return self.KINDS.get(self.kind, self.kind)


class InstitutionSetting(db.Model):
    """An institution-wide option the Principal changed in the app (services/settings.py).

    One row per changed option; no row means the server's default, from the environment or
    config.py. Key/value rather than one wide row, so new options need no migration.
    """
    key = db.Column(db.String(80), primary_key=True)
    value = db.Column(db.Text, nullable=False)  # JSON
    updated_at = db.Column(db.DateTime, nullable=False, default=utc_now)
    updated_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey('user_account.id', ondelete='SET NULL', name='fk_institution_setting_updated_by_user'),
        nullable=True,
    )

    updated_by = db.relationship('User', foreign_keys=[updated_by_user_id])


class Condonation(db.Model):
    """An HOD or the Principal allowing a student in the condonation band to sit the exams anyway."""
    __table_args__ = (
        db.Index('uq_condonation_student_semester', 'student_id', 'semester_id', unique=True),
    )
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id', ondelete='CASCADE'), nullable=False)
    semester_id = db.Column(db.Integer, db.ForeignKey('semester.id', ondelete='CASCADE'), nullable=False)
    reason = db.Column(db.String(200), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)
    granted_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey('user_account.id', ondelete='SET NULL', name='fk_condonation_granted_by_user'),
        nullable=True,
    )

    student = db.relationship('Student', backref=db.backref('condonations', cascade='all, delete-orphan'))
    granted_by = db.relationship('User', foreign_keys=[granted_by_user_id])
