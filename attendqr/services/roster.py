"""Student accounts and bulk student import."""
import csv
import io

import openpyxl
from werkzeug.security import generate_password_hash

from ..access import can_access_department, current_department_id, is_super_admin, is_valid_email
from ..extensions import db
from ..models import Admin, Department, Semester, Student
from ..utils import normalize_course_name, normalize_email, normalize_header, normalize_roll_no, normalize_text


def ensure_student_account(student):
    """Create or update the login account linked to a student profile."""
    if not student.email:
        return
    existing = Admin.query.filter_by(student_id=student.id).first()
    if existing:
        existing.role = 'student'
        existing.username = student.email
        existing.department_id = student.department_id
        return
    if Admin.query.filter_by(username=student.email).first():
        return
    db.session.add(Admin(
        username=student.email,
        password=generate_password_hash(student.roll_no),
        role='student',
        department_id=student.department_id,
        student_id=student.id,
        is_active=True,
        must_change_password=True,
    ))


def department_labels(department):
    labels = {
        department.course_name.lower(),
        department.display_name.lower(),
        str(department.id),
    }
    if department.branch_name:
        labels.add(department.branch_name.lower())
        labels.add(f"{department.branch_name} - {department.course_name}".lower())
    return labels


def import_template_rows():
    headers = ['name', 'roll_no', 'email', 'semester']
    sample = ['Rahul Das', 'MCA2026001', 'mca2026001@rcciit.org.in', 'Semester 1']
    if is_super_admin():
        headers.append('department')
        department = Department.query.order_by(Department.branch, Department.name).first()
        # Use a label the importer actually resolves, taken from real data when possible.
        sample.append(department.display_name if department else 'MCA - MCA')
    return headers, sample


def parse_student_import(file_storage):
    filename = (file_storage.filename or '').lower()
    file_storage.stream.seek(0)
    if filename.endswith('.csv'):
        raw_bytes = file_storage.stream.read()
        try:
            content = raw_bytes.decode('utf-8-sig')
        except UnicodeDecodeError:
            content = raw_bytes.decode('latin-1', errors='replace')
        return list(csv.DictReader(io.StringIO(content)))

    if filename.endswith('.xlsx'):
        workbook = openpyxl.load_workbook(file_storage.stream, read_only=True, data_only=True)
        rows = list(workbook.active.iter_rows(values_only=True))
        if not rows:
            return []
        headers = [normalize_header(value) for value in rows[0]]
        return [
            {headers[index]: value for index, value in enumerate(values) if index < len(headers)}
            for values in rows[1:]
        ]

    raise ValueError("Upload a .csv or .xlsx file")


def row_value(row, *keys):
    normalized = {normalize_header(key): value for key, value in row.items() if key is not None}
    for key in keys:
        value = normalized.get(key)
        if value is not None:
            if isinstance(value, float) and value.is_integer():
                value = int(value)
            return str(value).strip()
    return ''


class _DepartmentResolver:
    def __init__(self):
        self.departments = Department.query.all() if is_super_admin() else []
        self.own = db.session.get(Department, current_department_id()) if not is_super_admin() else None

    def resolve(self, row):
        """Return (department, error)."""
        if not is_super_admin():
            return self.own, None

        dept_id = row_value(row, 'department_id', 'dept_id')
        if dept_id:
            match = next((d for d in self.departments if str(d.id) == dept_id), None)
            return match, None if match else f"department_id {dept_id} not found"

        dept_name = row_value(row, 'department', 'dept')
        if not dept_name:
            return None, "department or department_id is required"
        wanted = normalize_course_name(dept_name).lower()
        matches = [d for d in self.departments if wanted in department_labels(d)]
        if len(matches) == 1:
            return matches[0], None
        if len(matches) > 1:
            return None, f"department '{dept_name}' matches more than one department; use department_id"
        return None, f"department '{dept_name}' not found"


def resolve_import_semester(row, department_id):
    sem_id = row_value(row, 'semester_id', 'sem_id')
    if sem_id:
        try:
            semester = db.session.get(Semester, int(sem_id))
        except ValueError:
            semester = None
        if semester and semester.department_id == department_id:
            return semester

    sem_name = row_value(row, 'semester', 'sem')
    if sem_name:
        return Semester.query.filter(
            Semester.department_id == department_id,
            db.func.lower(Semester.name) == sem_name.lower(),
        ).first()
    return None


def import_students_from_rows(rows):
    imported = 0
    errors = []
    seen_rolls = set()
    seen_emails = set()
    resolver = _DepartmentResolver()

    for index, row in enumerate(rows, start=2):
        name = normalize_text(row_value(row, 'name', 'student_name'))
        roll_no = normalize_roll_no(row_value(row, 'roll_no', 'roll', 'roll_number'))
        email = normalize_email(row_value(row, 'email', 'student_email'))

        if not name or not roll_no or not email:
            errors.append(f"Row {index}: name, roll_no, and email are required")
            continue
        if not is_valid_email(email):
            errors.append(f"Row {index}: valid student email is required")
            continue
        department, department_error = resolver.resolve(row)
        if not department or not can_access_department(department.id):
            errors.append(f"Row {index}: {department_error or 'valid department is required'}")
            continue

        semester = resolve_import_semester(row, department.id)
        if not semester:
            errors.append(f"Row {index}: valid semester is required for {department.display_name}")
            continue

        roll_key = (roll_no, department.id, semester.id)
        if roll_key in seen_rolls:
            errors.append(f"Row {index}: duplicate roll number {roll_no} in uploaded file")
            continue
        if email in seen_emails:
            errors.append(f"Row {index}: duplicate email {email} in uploaded file")
            continue
        if Student.query.filter(
            db.func.upper(Student.roll_no) == roll_no,
            Student.department_id == department.id,
            Student.semester_id == semester.id,
        ).first():
            errors.append(f"Row {index}: roll number {roll_no} already exists")
            continue
        if Student.query.filter(db.func.lower(Student.email) == email).first():
            errors.append(f"Row {index}: email {email} already exists")
            continue
        if Admin.query.filter(db.func.lower(Admin.username) == email).first():
            errors.append(f"Row {index}: login account email {email} already exists")
            continue

        student = Student(name=name, roll_no=roll_no, email=email,
                          department_id=department.id, semester_id=semester.id)
        db.session.add(student)
        db.session.flush()
        ensure_student_account(student)
        imported += 1
        seen_rolls.add(roll_key)
        seen_emails.add(email)

    db.session.commit()
    return imported, errors
