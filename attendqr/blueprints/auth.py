from functools import lru_cache

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash

from ..access import (
    build_account_profile,
    current_department_id,
    log_activity,
    own_security_activity_logs_query,
    role_label,
)
from ..extensions import db
from ..models import ActivityLog, Admin
from ..security import (
    clear_login_failures,
    client_ip,
    is_login_locked,
    is_safe_redirect_target,
    password_policy_error,
    record_login_failure,
)
from ..services.qr import clear_scan_grant
from ..utils import normalize_email

bp = Blueprint('auth', __name__)

INVALID_LOGIN_MESSAGE = "Invalid email or password."
LOCKED_LOGIN_MESSAGE = "Too many failed attempts from this device. Please wait a few minutes and try again."


@lru_cache(maxsize=1)
def _dummy_password_hash():
    return generate_password_hash('attendqr-timing-equaliser')


def safe_next_url():
    target = request.values.get('next')
    return target if is_safe_redirect_target(target) else None


@bp.route('/login', methods=['GET', 'POST'])
def login():
    next_url = safe_next_url()
    if current_user.is_authenticated:
        return redirect(next_url or url_for('main.dashboard'))

    error = None
    if request.method == 'POST':
        username = normalize_email(request.form.get('username'))
        password = request.form.get('password', '')
        ip = client_ip()

        if not username or not password:
            error = "Enter your email and password."
        elif is_login_locked(username, ip):
            error = LOCKED_LOGIN_MESSAGE
        else:
            admin = Admin.query.filter(db.func.lower(Admin.username) == username).first()
            password_ok = check_password_hash(admin.password if admin else _dummy_password_hash(), password)
            if admin and password_ok:
                if not admin.is_active:
                    # Only revealed once the password is proven correct.
                    error = "This account is inactive. Please contact the Principal or HOD."
                else:
                    clear_login_failures(username)
                    db.session.commit()
                    session.pop('_flashes', None)
                    login_user(admin)
                    return redirect(next_url or url_for('main.dashboard'))
            else:
                record_login_failure(username, ip)
                db.session.commit()
                error = INVALID_LOGIN_MESSAGE

    return render_template('login.html', error=error, next_url=next_url)


@bp.route('/logout', methods=['POST'])
def logout():
    logout_user()
    clear_scan_grant()
    return redirect(url_for('auth.login'))


@bp.route('/account', methods=['GET', 'POST'])
@login_required
def account():
    next_url = safe_next_url()
    error = None

    if request.method == 'POST':
        current_password = request.form.get('current_password', '')
        new_password = request.form.get('new_password', '')
        confirm_password = request.form.get('confirm_password', '')
        identifiers = [current_user.username]
        if current_user.student:
            identifiers.append(current_user.student.roll_no)

        if not current_password or not new_password or not confirm_password:
            error = "All password fields are required"
        elif not check_password_hash(current_user.password, current_password):
            error = "Current password is incorrect"
        elif new_password != confirm_password:
            error = "New password and confirm password do not match"
        elif check_password_hash(current_user.password, new_password):
            error = "New password must be different from the current password"
        else:
            error = password_policy_error(new_password, identifiers)

        if not error:
            user = current_user._get_current_object()
            user.password = generate_password_hash(new_password)
            user.must_change_password = False
            # Sign out every other session that used the old password, then keep this one.
            user.rotate_session_token()
            clear_login_failures(user.username)
            log_activity(
                'password_changed',
                f"{role_label(user.role)} account '{user.username}' changed its password.",
                target_type='admin',
                target_id=user.id,
                department_id=current_department_id(),
            )
            db.session.commit()
            login_user(user)
            flash("Password updated successfully.", "success")
            return redirect(next_url or url_for('auth.account'))

    return render_template(
        'account.html',
        profile=build_account_profile(current_user),
        security_logs=own_security_activity_logs_query().order_by(ActivityLog.created_at.desc()).limit(8).all(),
        error=error,
        next_url=next_url,
    )
