"""AttendQR: QR-based attendance management."""
import mimetypes
import os
import re
import secrets

import click
from flask import Flask, current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, logout_user
from werkzeug.exceptions import BadRequest
from werkzeug.middleware.proxy_fix import ProxyFix

from .access import (
    can_edit_account,
    can_manage_account,
    can_reset_staff_account,
    department_scope_label,
    role_label,
    session_display_name,
    staff_email_html_pattern,
    subject_display_name,
    user_role,
)
from .config import Config
from .extensions import db, login_manager, migrate
from .models import Admin
from .security import (
    apply_security_headers,
    configure_secret_key,
    csp_nonce,
    get_csrf_token,
    session_idle_expired,
    validate_csrf_request,
    wants_json_response,
)
from .services.network import describe_scan_networks, parse_scan_networks
from .services.retention import purge_expired_scan_ips, register_scan_ip_purge
from .utils import format_12h, ist_now

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Endpoints a user who must change their password may still reach. The scan link
# only stores a scan grant and redirects to /attend (which is blocked), so a QR
# opened before the password change is not lost: its 30-second token would
# otherwise expire while the student picks a new password.
PASSWORD_CHANGE_ALLOWED = {'auth.account', 'auth.logout', 'static', 'attendance.scan'}

# An entry of ALLOWED_STAFF_DOMAINS, after config.py adds the leading "@".
STAFF_DOMAIN_PATTERN = re.compile(r'^@(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}$')
PUBLIC_EMAIL_DOMAINS = frozenset({
    '@gmail.com', '@googlemail.com', '@outlook.com', '@hotmail.com', '@live.com',
    '@yahoo.com', '@yahoo.co.in', '@icloud.com', '@rediffmail.com', '@proton.me', '@protonmail.com',
})

# Stand-in id used to render API URL templates for JavaScript.
URL_ID_PLACEHOLDER = 987654321

# Windows' registry often lacks these, so the vendored fonts would be served as octet-stream.
mimetypes.add_type('font/woff2', '.woff2')
mimetypes.add_type('font/woff', '.woff')


def create_app(overrides=None):
    app = Flask(
        __name__,
        template_folder=os.path.join(PROJECT_ROOT, 'templates'),
        static_folder=os.path.join(PROJECT_ROOT, 'static'),
        instance_path=os.path.join(PROJECT_ROOT, 'instance'),
    )
    app.config.from_object(Config)
    if overrides:
        app.config.update(overrides)
    os.makedirs(app.instance_path, exist_ok=True)

    configure_secret_key(app)
    _check_settings(app)
    _check_scan_networks(app)
    proxies = app.config['TRUSTED_PROXY_COUNT']
    if proxies:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=proxies, x_proto=proxies, x_host=proxies)

    db.init_app(app)
    migrate.init_app(app, db, directory=os.path.join(PROJECT_ROOT, 'migrations'), render_as_batch=True)
    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'
    login_manager.login_message = None

    from .blueprints import ALL_BLUEPRINTS
    for blueprint in ALL_BLUEPRINTS:
        app.register_blueprint(blueprint)

    _register_request_hooks(app)
    _register_error_pages(app)
    register_scan_ip_purge(app)
    _register_template_helpers(app)
    _register_cli(app)

    if app.config['AUTO_MIGRATE']:
        from .schema import prepare_database
        with app.app_context():
            prepare_database()

    return app


