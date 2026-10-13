from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import login_required
from sqlalchemy import or_

from ..access import (
    can_access_department,
    can_access_teacher,
    current_department_id,
    describe_delete_block,
    is_allowed_staff_email,
    is_super_admin,
    log_activity,
    ordered_departments_query,
    role_required,
    staff_email_rule_message,
    subject_display_name,
    user_role,
    visible_semesters_query,
    visible_subjects_query,
    visible_teachers_query,
)
from ..extensions import db
from ..models import Admin, Attendance, ClassSession, Department, Semester, Student, Subject, Teacher
from ..utils import (
    natural_key,
    normalize_branch,
    normalize_course_name,
    normalize_email,
    normalize_paper_code,
    normalize_text,
    parse_int_value,
    sort_semesters,
)

bp = Blueprint('academics', __name__)


def scoped_department_id_from_form():
    """Principals choose a department; everyone else is pinned to their own."""
    return parse_int_value(request.form.get('department_id')) if is_super_admin() else current_department_id()


# ── Departments ──────────────────────────────────────────────

def _department_form():
    branch = normalize_branch(request.form.get('branch'))
    course_name = normalize_course_name(request.form.get('name'))
    if not branch or not course_name:
        return None, None, "Branch and course name are required."
    if len(branch) > 20 or len(course_name) > 100:
        return None, None, "Branch must be at most 20 characters and course name at most 100."
    return branch, course_name, None


@bp.route('/departments', methods=['GET', 'POST'])
@login_required
@role_required('super_admin', 'hod')
def departments():
    if request.method == 'POST':
        if not is_super_admin():
            abort(403)
        branch, course_name, error = _department_form()
        if error:
            flash(error, "danger")
            return redirect(url_for('academics.departments'))
        existing = Department.query.filter(db.func.lower(Department.name) == course_name.lower()).first()
        if existing:
            flash(f"Department '{existing.display_name}' already exists.", "warning")
            return redirect(url_for('academics.departments'))
        department = Department(branch=branch, name=course_name)
        db.session.add(department)
        db.session.flush()
        log_activity('department_created', f"Created department '{department.display_name}'.", 'department', department.id, department.id)
        db.session.commit()
        return redirect(url_for('academics.departments'))
    return render_template('departments.html', departments=ordered_departments_query().all())


@bp.route('/departments/edit/<int:id>', methods=['POST'])
@login_required
@role_required('super_admin')
def edit_department(id):
    department = db.get_or_404(Department, id)
    branch, course_name, error = _department_form()
    if error:
        flash(error, "danger")
        return redirect(url_for('academics.departments'))
    existing = Department.query.filter(
        db.func.lower(Department.name) == course_name.lower(),
        Department.id != department.id,
    ).first()
    if existing:
        flash(f"Department '{existing.display_name}' already exists.", "warning")
        return redirect(url_for('academics.departments'))
    department.branch = branch
    department.name = course_name
    log_activity('department_updated', f"Updated department '{department.display_name}'.", 'department', department.id, department.id)
    db.session.commit()
    return redirect(url_for('academics.departments'))


@bp.route('/departments/delete/<int:id>', methods=['POST'])
@login_required
@role_required('super_admin')
def delete_department(id):
    department = db.get_or_404(Department, id)
    counts = {
        'admins': Admin.query.filter(Admin.department_id == id, Admin.role != 'super_admin').count(),
        'teachers': Teacher.query.filter_by(department_id=id).count(),
        'students': Student.query.filter_by(department_id=id).count(),
        'subjects': Subject.query.filter_by(department_id=id).count(),
        'semesters': Semester.query.filter_by(department_id=id).count(),
        'sessions': ClassSession.query.join(Subject).filter(Subject.department_id == id).count(),
        'attendance': Attendance.query.join(ClassSession).join(Subject).filter(Subject.department_id == id).count(),
    }
    if any(counts.values()):
        flash(describe_delete_block(department.display_name, counts), 'warning')
        return redirect(url_for('academics.departments'))

    log_activity('department_deleted', f"Deleted department '{department.display_name}'.", 'department', department.id)
    db.session.delete(department)
    db.session.commit()
    return redirect(url_for('academics.departments'))


