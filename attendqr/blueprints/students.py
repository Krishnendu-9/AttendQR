import csv
import io
from urllib.parse import urlsplit

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, send_file, url_for
from flask_login import login_required
from sqlalchemy import or_
from sqlalchemy.orm import joinedload
from werkzeug.security import generate_password_hash

from ..access import (
    can_access_department,
    can_access_student,
    current_department_id,
    email_format_rule_message,
    is_super_admin,
    is_valid_email,
    log_activity,
    ordered_departments_query,
    role_required,
    visible_semesters_query,
    visible_students_query,
)
from ..extensions import db
from ..models import Admin, Semester, Student
from ..security import clear_login_failures, is_safe_redirect_target
from ..services.roster import (
    ensure_student_account,
    import_students_from_rows,
    import_template_rows,
    parse_student_import,
)
from ..utils import normalize_email, normalize_roll_no, normalize_text, parse_int_value, sort_semesters

bp = Blueprint('students', __name__)


def student_filters():
    return {
        'q': normalize_text(request.args.get('q'))[:100],
        'department_id': request.args.get('department_id', type=int),
        'semester_id': request.args.get('semester_id', type=int),
    }


def back_to_list():
    """Return to the list with the filters the user was looking at."""
    target = request.form.get('return_to')
    if is_safe_redirect_target(target) and urlsplit(target).path == url_for('students.index'):
        return redirect(target)
    return redirect(url_for('students.index'))


def _student_form():
    """Validate add/edit fields. Returns (values, error)."""
    values = {
        'name': normalize_text(request.form.get('name')),
        'roll_no': normalize_roll_no(request.form.get('roll_no')),
        'email': normalize_email(request.form.get('email')),
        'department_id': parse_int_value(request.form.get('department_id')) if is_super_admin() else current_department_id(),
        'semester_id': parse_int_value(request.form.get('semester_id')),
    }
    if not values['name'] or not values['roll_no'] or not values['email']:
        return None, "Name, roll number, and email are required."
    if len(values['name']) > 100 or len(values['roll_no']) > 20:
        return None, "Name must be at most 100 characters and roll number at most 20."
    if not is_valid_email(values['email']):
        return None, email_format_rule_message()
    if not values['department_id'] or not values['semester_id']:
        return None, "Valid department and semester are required."
    if not can_access_department(values['department_id']):
        abort(403)
    semester = db.session.get(Semester, values['semester_id'])
    if not semester or semester.department_id != values['department_id']:
        return None, "Selected semester does not belong to the selected department."
    return values, None


def _duplicate_error(values, student=None):
    roll_match = Student.query.filter(
        db.func.upper(Student.roll_no) == values['roll_no'],
        Student.department_id == values['department_id'],
        Student.semester_id == values['semester_id'],
    )
    email_match = Student.query.filter(db.func.lower(Student.email) == values['email'])
    login_match = Admin.query.filter(db.func.lower(Admin.username) == values['email'])
    if student is not None:
        roll_match = roll_match.filter(Student.id != student.id)
        email_match = email_match.filter(Student.id != student.id)
        login_match = login_match.filter(or_(Admin.student_id.is_(None), Admin.student_id != student.id))
    if roll_match.first():
        return f"Roll number {values['roll_no']} already exists in this department and semester."
    if email_match.first():
        return f"Email {values['email']} already exists."
    if login_match.first():
        return f"Login account email {values['email']} already exists."
    return None


@bp.route('/students', methods=['GET', 'POST'])
@login_required
@role_required('super_admin', 'hod', 'teacher')
def index():
    if request.method == 'POST':
        values, error = _student_form()
        error = error or _duplicate_error(values)
        if error:
            flash(error, "danger")
            return back_to_list()
        student = Student(**values)
        db.session.add(student)
        db.session.flush()
        ensure_student_account(student)
        log_activity(
            'student_created',
            f"Created student profile '{student.name}' ({student.roll_no}).",
            'student', student.id, student.department_id,
        )
        db.session.commit()
        flash(f"Added {student.name}. Their initial password is the roll number.", "success")
        return back_to_list()

    filters = student_filters()
    query = visible_students_query().options(joinedload(Student.department), joinedload(Student.semester))
    if filters['department_id']:
        query = query.filter(Student.department_id == filters['department_id'])
    if filters['semester_id']:
        query = query.filter(Student.semester_id == filters['semester_id'])
    if filters['q']:
        like = f"%{filters['q'].lower()}%"
        query = query.filter(or_(
            db.func.lower(Student.name).like(like),
            db.func.lower(Student.roll_no).like(like),
            db.func.lower(Student.email).like(like),
        ))
    page = query.order_by(Student.roll_no).paginate(
        page=request.args.get('page', 1, type=int),
        per_page=current_app.config['STUDENTS_PER_PAGE'],
        error_out=False,
    )

    semesters = visible_semesters_query()
    if filters['department_id']:
        semesters = semesters.filter(Semester.department_id == filters['department_id'])

    return render_template(
        'students.html',
        page=page,
        filters=filters,
        filter_args={key: value for key, value in filters.items() if value},
        total_students=visible_students_query().count(),
        departments=ordered_departments_query().all(),
        filter_semesters=sort_semesters(semesters.all()),
    )


