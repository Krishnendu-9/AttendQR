from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime

db = SQLAlchemy()

class Admin(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), nullable=False, default='super_admin')
    department_id = db.Column(db.Integer, db.ForeignKey('department.id', ondelete='SET NULL'), nullable=True)
    teacher_id = db.Column(db.Integer, db.ForeignKey('teacher.id', ondelete='SET NULL'), nullable=True)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id', ondelete='SET NULL'), nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    must_change_password = db.Column(db.Boolean, nullable=False, default=False)
    failed_login_attempts = db.Column(db.Integer, nullable=False, default=0)
    locked_until = db.Column(db.DateTime, nullable=True)

    department = db.relationship('Department', foreign_keys=[department_id])
    teacher = db.relationship('Teacher', foreign_keys=[teacher_id])
    student = db.relationship('Student', foreign_keys=[student_id])

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
    )
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    roll_no = db.Column(db.String(20), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    department_id = db.Column(db.Integer, db.ForeignKey('department.id', ondelete='CASCADE'), nullable=False)
    semester_id = db.Column(db.Integer, db.ForeignKey('semester.id', ondelete='CASCADE'), nullable=False)

class ClassSession(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    subject_id = db.Column(db.Integer, db.ForeignKey('subject.id', ondelete='CASCADE'), nullable=False)
    teacher_id = db.Column(db.Integer, db.ForeignKey('teacher.id', ondelete='SET NULL'), nullable=True)
    special_title = db.Column(db.String(200), nullable=True)
    token = db.Column(db.String(100), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=False)
    subject = db.relationship('Subject', backref=db.backref('sessions', lazy=True))
    teacher = db.relationship('Teacher', backref=db.backref('sessions', lazy=True))

class Attendance(db.Model):
    __table_args__ = (
        db.UniqueConstraint('student_id', 'session_id', name='unique_student_session_attendance'),
    )
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id', ondelete='CASCADE'), nullable=False)
    session_id = db.Column(db.Integer, db.ForeignKey('class_session.id', ondelete='CASCADE'), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    student = db.relationship('Student', backref=db.backref('attendances', cascade='all, delete-orphan'))
    session = db.relationship('ClassSession', backref=db.backref('attendances', cascade='all, delete-orphan'))


class ActivityLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    actor_admin_id = db.Column(db.Integer, db.ForeignKey('admin.id', ondelete='SET NULL'), nullable=True)
    actor_username = db.Column(db.String(120), nullable=False)
    department_id = db.Column(db.Integer, db.ForeignKey('department.id', ondelete='SET NULL'), nullable=True)
    action = db.Column(db.String(80), nullable=False)
    target_type = db.Column(db.String(40), nullable=True)
    target_id = db.Column(db.Integer, nullable=True)
    description = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    actor = db.relationship('Admin', foreign_keys=[actor_admin_id])
    department = db.relationship('Department', foreign_keys=[department_id])
