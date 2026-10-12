"""Security primitives: secret key, CSRF, redirects, passwords, headers, login throttling."""
import os
import re
import secrets
from datetime import timedelta
from urllib.parse import urlsplit

from flask import abort, current_app, g, jsonify, request, session

from .extensions import db
from .models import LoginThrottle
from .utils import ist_now

# Values that have been published in this repository or are common placeholders.
INSECURE_SECRET_KEYS = frozenset({
    'attendance-system-secret-key',
    'change-this-secret-key',
    'changeme',
    'secret',
    'dev',
})


def configure_secret_key(app):
    """Use SECRET_KEY from the environment, or a private key persisted in the instance folder."""
    key = app.config.get('SECRET_KEY')
    if key and key not in INSECURE_SECRET_KEYS:
        return
    if key:
        if app.debug or app.testing:
            app.logger.warning("SECRET_KEY is a public placeholder; acceptable only for local development.")
            return
        raise RuntimeError(
            "SECRET_KEY is set to a public placeholder value. Remove it from your environment/.env "
            "so AttendQR can generate a private key, or set it to a long random value."
        )

    path = os.path.join(app.instance_path, 'secret_key')
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        with open(path, encoding='utf-8') as handle:
            key = handle.read().strip()
    else:
        key = secrets.token_hex(32)
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(key)
    if not key:
        raise RuntimeError(f"{path} is empty. Delete it to generate a new secret key.")
    app.config['SECRET_KEY'] = key


# ── CSRF ─────────────────────────────────────────────────────

def get_csrf_token():
    token = session.get('_csrf_token')
    if not token:
        token = secrets.token_urlsafe(32)
        session['_csrf_token'] = token
    return token


def wants_json_response():
    return (
        request.is_json
        or request.blueprint == 'api'
        or request.endpoint == 'attendance.mark_attendance'
    )


def validate_csrf_request():
    if request.method != 'POST':
        return None

    expected = session.get('_csrf_token')
    supplied = (
        request.form.get('_csrf_token')
        or request.headers.get('X-CSRFToken')
        or request.headers.get('X-CSRF-Token')
    )
    if expected and supplied and secrets.compare_digest(expected, supplied):
        return None

    message = 'Security token expired. Refresh the page and try again.'
    if wants_json_response():
        return jsonify({'success': False, 'message': message}), 400
    abort(400, description=message)


# ── Redirects ────────────────────────────────────────────────

def is_safe_redirect_target(target):
    """Allow only same-site, path-only redirect targets such as "/students?page=2"."""
    if not target or not isinstance(target, str):
        return False
    # Browsers treat "\" like "/" and strip control characters, so "/\evil.com" means "//evil.com".
    if '\\' in target or any(ord(ch) < 32 or ord(ch) == 127 for ch in target):
        return False
    if not target.startswith('/') or target.startswith('//'):
        return False
    parsed = urlsplit(target)
    return not parsed.scheme and not parsed.netloc


# ── Passwords ────────────────────────────────────────────────

def password_policy_error(password, identifiers=()):
    """Return an error message when a user-chosen password is too weak, else None."""
    min_length = current_app.config['MIN_PASSWORD_LENGTH']
    if len(password) < min_length:
        return f"Password must be at least {min_length} characters."
    if not re.search(r'[A-Za-z]', password) or not re.search(r'\d', password):
        return "Password must include at least one letter and one number."
    lowered = password.strip().lower()
    for identifier in identifiers:
        if identifier and lowered == str(identifier).strip().lower():
            return "Password must not be the same as your email or roll number."
    return None


def generate_temporary_password():
    return f"Tmp-{secrets.token_urlsafe(9)}"


# ── Response headers ─────────────────────────────────────────

def csp_nonce():
    """Per-request nonce; every inline <script> in the templates must carry it."""
    if not hasattr(g, 'csp_nonce'):
        g.csp_nonce = secrets.token_urlsafe(16)
    return g.csp_nonce


def content_security_policy():
    return "; ".join([
        "default-src 'self'",
        # Only our own files and nonce-tagged inline blocks run; injected scripts and inline handlers do not.
        f"script-src 'self' 'nonce-{csp_nonce()}'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data:",
        "font-src 'self'",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ])


def apply_security_headers(response):
    headers = response.headers
    headers.setdefault('X-Content-Type-Options', 'nosniff')
    headers.setdefault('X-Frame-Options', 'DENY')
    # Scan URLs carry tokens; never leak them to other sites through the Referer header.
    headers.setdefault('Referrer-Policy', 'same-origin')
    headers.setdefault('Permissions-Policy', 'camera=(), microphone=(), geolocation=()')
    if response.mimetype == 'text/html':
        headers.setdefault('Content-Security-Policy', content_security_policy())
    if request.is_secure:
        headers.setdefault('Strict-Transport-Security', 'max-age=31536000')
    if request.endpoint != 'static':
        # Pages contain personal data and are often viewed on shared lab machines.
        headers.setdefault('Cache-Control', 'no-store')
    return response


# ── Login throttling ─────────────────────────────────────────

def client_ip():
    return request.remote_addr or 'unknown'


def _throttle_rules(username, ip):
    cfg = current_app.config
    return [
        (
            f"ip:{ip}|{username}",
            cfg['LOGIN_IP_FAILURE_LIMIT'],
            timedelta(minutes=cfg['LOGIN_IP_WINDOW_MINUTES']),
            timedelta(minutes=cfg['LOGIN_IP_LOCK_MINUTES']),
        ),
        (
            f"acct:{username}",
            cfg['LOGIN_ACCOUNT_FAILURE_LIMIT'],
            timedelta(minutes=cfg['LOGIN_ACCOUNT_WINDOW_MINUTES']),
            timedelta(minutes=cfg['LOGIN_ACCOUNT_LOCK_MINUTES']),
        ),
    ]


def is_login_locked(username, ip):
    keys = [rule[0] for rule in _throttle_rules(username, ip)]
    return db.session.query(
        LoginThrottle.query.filter(LoginThrottle.key.in_(keys), LoginThrottle.locked_until > ist_now()).exists()
    ).scalar()


def record_login_failure(username, ip):
    now = ist_now()
    for key, limit, window, lock in _throttle_rules(username, ip):
        row = LoginThrottle.query.filter_by(key=key).first()
        if row is None:
            row = LoginThrottle(key=key, username=username, failures=0, window_started_at=now)
            db.session.add(row)
        if row.window_started_at is None or now - row.window_started_at > window:
            row.failures = 0
            row.window_started_at = now
        row.failures = (row.failures or 0) + 1
        if row.failures >= limit:
            row.locked_until = now + lock
            row.failures = 0
            row.window_started_at = now

    stale_before = now - timedelta(days=1)
    LoginThrottle.query.filter(
        LoginThrottle.window_started_at < stale_before,
        db.or_(LoginThrottle.locked_until.is_(None), LoginThrottle.locked_until < now),
    ).delete(synchronize_session=False)


def clear_login_failures(username):
    LoginThrottle.query.filter_by(username=username).delete(synchronize_session=False)


def locked_usernames(usernames):
    if not usernames:
        return set()
    rows = db.session.query(LoginThrottle.username).filter(
        LoginThrottle.username.in_(list(usernames)),
        LoginThrottle.locked_until > ist_now(),
    ).distinct()
    return {username for (username,) in rows}
