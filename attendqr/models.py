import re
import secrets
from typing import ClassVar

import sqlalchemy as sa
from flask_login import UserMixin

from .extensions import db
from .utils import utc_now


def new_session_token():
    return secrets.token_hex(16)


class User(UserMixin, db.Model):
    """Every login: Principal, HOD, Teacher and Student accounts (see `role`)."""
    # Not "user": that is a reserved word in PostgreSQL.
    __tablename__ = 'user_account'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), nullable=False)  # 'principal', 'hod', 'teacher' or 'student'
    department_id = db.Column(db.Integer, db.ForeignKey('department.id', ondelete='SET NULL'), nullable=True)
    teacher_id = db.Column(db.Integer, db.ForeignKey('teacher.id', ondelete='SET NULL'), nullable=True)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id', ondelete='SET NULL'), nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    must_change_password = db.Column(db.Boolean, nullable=False, default=False)
    # Part of the login session identifier; rotating it signs out every existing session.
    session_token = db.Column(db.String(64), nullable=False, default=new_session_token)

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
    subjects = db.relationship('Subject', backref='semester', lazy=True)
    students = db.relationship('Student', backref='semester', lazy=True, foreign_keys='Student.semester_id')


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
    teacher = db.relationship('Teacher', backref=db.backref('assigned_subjects', lazy=True), foreign_keys=[teacher_id])


class Teacher(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    department_id = db.Column(db.Integer, db.ForeignKey('department.id', ondelete='CASCADE'), nullable=False)


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
    roll_no = db.Column(db.String(20), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    department_id = db.Column(db.Integer, db.ForeignKey('department.id', ondelete='CASCADE'), nullable=False)
    semester_id = db.Column(db.Integer, db.ForeignKey('semester.id', ondelete='CASCADE'), nullable=False)
    device_id = db.Column(db.String(100), nullable=True)


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
    # Sessions created from this moment on are not the student's.
    left_at = db.Column(db.DateTime, nullable=False)


class ClassSession(db.Model):
    __table_args__ = (
        db.Index('ix_class_session_subject_id', 'subject_id'),
        db.Index('ix_class_session_teacher_id', 'teacher_id'),
        db.Index('ix_class_session_created_at', 'created_at'),
    )
    id = db.Column(db.Integer, primary_key=True)
    subject_id = db.Column(db.Integer, db.ForeignKey('subject.id', ondelete='CASCADE'), nullable=False)
    teacher_id = db.Column(db.Integer, db.ForeignKey('teacher.id', ondelete='SET NULL'), nullable=True)
    special_title = db.Column(db.String(200), nullable=True)
    token = db.Column(db.String(100), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=utc_now)
    expires_at = db.Column(db.DateTime, nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    subject = db.relationship('Subject', backref=db.backref('sessions', lazy=True))
    teacher = db.relationship('Teacher', backref=db.backref('sessions', lazy=True))

    def is_live(self, now=None):
        return bool(self.is_active and (now or utc_now()) <= self.expires_at)


class Attendance(db.Model):
    SOURCE_SCAN = 'scan'
    SOURCE_MANUAL = 'manual'

    __table_args__ = (
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
    )
    id = db.Column(db.Integer, primary_key=True)
    actor_user_id = db.Column(db.Integer, db.ForeignKey('user_account.id', ondelete='SET NULL'), nullable=True)
    actor_username = db.Column(db.String(120), nullable=False)
    department_id = db.Column(db.Integer, db.ForeignKey('department.id', ondelete='SET NULL'), nullable=True)
    action = db.Column(db.String(80), nullable=False)
    target_type = db.Column(db.String(40), nullable=True)
    target_id = db.Column(db.Integer, nullable=True)
    description = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=utc_now, nullable=False)

    actor = db.relationship('User', foreign_keys=[actor_user_id])
    department = db.relationship('Department', foreign_keys=[department_id])


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
