from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import login_required
from werkzeug.security import generate_password_hash

from ..access import (
    ROLE_LABELS,
    SPECIAL_CLASS_SUBJECT_NAME,
    can_access_department,
    can_access_teacher,
    can_edit_account,
    can_manage_account,
    can_reset_staff_account,
    current_department_id,
    describe_delete_block,
    has_active_hod,
    is_allowed_staff_email,
    is_super_admin,
    log_activity,
    ordered_departments_query,
    role_label,
    role_required,
    staff_email_rule_message,
    subject_display_name,
    user_role,
    visible_activity_logs_query,
)
from ..extensions import db
from ..models import ActivityLog, Admin, ClassSession, Subject, Teacher
from ..security import clear_login_failures, generate_temporary_password, locked_usernames, password_policy_error
from ..utils import normalize_email, normalize_text, parse_int_value

bp = Blueprint('accounts', __name__)


class AccountError(ValueError):
    """A validation problem to show to the user."""


def allowed_roles():
    keys = ('super_admin', 'hod', 'teacher') if is_super_admin() else ('teacher',)
    return {key: ROLE_LABELS[key] for key in keys}


def _assign_subject(subject_id, department_id, teacher):
    if not subject_id:
        return None
    subject = db.session.get(Subject, subject_id)
    if not subject or subject.name == SPECIAL_CLASS_SUBJECT_NAME or subject.department_id != department_id:
        raise AccountError("Please select a valid subject from the same department")
    subject.teacher_id = teacher.id
    return subject


def _teacher_move_block(teacher, department_id):
    if department_id == teacher.department_id:
        return
    assigned_subjects = Subject.query.filter_by(teacher_id=teacher.id).count()
    sessions_count = ClassSession.query.filter_by(teacher_id=teacher.id).count()
    if assigned_subjects or sessions_count:
        raise AccountError(describe_delete_block(
            f"moving teacher '{teacher.name}'",
            {'assigned subjects': assigned_subjects, 'sessions': sessions_count},
        ))


def _create_account(roles):
    username = normalize_email(request.form.get('username', ''))
    password = request.form.get('password', '')
    confirm_password = request.form.get('confirm_password', '')
    role = request.form.get('role', 'teacher' if user_role() == 'hod' else 'super_admin')
    raw_department_id = request.form.get('department_id')
    subject_id = parse_int_value(request.form.get('subject_id'))
    teacher_name = normalize_text(request.form.get('teacher_name'))

    if not username or not password:
        raise AccountError("Account email and password are required")
    if role not in roles:
        raise AccountError("Invalid role selected")
    if not is_allowed_staff_email(username):
        raise AccountError(staff_email_rule_message())
    policy_error = password_policy_error(password, [username])
    if policy_error:
        raise AccountError(policy_error)
    if password != confirm_password:
        raise AccountError("Passwords do not match")
    if Admin.query.filter(db.func.lower(Admin.username) == username).first():
        raise AccountError(f"Account email '{username}' already exists")

    department_id = teacher_id = None
    assigned_subject = None
    if role == 'hod':
        department_id = parse_int_value(raw_department_id)
        if not department_id or not can_access_department(department_id):
            raise AccountError("Valid department is required for HOD accounts")
        if has_active_hod(department_id):
            raise AccountError("This department already has an active HOD. Deactivate the current HOD first.")
    elif role == 'teacher':
        department_id = parse_int_value(raw_department_id) if is_super_admin() else current_department_id()
        if not teacher_name:
            raise AccountError("Teacher name is required for teacher accounts")
        if len(teacher_name) > 100:
            raise AccountError("Teacher name must be at most 100 characters")
        if not department_id or not can_access_department(department_id):
            raise AccountError("Valid department is required for teacher accounts")

        teacher = Teacher.query.filter(db.func.lower(Teacher.email) == username).first()
        if teacher:
            if not can_access_teacher(teacher):
                raise AccountError("You can create teacher accounts only for your department")
            _teacher_move_block(teacher, department_id)
            teacher.name = teacher_name
            teacher.department_id = department_id
        else:
            teacher = Teacher(name=teacher_name, email=username, department_id=department_id)
            db.session.add(teacher)
            db.session.flush()
        teacher_id = teacher.id
        assigned_subject = _assign_subject(subject_id, department_id, teacher)

    account = Admin(
        username=username,
        password=generate_password_hash(password),
        role=role,
        department_id=department_id,
        teacher_id=teacher_id,
        is_active=True,
        must_change_password=True,
    )
    db.session.add(account)
    db.session.flush()
    log_activity('account_created', f"Created {role_label(role)} account '{username}'.", 'admin', account.id, department_id)
    db.session.commit()

    message = f"{role_label(role)} account '{username}' added successfully."
    if assigned_subject:
        message += f" Subject assigned: {subject_display_name(assigned_subject)}."
    return message + " The user must change the password at first login."


@bp.route('/add_admin', methods=['GET', 'POST'])
@login_required
@role_required('super_admin', 'hod')
def index():
    roles = allowed_roles()
    if request.method == 'POST':
        try:
            flash(_create_account(roles), 'success')
        except AccountError as exc:
            db.session.rollback()
            flash(str(exc), 'danger')
        return redirect(url_for('accounts.index'))

    accounts = Admin.query.filter(Admin.role != 'student')
    if not is_super_admin():
        accounts = accounts.filter(Admin.department_id == current_department_id())
    accounts = accounts.order_by(Admin.role, Admin.is_active.desc(), Admin.username).all()

    return render_template(
        'add_admin.html',
        admins=accounts,
        locked=locked_usernames([a.username for a in accounts]),
        departments=ordered_departments_query().all(),
        roles=roles,
        recent_logs=visible_activity_logs_query().order_by(ActivityLog.created_at.desc()).limit(12).all(),
    )


