from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_

from ..access import (
    can_access_department,
    can_access_teacher,
    current_department_id,
    describe_delete_block,
    has_active_hod,
    is_allowed_staff_email,
    is_principal,
    log_activity,
    ordered_departments_query,
    role_label,
    role_required,
    staff_email_rule_message,
    subject_display_name,
    user_role,
    visible_semesters_query,
    visible_subjects_query,
    visible_teachers_query,
)
from ..extensions import db
from ..models import (
    Attendance,
    ClassGroup,
    ClassSession,
    Department,
    Semester,
    Student,
    Subject,
    Teacher,
    TimetableEntry,
    User,
)
from ..services.locks import semester_lock_error
from ..services.settings import get_setting, term, terms
from ..services.subject_colors import SUBJECT_COLORS, parse_color
from ..utils import (
    natural_key,
    normalize_branch,
    normalize_course_name,
    normalize_email,
    normalize_paper_code,
    normalize_text,
    parse_int_value,
    sort_semesters,
    utc_now,
)

bp = Blueprint('academics', __name__)


def scoped_department_id_from_form():
    """Principals choose a department; everyone else is pinned to their own."""
    return parse_int_value(request.form.get('department_id')) if is_principal() else current_department_id()


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
@role_required('principal', 'hod')
def departments():
    if request.method == 'POST':
        if not is_principal():
            abort(403)
        branch, course_name, error = _department_form()
        if error or not course_name:
            flash(error, "danger")
            return redirect(url_for('academics.departments'))
        existing = Department.query.filter(db.func.lower(Department.name) == course_name.lower()).first()
        if existing:
            flash(f"{term('department')} '{existing.display_name}' already exists.", "warning")
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
@role_required('principal')
def edit_department(id):
    department = db.get_or_404(Department, id)
    branch, course_name, error = _department_form()
    if error or not course_name:
        flash(error, "danger")
        return redirect(url_for('academics.departments'))
    existing = Department.query.filter(
        db.func.lower(Department.name) == course_name.lower(),
        Department.id != department.id,
    ).first()
    if existing:
        flash(f"{term('department')} '{existing.display_name}' already exists.", "warning")
        return redirect(url_for('academics.departments'))
    department.branch = branch
    department.name = course_name
    log_activity('department_updated', f"Updated department '{department.display_name}'.", 'department', department.id, department.id)
    db.session.commit()
    return redirect(url_for('academics.departments'))