@bp.route('/students/import', methods=['POST'])
@login_required
@role_required('super_admin', 'hod', 'teacher')
def import_students():
    upload = request.files.get('student_file')
    if not upload or not upload.filename:
        flash("Please choose a CSV or XLSX file.", "danger")
        return redirect(url_for('students.index'))

    try:
        rows = parse_student_import(upload)
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for('students.index'))
    except Exception:
        current_app.logger.exception("Could not read student import file")
        flash("The file could not be read. Upload a valid CSV or XLSX file.", "danger")
        return redirect(url_for('students.index'))

    if not rows:
        flash("The uploaded file has no student rows.", "warning")
        return redirect(url_for('students.index'))

    try:
        imported, errors = import_students_from_rows(rows)
        if imported:
            log_activity('student_imported', f"Imported {imported} student profile(s) by bulk upload.", 'student', None, current_department_id())
            db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Student import failed")
        flash("Import failed because of an unexpected error. No students were added.", "danger")
        return redirect(url_for('students.index'))

    if imported:
        flash(
            f"Imported {imported} student profile{'s' if imported != 1 else ''}. "
            "Student logins were created with roll number as initial password.",
            "success",
        )
    if errors:
        preview = '; '.join(errors[:5])
        suffix = f" and {len(errors) - 5} more" if len(errors) > 5 else ""
        flash(f"{len(errors)} row issue{'s' if len(errors) != 1 else ''}: {preview}{suffix}", "warning")
    return redirect(url_for('students.index'))


@bp.route('/students/import-template')
@login_required
@role_required('super_admin', 'hod', 'teacher')
def import_template():
    headers, sample = import_template_rows()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)
    writer.writerow(sample)
    return send_file(
        io.BytesIO(output.getvalue().encode('utf-8')),
        mimetype='text/csv',
        download_name='student_import_template.csv',
        as_attachment=True,
    )


@bp.route('/students/edit/<int:id>', methods=['POST'])
@login_required
@role_required('super_admin', 'hod')
def edit(id):
    student = db.get_or_404(Student, id)
    if not can_access_student(student):
        abort(403)
    values, error = _student_form()
    error = error or _duplicate_error(values, student)
    if error:
        flash(error, "danger")
        return back_to_list()
    for key, value in values.items():
        setattr(student, key, value)
    ensure_student_account(student)
    log_activity('student_updated', f"Updated student profile '{student.name}' ({student.roll_no}).", 'student', student.id, student.department_id)
    db.session.commit()
    return back_to_list()


@bp.route('/students/delete/<int:id>', methods=['POST'])
@login_required
@role_required('super_admin', 'hod')
def delete(id):
    student = db.get_or_404(Student, id)
    if not can_access_student(student):
        abort(403)
    log_activity('student_deleted', f"Deleted student profile '{student.name}' ({student.roll_no}).", 'student', student.id, student.department_id)
    Admin.query.filter_by(student_id=student.id).delete()
    db.session.delete(student)
    db.session.commit()
    return back_to_list()


@bp.route('/students/reset-password/<int:id>', methods=['POST'])
@login_required
@role_required('super_admin', 'hod', 'teacher')
def reset_password(id):
    student = db.get_or_404(Student, id)
    if not can_access_student(student):
        abort(403)

    ensure_student_account(student)
    db.session.flush()
    account = Admin.query.filter_by(student_id=student.id).first()
    if not account:
        flash(f"{student.name} has no login account because {student.email} is used by another account.", "danger")
        return back_to_list()

    account.password = generate_password_hash(student.roll_no)
    account.is_active = True
    account.must_change_password = True
    account.rotate_session_token()
    clear_login_failures(account.username)
    log_activity(
        'student_password_reset',
        f"Reset student password for '{student.name}' to the roll number.",
        'student', student.id, student.department_id,
    )
    db.session.commit()
    flash(f"Password for {student.name} was reset to the roll number ({student.roll_no}).", 'success')
    return back_to_list()


@bp.route('/students/reset-device/<int:id>', methods=['POST'])
@login_required
@role_required('super_admin', 'hod', 'teacher')
def reset_device(id):
    student = db.get_or_404(Student, id)
    if not can_access_student(student):
        abort(403)

    student.device_id = None
    log_activity(
        'student_device_reset',
        f"Reset registered device for student '{student.name}' ({student.roll_no}).",
        'student', student.id, student.department_id,
    )
    db.session.commit()
    flash(f"Device lock reset successfully for {student.name}.", "success")
    return back_to_list()