# ── Semesters ────────────────────────────────────────────────

@bp.route('/semesters', methods=['GET', 'POST'])
@login_required
@role_required('super_admin', 'hod')
def semesters():
    if request.method == 'POST':
        department_id = scoped_department_id_from_form()
        name = normalize_text(request.form.get('name'))
        if not name or len(name) > 50:
            flash("Semester name is required (at most 50 characters).", "danger")
            return redirect(url_for('academics.semesters'))
        if not department_id or not db.session.get(Department, department_id):
            flash("Valid department is required.", "danger")
            return redirect(url_for('academics.semesters'))
        if not can_access_department(department_id):
            abort(403)
        if Semester.query.filter(
            Semester.department_id == department_id,
            db.func.lower(Semester.name) == name.lower(),
        ).first():
            flash(f"Semester '{name}' already exists for this department.", "warning")
            return redirect(url_for('academics.semesters'))
        semester = Semester(name=name, department_id=department_id)
        db.session.add(semester)
        db.session.flush()
        log_activity(
            'semester_created',
            f"Created semester '{semester.name}' for {semester.department.display_name}.",
            'semester', semester.id, department_id,
        )
        db.session.commit()
        return redirect(url_for('academics.semesters'))

    semester_rows = sorted(
        visible_semesters_query().all(),
        key=lambda s: (natural_key(s.department.display_name), natural_key(s.name)),
    )
    return render_template('semesters.html', semesters=semester_rows, departments=ordered_departments_query().all())


@bp.route('/semesters/edit/<int:id>', methods=['POST'])
@login_required
@role_required('super_admin', 'hod')
def edit_semester(id):
    semester = db.get_or_404(Semester, id)
    if not can_access_department(semester.department_id):
        abort(403)
    name = normalize_text(request.form.get('name'))
    if not name or len(name) > 50:
        flash("Semester name is required (at most 50 characters).", "danger")
        return redirect(url_for('academics.semesters'))
    department_id = scoped_department_id_from_form()
    if not department_id or not db.session.get(Department, department_id):
        flash("Valid department is required.", "danger")
        return redirect(url_for('academics.semesters'))
    if not can_access_department(department_id):
        abort(403)
    if Semester.query.filter(
        Semester.id != semester.id,
        Semester.department_id == department_id,
        db.func.lower(Semester.name) == name.lower(),
    ).first():
        flash(f"Semester '{name}' already exists for this department.", "warning")
        return redirect(url_for('academics.semesters'))
    if department_id != semester.department_id and (
        Student.query.filter_by(semester_id=semester.id).count()
        or Subject.query.filter_by(semester_id=semester.id).count()
    ):
        flash("Move or remove this semester's students and subjects before changing its department.", "warning")
        return redirect(url_for('academics.semesters'))
    semester.name = name
    semester.department_id = department_id
    log_activity('semester_updated', f"Updated semester '{semester.name}'.", 'semester', semester.id, department_id)
    db.session.commit()
    return redirect(url_for('academics.semesters'))


@bp.route('/semesters/delete/<int:id>', methods=['POST'])
@login_required
@role_required('super_admin', 'hod')
def delete_semester(id):
    semester = db.get_or_404(Semester, id)
    if not can_access_department(semester.department_id):
        abort(403)
    counts = {
        'students': Student.query.filter_by(semester_id=id).count(),
        'subjects': Subject.query.filter_by(semester_id=id).count(),
        'sessions': ClassSession.query.join(Subject).filter(Subject.semester_id == id).count(),
        'attendance': Attendance.query.join(ClassSession).join(Subject).filter(Subject.semester_id == id).count(),
    }
    if any(counts.values()):
        flash(describe_delete_block(f"semester '{semester.name}'", counts), 'warning')
        return redirect(url_for('academics.semesters'))

    log_activity('semester_deleted', f"Deleted semester '{semester.name}'.", 'semester', semester.id, semester.department_id)
    db.session.delete(semester)
    db.session.commit()
    return redirect(url_for('academics.semesters'))