def _check_settings(app):
    """Refuse to start with timing settings that would break scanning."""
    cfg = app.config
    max_age, refresh, grant = cfg['QR_TOKEN_MAX_AGE'], cfg['QR_REFRESH_SECONDS'], cfg['SCAN_GRANT_SECONDS']
    problems = []
    if max_age < 5:
        problems.append(f"QR_TOKEN_MAX_AGE must be at least 5 seconds (got {max_age}).")
    if refresh < 1:
        problems.append(f"QR_REFRESH_SECONDS must be at least 1 second (got {refresh}).")
    elif refresh >= max_age:
        # Otherwise the code on screen stops working before it is replaced.
        problems.append(
            f"QR_REFRESH_SECONDS ({refresh}) must be less than QR_TOKEN_MAX_AGE ({max_age}), "
            "so the code on screen is replaced before it expires."
        )
    if grant < 30:
        problems.append(f"SCAN_GRANT_SECONDS must be at least 30 seconds, time to log in after scanning (got {grant}).")
    idle = cfg['SESSION_IDLE_MINUTES']
    if idle and idle < 5:
        problems.append(f"SESSION_IDLE_MINUTES must be 0 (never sign out) or at least 5 (got {idle}).")
    if cfg['TRUSTED_PROXY_COUNT'] < 0:
        problems.append(f"TRUSTED_PROXY_COUNT cannot be negative (got {cfg['TRUSTED_PROXY_COUNT']}).")
    for domain in cfg['ALLOWED_STAFF_DOMAINS']:
        if not STAFF_DOMAIN_PATTERN.fullmatch(domain):
            problems.append(f"ALLOWED_STAFF_DOMAINS has an invalid entry {domain!r}; use the form @college.edu.")
    if problems:
        raise ValueError(' '.join(problems))

    domains = cfg['ALLOWED_STAFF_DOMAINS']
    if not domains:
        app.logger.warning(
            "ALLOWED_STAFF_DOMAINS is not set, so no HOD or Teacher accounts can be created. "
            "Set it to the institution's email domain, e.g. ALLOWED_STAFF_DOMAINS=@your-college.edu"
        )
    public = sorted(set(domains) & PUBLIC_EMAIL_DOMAINS)
    if public:
        app.logger.warning(
            "ALLOWED_STAFF_DOMAINS includes public email domains (%s): anyone can get such an address. "
            "Remove them outside testing.", ', '.join(public),
        )


def _check_scan_networks(app):
    """Refuse to start with a malformed SCAN_ALLOWED_NETWORKS, and log what it allows."""
    networks = parse_scan_networks(app.config['SCAN_ALLOWED_NETWORKS'])
    if networks is None:
        app.logger.warning(
            "QR scans are accepted from any address (SCAN_ALLOWED_NETWORKS=any). "
            "A forwarded QR works from anywhere until it expires."
        )
    else:
        app.logger.info("QR scans are accepted from: %s", describe_scan_networks(networks))


@login_manager.user_loader
def load_user(user_id):
    admin_id, _, token = (user_id or '').partition(':')
    if not admin_id.isdigit() or not token:
        return None
    admin = db.session.get(Admin, int(admin_id))
    if admin is None or not admin.session_token or not secrets.compare_digest(admin.session_token, token):
        return None
    return admin


def _register_request_hooks(app):
    @app.before_request
    def guard_request():
        csrf_error = validate_csrf_request()
        if csrf_error:
            return csrf_error

        if not current_user.is_authenticated:
            return None

        # Static files are not activity, and refreshing the session on them would only add cookies.
        if request.endpoint != 'static' and session_idle_expired():
            logout_user()
            message = 'You were signed out after a period of inactivity. Please log in again.'
            if wants_json_response():
                return jsonify({'success': False, 'message': message}), 401
            flash(message, "warning")
            next_url = request.full_path.rstrip('?') if request.method == 'GET' else None
            return redirect(url_for('auth.login', next=next_url))

        if not current_user.is_active:
            logout_user()
            flash("That account is inactive. Please contact the Principal or HOD.", "warning")
            return redirect(url_for('auth.login'))

        if current_user.must_change_password and request.endpoint not in PASSWORD_CHANGE_ALLOWED:
            if wants_json_response():
                return jsonify({'success': False, 'message': 'Change your password before continuing.'}), 403
            next_url = request.full_path.rstrip('?') if request.method == 'GET' else None
            flash("Please set a new private password before continuing.", "warning")
            return redirect(url_for('auth.account', next=next_url))
        return None

    app.after_request(apply_security_headers)


