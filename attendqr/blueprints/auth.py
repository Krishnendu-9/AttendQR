import secrets
import time
from functools import lru_cache
from typing import cast

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash

from ..access import (
    SIGN_IN_ATTEMPT_ACTOR,
    build_account_profile,
    current_department_id,
    log_activity,
    own_security_activity_logs_query,
    role_label,
)
from ..extensions import db
from ..models import ActivityLog, User
from ..observability import count_login
from ..schema import discard_used_initial_password
from ..security import (
    clear_login_failures,
    client_ip,
    is_login_locked,
    is_safe_redirect_target,
    mark_session_active,
    password_policy_error,
    register_login_failure,
)
from ..services import two_factor
from ..services.qr import clear_scan_grant, qr_data_uri
from ..utils import normalize_email

bp = Blueprint('auth', __name__)

INVALID_LOGIN_MESSAGE = "Invalid email or password."
LOCKED_LOGIN_MESSAGE = "Too many failed attempts from this device. Please wait a few minutes and try again."


@lru_cache(maxsize=1)
def _dummy_password_hash():
    return generate_password_hash('attendqr-timing-equaliser')


def _log_failed_login(user, locked, reason='wrong password'):
    """Audit failed sign-ins to real accounts (unknown usernames are not logged, so they cannot flood it).

    The throttle bounds how many can be recorded: once an account is paused,
    further attempts are refused before the password is checked.
    """
    log_activity(
        'login_failed', f"Failed sign-in attempt for '{user.username}' ({reason}).",
        'user', user.id, user.department_id, anonymous_actor=SIGN_IN_ATTEMPT_ACTOR,
    )
    if locked:
        log_activity(
            'login_locked', f"Sign-in for '{user.username}' paused after repeated failed attempts.",
            'user', user.id, user.department_id, anonymous_actor=SIGN_IN_ATTEMPT_ACTOR,
        )
    db.session.commit()


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
            count_login('locked')
            error = LOCKED_LOGIN_MESSAGE
        else:
            user = User.query.filter(db.func.lower(User.username) == username).first()
            password_ok = check_password_hash(user.password if user else _dummy_password_hash(), password)
            if user and password_ok:
                if not user.is_active:
                    # Only revealed once the password is proven correct.
                    count_login('inactive')
                    error = "This account is inactive. Please contact the Principal or HOD."
                elif two_factor.is_enabled(user):
                    # The password is right; the account is only signed in once the code is too.
                    session['two_step'] = {'id': user.id, 'token': user.session_token,
                                           'expires': int(time.time()) + TWO_STEP_SECONDS, 'next': next_url}
                    return redirect(url_for('auth.two_step'))
                else:
                    return _complete_login(user, next_url)
            else:
                locked = register_login_failure(username, ip)
                count_login('locked' if locked else 'failed')
                if user:
                    _log_failed_login(user, locked)
                error = INVALID_LOGIN_MESSAGE

    return render_template('login.html', error=error, next_url=next_url)


def _complete_login(user, next_url, how=None):
    clear_login_failures(normalize_email(user.username))
    session.pop('_flashes', None)
    login_user(user)
    mark_session_active()
    if user.role != 'student':
        # Staff sign-ins are audited; students sign in daily, which would flood the log.
        log_activity(
            'login_succeeded', f"{role_label(user.role)} account '{user.username}' signed in{how or ''}.",
            'user', user.id, user.department_id,
        )
    db.session.commit()
    count_login('success')
    return redirect(next_url or url_for('main.dashboard'))


# ── Two-step sign-in ─────────────────────────────────────────

# Time between a correct password and the code before the password must be entered again.
TWO_STEP_SECONDS = 300


def _two_step_user():
    """The account waiting for its code after a correct password, and where to go next."""
    pending = session.get('two_step')
    if not isinstance(pending, dict) or not isinstance(pending.get('id'), int) \
            or not isinstance(pending.get('expires'), int) or pending['expires'] < time.time():
        return None, None
    user = db.session.get(User, pending['id'])
    if user is None or not user.is_active or not two_factor.is_enabled(user) \
            or not secrets.compare_digest(user.session_token, str(pending.get('token', ''))):
        return None, None
    next_url = pending.get('next')
    return user, next_url if is_safe_redirect_target(next_url) else None


@bp.route('/login/two-step', methods=['GET', 'POST'])
def two_step():
    user, next_url = _two_step_user()
    if user is None:
        session.pop('two_step', None)
        flash("Sign in again to continue.", 'warning')
        return redirect(url_for('auth.login'))

    error = None
    if request.method == 'POST':
        code = request.form.get('code', '')
        username, ip = normalize_email(user.username), client_ip()
        if is_login_locked(username, ip):
            count_login('locked')
            error = LOCKED_LOGIN_MESSAGE
        elif two_factor.verify_code(user, code):
            session.pop('two_step', None)
            return _complete_login(user, next_url, ' with two-step sign-in')
        elif two_factor.use_recovery_code(user, code):
            session.pop('two_step', None)
            left = two_factor.recovery_codes_left(user)
            log_activity('recovery_code_used', f"'{user.username}' signed in with a recovery code ({left} left).",
                         'user', user.id, user.department_id)
            response = _complete_login(user, next_url, ' with a recovery code')
            flash(f"You signed in with a recovery code; {left} left. If your phone is lost, set up two-step "
                  "sign-in again from your account page.", 'warning')
            return response
        else:
            locked = register_login_failure(username, ip)
            count_login('locked' if locked else 'failed_code')
            _log_failed_login(user, locked, 'wrong two-step code')
            error = "That code is not right. Use the newest code in your authenticator app, or a recovery code."
    return render_template('two_step.html', error=error, username=user.username)


