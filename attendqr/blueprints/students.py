import csv
import io
import re
from urllib.parse import urlsplit

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, send_file, url_for
from flask_login import login_required
from sqlalchemy import or_
from sqlalchemy.orm import joinedload

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
from ..security import (
    clear_login_failures,
    generate_temporary_password,
    hash_temporary_password,
    is_safe_redirect_target,
)
from ..services.enrollment import record_class_change
from ..services.reports import build_student_attendance_summary
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


def safe_return_to(target):
    """The student list (with its filters) or one student's page, else None."""
    if not is_safe_redirect_target(target):
        return None
    path = urlsplit(target).path
    list_path = url_for('students.index')
    if path == list_path or re.fullmatch(re.escape(list_path) + r'/\d+', path):
        return target
    return None


def back_to_list():
    """Return to the list with the filters the user was looking at, or the student page the action came from."""
    return redirect(safe_return_to(request.form.get('return_to')) or url_for('students.index'))


def _one_time_password_notice(temporary_password):
    if not temporary_password:
        return "No new login was created because that email already has an account."
    return (
        f"Temporary password: {temporary_password} (shown only now; give it to the student, "
        "who must change it at first login)."
    )


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


def _error_field(error):
    """The form field a validation message is about, so the full-page form can show it there."""
    if error.startswith('Roll number '):
        return 'roll_no'
    if error.startswith(('Email ', 'Login account email ')) or error == email_format_rule_message():
        return 'email'
    if error.startswith(('Valid department and semester', 'Selected semester')):
        return 'semester_id'
    return None


def _render_form(student=None, error=None, status=200):
    """The add/edit page. After a failed save it shows what was typed, with the error on its field."""
    submitted = request.method == 'POST'
    source = request.form if submitted else None

    def value(name, default=''):
        return source.get(name, '') if submitted else default

    department_id = parse_int_value(value('department_id', student.department_id if student else '')) if is_super_admin() else current_department_id()
    departments = ordered_departments_query().all()
    if department_id is None and len(departments) == 1:
        department_id = departments[0].id
    semesters = visible_semesters_query().filter(Semester.department_id == department_id).all() if department_id else []

    default_return = url_for('students.view', id=student.id) if student else url_for('students.index')
    return_to = safe_return_to(value('return_to', request.args.get('return_to'))) or default_return

    field = _error_field(error) if error else None
    if error and not field:
        flash(error, "danger")
    return render_template(
        'students/form.html',
        student=student,
        values={
            'name': value('name', student.name if student else ''),
            'roll_no': value('roll_no', student.roll_no if student else ''),
            'email': value('email', student.email if student else ''),
            'department_id': department_id,
            'semester_id': parse_int_value(value('semester_id', student.semester_id if student else '')),
        },
        errors={field: error} if field else {},
        departments=departments,
        semesters=sort_semesters(semesters),
        return_to=return_to,
    ), status


def _save_failed(error, student=None):
    """A failed save: the full-page form shows again with the input; older forms go back to the list."""
    if request.form.get('form_page'):
        return _render_form(student, error=error, status=400)
    flash(error, "danger")
    return back_to_list()


def _find_student(id):
    """The student if it exists and the user may see it, else None (shown as a friendly not-found page)."""
    student = db.session.get(Student, id)
    return student if student is not None and can_access_student(student) else None


def _not_found():
    return render_template('students/not_found.html'), 404


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
            return _save_failed(error)
        student = Student(**values)
        db.session.add(student)
        db.session.flush()
        temporary_password = ensure_student_account(student)
        log_activity(
            'student_created',
            f"Created student profile '{student.name}' ({student.roll_no}).",
            'student', student.id, student.department_id,
        )
        db.session.commit()
        flash(f"Added {student.name}. {_one_time_password_notice(temporary_password)}", "success")
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


@bp.route('/students/new')
@login_required
@role_required('super_admin', 'hod', 'teacher')
def new():
    return _render_form()


@bp.route('/students/<int:id>')
@login_required
@role_required('super_admin', 'hod', 'teacher')
def view(id):
    student = _find_student(id)
    if student is None:
        return _not_found()
    return render_template(
        'students/view.html',
        student=student,
        account=Admin.query.filter_by(student_id=student.id).first(),
        summary=build_student_attendance_summary(student),
    )


@bp.route('/students/<int:id>/edit')
@login_required
@role_required('super_admin', 'hod')
def edit_form(id):
    student = _find_student(id)
    if student is None:
        return _not_found()
    return _render_form(student)


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
        created, errors = import_students_from_rows(rows)
        if created:
            log_activity('student_imported', f"Imported {len(created)} student profile(s) by bulk upload.", 'student', None, current_department_id())
            db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Student import failed")
        flash("Import failed because of an unexpected error. No students were added.", "danger")
        return redirect(url_for('students.index'))

    if errors:
        preview = '; '.join(errors[:5])
        suffix = f" and {len(errors) - 5} more" if len(errors) > 5 else ""
        flash(f"{len(errors)} row issue{'s' if len(errors) != 1 else ''}: {preview}{suffix}", "warning")
    if not created:
        return redirect(url_for('students.index'))

    # The one-time passwords exist only in this response, so show them here instead of redirecting.
    credentials = [{
        'roll_no': student.roll_no,
        'name': student.name,
        'email': student.email,
        'password': password or '',
    } for student, password in created]
    return render_template('import_result.html', credentials=credentials)


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
        return _save_failed(error, student)
    old_class = (student.department_id, student.semester_id)
    for key, value in values.items():
        setattr(student, key, value)
    # Keeps the student in the old class's reports for the sessions held before the move.
    record_class_change(student, *old_class)
    temporary_password = ensure_student_account(student)
    log_activity('student_updated', f"Updated student profile '{student.name}' ({student.roll_no}).", 'student', student.id, student.department_id)
    db.session.commit()
    if temporary_password:
        flash(f"Created a login for {student.name}. {_one_time_password_notice(temporary_password)}", "success")
    else:
        flash(f"Updated {student.name}.", "success")
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
    name = student.name
    db.session.delete(student)
    db.session.commit()
    flash(f"Deleted {name}.", "success")
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

    temporary_password = generate_temporary_password()
    account.password = hash_temporary_password(temporary_password)
    account.is_active = True
    account.must_change_password = True
    account.rotate_session_token()
    clear_login_failures(account.username)
    log_activity(
        'student_password_reset',
        f"Reset student password for '{student.name}' to a new temporary password.",
        'student', student.id, student.department_id,
    )
    db.session.commit()
    flash(f"Password for {student.name} was reset. {_one_time_password_notice(temporary_password)}", 'success')
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