@bp.route('/departments/delete/<int:id>', methods=['POST'])
@login_required
@role_required('principal')
def delete_department(id):
    department = db.get_or_404(Department, id)
    counts = {
        'staff accounts': User.query.filter(User.department_id == id, User.role != 'principal').count(),
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
@role_required('principal', 'hod')
def semesters():
    if request.method == 'POST':
        department_id = scoped_department_id_from_form()
        name = normalize_text(request.form.get('name'))
        if not name or len(name) > 50:
            flash(f"{term('semester')} name is required (at most 50 characters).", "danger")
            return redirect(url_for('academics.semesters'))
        if not department_id or not db.session.get(Department, department_id):
            flash(f"Valid {term('department').lower()} is required.", "danger")
            return redirect(url_for('academics.semesters'))
        if not can_access_department(department_id):
            abort(403)
        if Semester.query.filter(
            Semester.department_id == department_id,
            db.func.lower(Semester.name) == name.lower(),
        ).first():
            flash(f"{term('semester')} '{name}' already exists for this {term('department').lower()}.", "warning")
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
@role_required('principal', 'hod')
def edit_semester(id):
    semester = db.get_or_404(Semester, id)
    if not can_access_department(semester.department_id):
        abort(403)
    name = normalize_text(request.form.get('name'))
    if not name or len(name) > 50:
        flash(f"{term('semester')} name is required (at most 50 characters).", "danger")
        return redirect(url_for('academics.semesters'))
    department_id = scoped_department_id_from_form()
    if not department_id or not db.session.get(Department, department_id):
        flash(f"Valid {term('department').lower()} is required.", "danger")
        return redirect(url_for('academics.semesters'))
    if not can_access_department(department_id):
        abort(403)
    if Semester.query.filter(
        Semester.id != semester.id,
        Semester.department_id == department_id,
        db.func.lower(Semester.name) == name.lower(),
    ).first():
        flash(f"{term('semester')} '{name}' already exists for this {term('department').lower()}.", "warning")
        return redirect(url_for('academics.semesters'))
    if department_id != semester.department_id and (
        Student.query.filter_by(semester_id=semester.id).count()
        or Subject.query.filter_by(semester_id=semester.id).count()
    ):
        flash(f"Move or remove this {term('semester').lower()}'s students and {terms('subject').lower()} before "
              f"changing its {term('department').lower()}.", "warning")
        return redirect(url_for('academics.semesters'))
    semester.name = name
    semester.department_id = department_id
    log_activity('semester_updated', f"Updated semester '{semester.name}'.", 'semester', semester.id, department_id)
    db.session.commit()
    return redirect(url_for('academics.semesters'))


@bp.route('/semesters/<int:id>/lock', methods=['POST'])
@login_required
@role_required('principal', 'hod')
def lock_semester(id):
    """Make the semester's attendance final: nothing in it can change until the Principal unlocks it."""
    semester = db.get_or_404(Semester, id)
    if not can_access_department(semester.department_id):
        abort(403)
    if semester.attendance_locked:
        flash(f"Attendance for {semester.name} is already locked.", 'warning')
        return redirect(url_for('academics.semesters'))
    semester.attendance_locked_at = utc_now()
    semester.attendance_locked_by_user_id = current_user.id
    # The eligibility report of a final semester keeps the rules it was locked under.
    semester.locked_eligibility_percent = get_setting('eligibility_percent')
    semester.locked_condonation_percent = get_setting('condonation_percent')
    log_activity('attendance_locked', f"Locked attendance for {semester.department.display_name} {semester.name} "
                 f"(eligibility {semester.locked_eligibility_percent}%, condonation from "
                 f"{semester.locked_condonation_percent}%).", 'semester', semester.id, semester.department_id)
    db.session.commit()
    flash(f"Attendance for {semester.name} is locked. Only the {role_label('principal')} can unlock it.", 'success')
    return redirect(url_for('academics.semesters'))


@bp.route('/semesters/<int:id>/unlock', methods=['POST'])
@login_required
@role_required('principal')
def unlock_semester(id):
    semester = db.get_or_404(Semester, id)
    reason = normalize_text(request.form.get('note'))
    if not semester.attendance_locked:
        flash(f"Attendance for {semester.name} is not locked.", 'warning')
        return redirect(url_for('academics.semesters'))
    if not reason or len(reason) > 200:
        flash("Give a short reason for unlocking (at most 200 characters).", 'danger')
        return redirect(url_for('academics.semesters'))
    semester.attendance_locked_at = None
    semester.attendance_locked_by_user_id = None
    semester.locked_eligibility_percent = semester.locked_condonation_percent = None
    log_activity('attendance_unlocked', f"Unlocked attendance for {semester.department.display_name} "
                 f"{semester.name}. Reason: {reason}", 'semester', semester.id, semester.department_id)
    db.session.commit()
    flash(f"Attendance for {semester.name} is unlocked.", 'success')
    return redirect(url_for('academics.semesters'))


@bp.route('/semesters/delete/<int:id>', methods=['POST'])
@login_required
@role_required('principal', 'hod')
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
    group_id = parse_int_value(request.form.get('group_id'))
    co_teacher_ids = {parse_int_value(value) for value in request.form.getlist('co_teacher_ids')} - {None, teacher_id}
    # The page sends "0" before the checkbox, so an unticked box is "off"; a form without the field keeps it on.
    counts_values = request.form.getlist('counts_towards_attendance')
    counts = '1' in counts_values if counts_values else True
    # Its colour in the routine; a form without the field keeps the current one.
    if 'color' in request.form:
        try:
            color = parse_color(request.form.get('color'))
        except ValueError as exc:
            return None, str(exc)
    else:
        color = subject.color if subject is not None else None

    if not paper_code or not name:
        return None, f"{term('paper_code')} and {term('subject').lower()} name are required."
    if len(paper_code) > 50 or len(name) > 100:
        return None, (f"{term('paper_code')} must be at most 50 characters and {term('subject').lower()} name at "
                      "most 100.")
    if not department_id or not semester_id:
        return None, f"Valid {term('department').lower()} and {term('semester').lower()} are required."
    semester = db.session.get(Semester, semester_id)
    if not semester:
        return None, f"Valid {term('department').lower()} and {term('semester').lower()} are required."
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
    co_teachers = Teacher.query.filter(Teacher.id.in_(sorted(co_teacher_ids))).order_by(Teacher.name).all()
    if len(co_teachers) != len(co_teacher_ids) or any(
            t.department_id != department_id or not can_access_teacher(t) for t in co_teachers):
        return None, "Co-teachers must be teachers of the subject's department."
    if group_id:
        group = db.session.get(ClassGroup, group_id)
        if not group or group.semester_id != semester_id:
            return None, "The group must belong to the subject's semester."

    return {
        'name': name,
        'paper_code': paper_code,
        'department_id': department_id,
        'semester_id': semester_id,
        'teacher_id': teacher_id,
        'group_id': group_id,
        'counts_towards_attendance': counts,
        'color': color,
        'co_teachers': co_teachers,
    }, None


def _subject_details(subject):
    """What the activity log says about a subject's options besides its name."""
    parts = []
    if subject.co_teachers:
        parts.append("co-teachers: " + ', '.join(t.name for t in subject.co_teachers))
    if subject.group:
        parts.append(f"only for {subject.group.name}")
    if not subject.counts_towards_attendance:
        parts.append("does not count towards attendance")
    if subject.color:
        parts.append(f"colour {SUBJECT_COLORS[subject.color].lower()}")
    return f" ({'; '.join(parts)})" if parts else ""


@bp.route('/subjects', methods=['GET', 'POST'])
@login_required
@role_required('principal', 'hod', 'teacher')
def subjects():
    if request.method == 'POST':
        if user_role() == 'teacher':
            abort(403)
        values, error = _subject_form()
        if error or values is None:
            error = error or "Check the subject details and try again."
            flash(error, "warning" if "already exists" in error else "danger")
            return redirect(url_for('academics.subjects'))
        subject = Subject(**values)
        db.session.add(subject)
        db.session.flush()
        teacher_suffix = f" and assigned to {subject.teacher.name}" if subject.teacher else ""
        log_activity(
            'subject_created',
            f"Created subject '{subject_display_name(subject)}'{teacher_suffix}{_subject_details(subject)}.",
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
@role_required('principal', 'hod')
def edit_subject(id):
    subject = db.get_or_404(Subject, id)
    if not can_access_department(subject.department_id):
        abort(403)
    values, error = _subject_form(subject)
    if error or values is None:
        error = error or "Check the subject details and try again."
        flash(error, "warning" if "already exists" in error else "danger")
        return redirect(url_for('academics.subjects'))
    lock_error = semester_lock_error(subject.semester)
    if lock_error and bool(values['counts_towards_attendance']) != bool(subject.counts_towards_attendance):
        # Whether it counts changes every student's percentage, which a locked semester keeps final.
        flash(f"{lock_error} Until then, whether '{subject_display_name(subject)}' counts towards attendance "
              "cannot change.", 'warning')
        return redirect(url_for('academics.subjects'))
    moving = (values['department_id'], values['semester_id']) != (subject.department_id, subject.semester_id)
    sessions_count = ClassSession.query.filter_by(subject_id=subject.id).count() if moving else 0
    if moving and TimetableEntry.query.filter_by(subject_id=subject.id).count():
        flash(f"Subject '{subject_display_name(subject)}' is in this class's routine, so it cannot move to another "
              "class. Remove it from the routine first.", "warning")
        return redirect(url_for('academics.subjects'))
    if sessions_count:
        # Its sessions and attendance belong to the students of the current class;
        # moving it would put them in another class's reports.
        flash(
            f"Subject '{subject_display_name(subject)}' already has {sessions_count} QR session(s), so it cannot "
            "move to another semester or department. Create a new subject for the other class instead.",
            "warning",
        )
        return redirect(url_for('academics.subjects'))
    for key, value in values.items():
        setattr(subject, key, value)
    db.session.flush()
    teacher_suffix = f" Assigned teacher: {subject.teacher.name}." if subject.teacher else " No teacher assigned."
    log_activity(
        'subject_updated',
        f"Updated subject '{subject_display_name(subject)}'.{teacher_suffix}{_subject_details(subject)}",
        'subject', subject.id, subject.department_id,
    )
    db.session.commit()
    return redirect(url_for('academics.subjects'))


@bp.route('/subjects/delete/<int:id>', methods=['POST'])
@login_required
@role_required('principal', 'hod')
def delete_subject(id):
    subject = db.get_or_404(Subject, id)
    if not can_access_department(subject.department_id):
        abort(403)
    counts = {
        'sessions': ClassSession.query.filter_by(subject_id=id).count(),
        'attendance': Attendance.query.join(ClassSession).filter(ClassSession.subject_id == id).count(),
        'routine classes': TimetableEntry.query.filter_by(subject_id=id).count(),
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
@role_required('principal', 'hod')
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
@role_required('principal', 'hod')
def edit_teacher(id):
    teacher = db.get_or_404(Teacher, id)
    if not can_access_teacher(teacher):
        abort(403)
    department_id = scoped_department_id_from_form()
    if not department_id or not db.session.get(Department, department_id):
        flash(f"Valid {term('department').lower()} is required.", "danger")
        return redirect(url_for('academics.teachers'))
    if not can_access_department(department_id):
        abort(403)
    name = normalize_text(request.form.get('name'))
    email = normalize_email(request.form.get('email'))
    short_name = normalize_text(request.form.get('short_name')) or None
    if not name:
        flash("Teacher name is required.", "danger")
        return redirect(url_for('academics.teachers'))
    if short_name and len(short_name) > 10:
        flash("Keep the short name to 10 characters, such as SC or MG.", "danger")
        return redirect(url_for('academics.teachers'))
    if not is_allowed_staff_email(email):
        flash(staff_email_rule_message(), "danger")
        return redirect(url_for('academics.teachers'))
    if Teacher.query.filter(Teacher.id != teacher.id, db.func.lower(Teacher.email) == email).first():
        flash(f"Teacher email '{email}' already exists.", "warning")
        return redirect(url_for('academics.teachers'))
    if User.query.filter(
        db.func.lower(User.username) == email,
        or_(User.teacher_id.is_(None), User.teacher_id != teacher.id),
    ).first():
        flash(f"Account email '{email}' already exists.", "warning")
        return redirect(url_for('academics.teachers'))
    if department_id != teacher.department_id:
        block = _teacher_links_block(teacher, f"moving teacher '{teacher.name}'")
        if block:
            flash(block, 'warning')
            return redirect(url_for('academics.teachers'))
    linked_account = User.query.filter_by(teacher_id=teacher.id).first()
    if linked_account and linked_account.role != 'teacher':
        # The profile of an HOD who also teaches: its email is that HOD's login and its department
        # theirs, and only the Principal manages HOD accounts.
        if not is_principal() and email != normalize_email(teacher.email):
            flash(f"This teacher profile belongs to the {role_label(linked_account.role)} account "
                  f"'{linked_account.username}'. Only the {role_label('principal')} can change its email.", 'warning')
            return redirect(url_for('academics.teachers'))
        if (linked_account.role == 'hod' and linked_account.is_active and department_id != linked_account.department_id
                and has_active_hod(department_id, exclude_user_id=linked_account.id)):
            flash(f"This teacher profile belongs to the {role_label('hod')} account '{linked_account.username}', "
                  f"and that {term('department').lower()} already has an active {role_label('hod')}. "
                  f"Deactivate that {role_label('hod')} first.", 'warning')
            return redirect(url_for('academics.teachers'))
    teacher.name = name
    teacher.email = email
    teacher.department_id = department_id
    if 'short_name' in request.form:  # older forms without the field leave it alone
        teacher.short_name = short_name
    if linked_account:
        linked_account.username = email
        linked_account.department_id = department_id
    log_activity('teacher_updated', f"Updated teacher profile '{teacher.name}'.", 'teacher', teacher.id, department_id)
    db.session.commit()
    return redirect(url_for('academics.teachers'))


@bp.route('/teachers/delete/<int:id>', methods=['POST'])
@login_required
@role_required('principal', 'hod')
def delete_teacher(id):
    teacher = db.get_or_404(Teacher, id)
    if not can_access_teacher(teacher):
        abort(403)
    block = _teacher_links_block(teacher, f"teacher '{teacher.name}'")
    if block:
        flash(block, 'warning')
        return redirect(url_for('academics.teachers'))
    # Only teacher logins go with the profile. Another account that teaches with it (an HOD) keeps
    # its login: the Principal unlinks it, and only the Principal manages those accounts.
    other_logins = User.query.filter(User.teacher_id == teacher.id, User.role != 'teacher').all()
    if other_logins and not is_principal():
        flash(f"This teacher profile belongs to the {role_label(other_logins[0].role)} account "
              f"'{other_logins[0].username}'. Only the {role_label('principal')} can remove it.", 'warning')
        return redirect(url_for('academics.teachers'))
    log_activity('teacher_deleted', f"Deleted teacher profile '{teacher.name}'.", 'teacher', teacher.id, teacher.department_id)
    for account in other_logins:
        account.teacher_id = None
    User.query.filter_by(teacher_id=teacher.id, role='teacher').delete()
    db.session.delete(teacher)
    db.session.commit()
    return redirect(url_for('academics.teachers'))