# (title, message, icon) shown for each error status.
ERROR_PAGES = {
    400: ('Request Not Accepted', 'The request could not be processed. Go back, refresh the page, and try again.',
          'bi-exclamation-circle'),
    403: ('Access Denied', 'Your account does not have access to this page or action.', 'bi-shield-lock'),
    404: ('Page Not Found', 'This page or record does not exist. It may have been deleted.', 'bi-question-circle'),
    405: ('Not Allowed', 'This page cannot be used that way. Go back and use the buttons on the page.',
          'bi-slash-circle'),
    413: ('File Too Large', 'The upload is larger than the {limit} MB limit. Split the file and try again.',
          'bi-file-earmark-x'),
    500: ('Something Went Wrong', 'An unexpected error occurred and has been logged. Please try again.',
          'bi-exclamation-triangle'),
}


def _register_error_pages(app):
    def render_error(code, description=None):
        title, message, icon = ERROR_PAGES[code]
        message = message.format(limit=(app.config.get('MAX_CONTENT_LENGTH') or 0) // (1024 * 1024))
        if description:
            message = description
        if wants_json_response() or request.accept_mimetypes.best == 'application/json':
            return jsonify({'success': False, 'message': message}), code
        return render_template('error.html', code=code, title=title, message=message, icon=icon), code

    def http_error(error):
        # Only our own abort(400, description=...) texts (such as the CSRF message) are shown;
        # Werkzeug's default descriptions are technical.
        custom = error.description if error.code == 400 and error.description != BadRequest.description else None
        return render_error(error.code, custom)

    for code in (400, 403, 404, 405, 413):
        app.register_error_handler(code, http_error)

    @app.errorhandler(500)
    def server_error(error):
        # The request's transaction may be half done; never let the next query reuse it.
        db.session.rollback()
        return render_error(500)


def build_js_config():
    p = URL_ID_PLACEHOLDER
    return {
        'idPlaceholder': str(p),
        'minPasswordLength': current_app.config['MIN_PASSWORD_LENGTH'],
        'urls': {
            'semesters': url_for('api.semesters', department_id=p),
            'subjects': url_for('api.subjects', semester_id=p),
            'departmentSubjects': url_for('api.department_subjects', department_id=p),
            'teachers': url_for('api.teachers', department_id=p),
            'sessionQr': url_for('api.session_qr', id=p),
            'sessionAttendance': url_for('api.session_attendance', session_id=p),
        },
    }


def _register_template_helpers(app):
    @app.context_processor
    def inject_helpers():
        return {
            'now': ist_now,
            'role_label': role_label,
            'user_role': user_role,
            'department_scope_label': department_scope_label,
            'subject_display_name': subject_display_name,
            'session_display_name': session_display_name,
            'format_12h': format_12h,
            'csrf_token': get_csrf_token,
            'csp_nonce': csp_nonce,
            'can_edit_account': can_edit_account,
            'can_manage_account': can_manage_account,
            'can_toggle_account_status': can_manage_account,
            'can_reset_staff_account': can_reset_staff_account,
            'attendance_target': app.config['ATTENDANCE_TARGET'],
            'min_password_length': app.config['MIN_PASSWORD_LENGTH'],
            'staff_email_pattern': staff_email_html_pattern,
            'staff_domains': app.config['ALLOWED_STAFF_DOMAINS'],
            'js_config': build_js_config,
        }


def _register_cli(app):
    @app.cli.command('init-db')
    def init_db_command():
        """Create or upgrade the database (also bridges databases created before migrations)."""
        from .schema import prepare_database
        prepare_database()
        click.echo("Database is up to date.")

    @app.cli.command('purge-scan-ips')
    def purge_scan_ips_command():
        """Clear scan IP addresses older than SCAN_IP_RETENTION_DAYS (the app also does this hourly)."""
        cleared = purge_expired_scan_ips()
        click.echo(f"Cleared {cleared} scan IP address(es).")