@bp.route('/edit_admin/<int:id>', methods=['POST'])
@login_required
@role_required('super_admin', 'hod')
def edit(id):
    account = db.get_or_404(Admin, id)
    if not can_edit_account(account):
        abort(403)

    username = normalize_email(request.form.get('username', ''))
    teacher_name = normalize_text(request.form.get('teacher_name'))
    raw_department_id = request.form.get('department_id')
    subject_id = parse_int_value(request.form.get('subject_id'))

    try:
        if not username:
            raise AccountError("Account email is required.")
        if not is_allowed_staff_email(username):
            raise AccountError(staff_email_rule_message())
        if Admin.query.filter(Admin.id != account.id, db.func.lower(Admin.username) == username).first():
            raise AccountError(f"Account email '{username}' already exists.")

        if account.role == 'super_admin':
            account.username = username
        elif account.role == 'hod':
            if not is_super_admin():
                abort(403)
            department_id = parse_int_value(raw_department_id)
            if not department_id:
                raise AccountError("Valid department is required for HOD accounts")
            if not can_access_department(department_id):
                abort(403)
            if account.is_active and has_active_hod(department_id, exclude_admin_id=account.id):
                raise AccountError("This department already has an active HOD. Deactivate the current HOD first.")
            account.username = username
            account.department_id = department_id
        elif account.role == 'teacher':
            department_id = parse_int_value(raw_department_id) if is_super_admin() else current_department_id()
            if not teacher_name:
                raise AccountError("Teacher name is required for Teacher accounts")
            if not department_id:
                raise AccountError("Valid department is required for Teacher accounts")
            if not can_access_department(department_id):
                abort(403)
            if Teacher.query.filter(
                db.func.lower(Teacher.email) == username,
                Teacher.id != (account.teacher_id or 0),
            ).first():
                raise AccountError(f"Teacher email '{username}' already belongs to another teacher profile.")

            teacher = account.teacher
            if not teacher:
                teacher = Teacher(name=teacher_name, email=username, department_id=department_id)
                db.session.add(teacher)
                db.session.flush()
                account.teacher_id = teacher.id
            _teacher_move_block(teacher, department_id)

            teacher.name = teacher_name
            teacher.email = username
            teacher.department_id = department_id
            account.username = username
            account.department_id = department_id
            _assign_subject(subject_id, department_id, teacher)

        log_activity('account_updated', f"Updated {role_label(account.role)} account '{account.username}'.",
                     'admin', account.id, account.department_id)
        db.session.commit()
        flash(f"{role_label(account.role)} account '{account.username}' updated successfully.", "success")
    except AccountError as exc:
        db.session.rollback()
        flash(f"Unable to update account: {exc}", "danger")

    return redirect(url_for('accounts.index'))


@bp.route('/delete_admin/<int:id>', methods=['POST'])
@login_required
@role_required('super_admin', 'hod')
def delete(id):
    account = db.get_or_404(Admin, id)
    if not can_manage_account(account):
        abort(403)

    if account.teacher_id:
        assigned_subjects = Subject.query.filter_by(teacher_id=account.teacher_id).count()
        sessions_count = ClassSession.query.filter_by(teacher_id=account.teacher_id).count()
        if assigned_subjects or sessions_count:
            flash(describe_delete_block(
                f"account '{account.username}'",
                {'assigned subjects': assigned_subjects, 'sessions': sessions_count},
            ), 'warning')
            return redirect(url_for('accounts.index'))

    log_activity('account_deleted', f"Deleted {role_label(account.role)} account '{account.username}'.",
                 'admin', account.id, account.department_id)
    clear_login_failures(account.username)
    db.session.delete(account)
    db.session.commit()
    return redirect(url_for('accounts.index'))


@bp.route('/toggle_admin_status/<int:id>', methods=['POST'])
@login_required
@role_required('super_admin', 'hod')
def toggle_status(id):
    account = db.get_or_404(Admin, id)
    if not can_manage_account(account):
        abort(403)

    activate = not account.is_active
    if activate and account.role == 'hod' and account.department_id and has_active_hod(account.department_id, exclude_admin_id=account.id):
        flash("This department already has an active HOD. Deactivate that HOD first.", 'warning')
        return redirect(url_for('accounts.index'))

    account.is_active = activate
    if not activate:
        account.rotate_session_token()
    log_activity(
        'account_status_changed',
        f"{'Activated' if activate else 'Deactivated'} {role_label(account.role)} account '{account.username}'.",
        'admin', account.id, account.department_id,
    )
    db.session.commit()
    flash(f"{role_label(account.role)} account '{account.username}' is now {'active' if activate else 'inactive'}.", 'success')
    return redirect(url_for('accounts.index'))


@bp.route('/reset_admin_password/<int:id>', methods=['POST'])
@login_required
@role_required('super_admin', 'hod')
def reset_password(id):
    account = db.get_or_404(Admin, id)
    if not can_reset_staff_account(account):
        abort(403)

    temporary_password = generate_temporary_password()
    account.password = generate_password_hash(temporary_password)
    account.must_change_password = True
    account.rotate_session_token()
    clear_login_failures(account.username)
    log_activity('account_password_reset', f"Reset password for {role_label(account.role)} account '{account.username}'.",
                 'admin', account.id, account.department_id)
    db.session.commit()
    current_app.logger.info("Password reset for account %s", account.id)
    flash(f"Temporary password for {account.username}: {temporary_password}", 'success')
    return redirect(url_for('accounts.index'))