# ── Subjects ─────────────────────────────────────────────────

def _subject_form(subject=None):
    """Validate the subject form. Returns (values, error); aborts on scope violations."""
    department_id = scoped_department_id_from_form()
    semester_id = parse_int_value(request.form.get('semester_id'))
    teacher_id = parse_int_value(request.form.get('teacher_id'))
    paper_code = normalize_paper_code(request.form.get('paper_code'))
    name = normalize_text(request.form.get('name'))

    if not paper_code or not name:
        return None, "Paper code and subject name are required."
    if len(paper_code) > 50 or len(name) > 100:
        return None, "Paper code must be at most 50 characters and subject name at most 100."
    if not department_id or not semester_id:
        return None, "Valid department and semester are required."
    semester = db.session.get(Semester, semester_id)
    if not semester:
        return None, "Valid department and semester are required."
    if not can_access_department(department_id) or semester.department_id != department_id:
        abort(403)

    others = Subject.query.filter(Subject.department_id == department_id)
    if subject is not None:
        others = others.filter(Subject.id != subject.id)
    if others.filter(Subject.semester_id == semester_id, db.func.lower(Subject.name) == name.lower()).first():
        return None, f"Subject '{name}' already exists for this semester."
    if others.filter(db.func.upper(Subject.paper_code) == paper_code).first():
        return None, f"Paper code '{paper_code}' already exists for this department."

    if teacher_id:
        teacher = db.get_or_404(Teacher, teacher_id)
        if teacher.department_id != department_id or not can_access_teacher(teacher):
            abort(403)

    return {
        'name': name,
        'paper_code': paper_code,
        'department_id': department_id,
        'semester_id': semester_id,
        'teacher_id': teacher_id,
    }, None


@bp.route('/subjects', methods=['GET', 'POST'])
@login_required
@role_required('super_admin', 'hod', 'teacher')
def subjects():
    if request.method == 'POST':
        if user_role() == 'teacher':
            abort(403)
        values, error = _subject_form()
        if error:
            flash(error, "warning" if "already exists" in error else "danger")
            return redirect(url_for('academics.subjects'))
        subject = Subject(**values)
        db.session.add(subject)
        db.session.flush()
        teacher_suffix = f" and assigned to {subject.teacher.name}" if subject.teacher else ""
        log_activity(
            'subject_created',
            f"Created subject '{subject_display_name(subject)}'{teacher_suffix}.",
            'subject', subject.id, subject.department_id,
        )
        db.session.commit()
        return redirect(url_for('academics.subjects'))

    return render_template(
        'subjects.html',
        subjects=visible_subjects_query().order_by(Subject.paper_code, Subject.name).all(),
        departments=ordered_departments_query().all(),
        semesters=sort_semesters(visible_semesters_query().all()),
        teachers=visible_teachers_query().order_by(Teacher.name).all(),
    )


@bp.route('/subjects/edit/<int:id>', methods=['POST'])
@login_required
@role_required('super_admin', 'hod')
def edit_subject(id):
    subject = db.get_or_404(Subject, id)
    if not can_access_department(subject.department_id):
        abort(403)
    values, error = _subject_form(subject)
    if error:
        flash(error, "warning" if "already exists" in error else "danger")
        return redirect(url_for('academics.subjects'))
    for key, value in values.items():
        setattr(subject, key, value)
    db.session.flush()
    teacher_suffix = f" Assigned teacher: {subject.teacher.name}." if subject.teacher else " No teacher assigned."
    log_activity(
        'subject_updated',
        f"Updated subject '{subject_display_name(subject)}'.{teacher_suffix}",
        'subject', subject.id, subject.department_id,
    )
    db.session.commit()
    return redirect(url_for('academics.subjects'))


