"""AttendQR: QR-based attendance management."""
import mimetypes
import os
import secrets

import click
from flask import Flask, flash, jsonify, redirect, request, url_for
from flask_login import current_user, logout_user
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
    validate_csrf_request,
    wants_json_response,
)
from .services.network import describe_scan_networks, parse_scan_networks
from .services.retention import purge_expired_scan_ips, register_scan_ip_purge
from .utils import format_12h, ist_now

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Endpoints a user who must change their password may still reach.
PASSWORD_CHANGE_ALLOWED = {'auth.account', 'auth.logout', 'static'}

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
    register_scan_ip_purge(app)
    _register_template_helpers(app)
    _register_cli(app)

    if app.config['AUTO_MIGRATE']:
        from .schema import prepare_database
        with app.app_context():
            prepare_database()

    return app


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


def build_js_config():
    p = URL_ID_PLACEHOLDER
    return {
        'idPlaceholder': str(p),
        'minPasswordLength': Config.MIN_PASSWORD_LENGTH,
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