@bp.route('/account/two-step', methods=['GET', 'POST'])
@login_required
def two_step_setup():
    """Turn on two-step sign-in, make new recovery codes, or turn it off."""
    user = cast(User, current_user._get_current_object())  # @login_required: never anonymous
    if not two_factor.can_use(user):
        abort(404)
    error, codes, action = None, None, None
    if request.method == 'POST':
        action = request.form.get('action')
        code = request.form.get('code', '')
        throttle_name, ip = normalize_email(user.username), client_ip()
        if is_login_locked(throttle_name, ip):
            error = LOCKED_LOGIN_MESSAGE
        elif action == 'enable' and not two_factor.is_enabled(user):
            codes = two_factor.enable(user, code)
            if codes is None:
                error = ("That code did not match. Check that your phone sets its time automatically, "
                         "and type the newest code.")
            else:
                log_activity('two_factor_enabled', f"'{user.username}' turned on two-step sign-in.",
                             'user', user.id, current_department_id())
        elif action == 'recovery' and two_factor.is_enabled(user):
            if two_factor.verify_code(user, code):
                codes = two_factor.new_recovery_codes(user)
                log_activity('recovery_codes_replaced', f"'{user.username}' made new recovery codes.",
                             'user', user.id, current_department_id())
            else:
                error = "That code is not right."
                register_login_failure(throttle_name, ip)
        elif action == 'disable' and two_factor.is_enabled(user):
            if two_factor.is_required(user):
                error = f"{role_label(user.role)} accounts must keep two-step sign-in on."
            elif not check_password_hash(user.password, request.form.get('password', '')) \
                    or not two_factor.verify_code(user, code):
                error = "Your password or code is not right."
                register_login_failure(throttle_name, ip)
            else:
                two_factor.disable(user)
                log_activity('two_factor_disabled', f"'{user.username}' turned off two-step sign-in.",
                             'user', user.id, current_department_id())
                db.session.commit()
                flash("Two-step sign-in is off.", 'success')
                return redirect(url_for('auth.account'))
        else:
            abort(400)

    secret = None if two_factor.is_enabled(user) else two_factor.pending_secret(user)
    db.session.commit()  # keeps the pending secret, a used code, and new recovery codes
    if codes:
        flash("Two-step sign-in is on." if action == 'enable' else "New recovery codes made.", 'success')
    return render_template(
        'two_step_setup.html',
        enabled=two_factor.is_enabled(user),
        required=two_factor.is_required(user),
        qr_image=qr_data_uri(two_factor.provisioning_uri(user, secret)) if secret else None,
        key=two_factor.grouped(secret) if secret else None,
        codes=codes,
        codes_left=two_factor.recovery_codes_left(user),
        error=error,
    )


@bp.route('/logout', methods=['POST'])
def logout():
    if current_user.is_authenticated:
        # The login lives in a cookie, so clearing it here would leave any copy of it
        # usable. A new session token makes every copy, on any device, invalid.
        current_user.rotate_session_token()
        db.session.commit()
    logout_user()
    clear_scan_grant()
    return redirect(url_for('auth.login'))


def _discard_initial_password():
    """The generated first password is no longer needed once the Principal has replaced it."""
    try:
        discard_used_initial_password()
    except OSError:
        current_app.logger.exception("Could not delete the initial Principal password file")


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
        # Wrong current passwords count like failed logins, so an unattended or stolen
        # session cannot be used to guess the password without limit.
        throttle_name, ip = normalize_email(current_user.username), client_ip()

        if not current_password or not new_password or not confirm_password:
            error = "All password fields are required"
        elif is_login_locked(throttle_name, ip):
            error = LOCKED_LOGIN_MESSAGE
        elif not check_password_hash(current_user.password, current_password):
            error = "Current password is incorrect"
            if register_login_failure(throttle_name, ip):
                log_activity(
                    'login_locked',
                    f"Password change for '{current_user.username}' paused after repeated wrong current passwords.",
                    'user', current_user.id, current_department_id(),
                )
                db.session.commit()
        elif new_password != confirm_password:
            error = "New password and confirm password do not match"
        elif check_password_hash(current_user.password, new_password):
            error = "New password must be different from the current password"
        else:
            error = password_policy_error(new_password, identifiers)

        if not error:
            user = cast(User, current_user._get_current_object())  # @login_required: never anonymous
            user.password = generate_password_hash(new_password)
            user.must_change_password = False
            # Sign out every other session that used the old password, then keep this one.
            user.rotate_session_token()
            clear_login_failures(user.username)
            log_activity(
                'password_changed',
                f"{role_label(user.role)} account '{user.username}' changed its password.",
                target_type='user',
                target_id=user.id,
                department_id=current_department_id(),
            )
            db.session.commit()
            if user.role == 'principal':
                _discard_initial_password()
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