@bp.route('/subjects/delete/<int:id>', methods=['POST'])
@login_required
@role_required('super_admin', 'hod')
def delete_subject(id):
    subject = db.get_or_404(Subject, id)
    if not can_access_department(subject.department_id):
        abort(403)
    counts = {
        'sessions': ClassSession.query.filter_by(subject_id=id).count(),
        'attendance': Attendance.query.join(ClassSession).filter(ClassSession.subject_id == id).count(),
    }
    if any(counts.values()):
        flash(describe_delete_block(f"subject '{subject.name}'", counts), 'warning')
        return redirect(url_for('academics.subjects'))
    log_activity('subject_deleted', f"Deleted subject '{subject_display_name(subject)}'.", 'subject', subject.id, subject.department_id)
    db.session.delete(subject)
    db.session.commit()
    return redirect(url_for('academics.subjects'))


# ── Teachers ─────────────────────────────────────────────────

@bp.route('/teachers')
@login_required
@role_required('super_admin', 'hod')
def teachers():
    return render_template(
        'teachers.html',
        teachers=visible_teachers_query().order_by(Teacher.name).all(),
        departments=ordered_departments_query().all(),
    )


def _teacher_links_block(teacher, label):
    assigned_subjects = Subject.query.filter_by(teacher_id=teacher.id).count()
    sessions_count = ClassSession.query.filter_by(teacher_id=teacher.id).count()
    if assigned_subjects or sessions_count:
        return describe_delete_block(label, {'assigned subjects': assigned_subjects, 'sessions': sessions_count})
    return None


@bp.route('/teachers/edit/<int:id>', methods=['POST'])
@login_required
@role_required('super_admin', 'hod')
def edit_teacher(id):
    teacher = db.get_or_404(Teacher, id)
    if not can_access_teacher(teacher):
        abort(403)
    department_id = scoped_department_id_from_form()
    if not department_id or not db.session.get(Department, department_id):
        flash("Valid department is required.", "danger")
        return redirect(url_for('academics.teachers'))
    if not can_access_department(department_id):
        abort(403)
    name = normalize_text(request.form.get('name'))
    email = normalize_email(request.form.get('email'))
    if not name:
        flash("Teacher name is required.", "danger")
        return redirect(url_for('academics.teachers'))
    if not is_allowed_staff_email(email):
        flash(staff_email_rule_message(), "danger")
        return redirect(url_for('academics.teachers'))
    if Teacher.query.filter(Teacher.id != teacher.id, db.func.lower(Teacher.email) == email).first():
        flash(f"Teacher email '{email}' already exists.", "warning")
        return redirect(url_for('academics.teachers'))
    if Admin.query.filter(
        db.func.lower(Admin.username) == email,
        or_(Admin.teacher_id.is_(None), Admin.teacher_id != teacher.id),
    ).first():
        flash(f"Account email '{email}' already exists.", "warning")
        return redirect(url_for('academics.teachers'))
    if department_id != teacher.department_id:
        block = _teacher_links_block(teacher, f"moving teacher '{teacher.name}'")
        if block:
            flash(block, 'warning')
            return redirect(url_for('academics.teachers'))
    teacher.name = name
    teacher.email = email
    teacher.department_id = department_id
    linked_admin = Admin.query.filter_by(teacher_id=teacher.id).first()
    if linked_admin:
        linked_admin.username = email
        linked_admin.department_id = department_id
    log_activity('teacher_updated', f"Updated teacher profile '{teacher.name}'.", 'teacher', teacher.id, department_id)
    db.session.commit()
    return redirect(url_for('academics.teachers'))


@bp.route('/teachers/delete/<int:id>', methods=['POST'])
@login_required
@role_required('super_admin', 'hod')
def delete_teacher(id):
    teacher = db.get_or_404(Teacher, id)
    if not can_access_teacher(teacher):
        abort(403)
    block = _teacher_links_block(teacher, f"teacher '{teacher.name}'")
    if block:
        flash(block, 'warning')
        return redirect(url_for('academics.teachers'))
    log_activity('teacher_deleted', f"Deleted teacher profile '{teacher.name}'.", 'teacher', teacher.id, teacher.department_id)
    Admin.query.filter_by(teacher_id=teacher.id).delete()
    db.session.delete(teacher)
    db.session.commit()
    return redirect(url_for('academics.teachers'))
