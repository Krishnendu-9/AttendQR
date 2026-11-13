"""Security primitives: secret key, CSRF, redirects, passwords, headers, login throttling."""
import os
import re
import secrets
import time
from datetime import timedelta
from urllib.parse import urlsplit

from flask import abort, current_app, g, jsonify, request, session
from sqlalchemy.exc import IntegrityError
from werkzeug.security import generate_password_hash

from .extensions import db
from .models import LoginThrottle
from .services.network import client_ip as network_client_ip
from .utils import utc_now

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
        key = _read_secret_key(path)
    else:
        key = secrets.token_hex(32)
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(key)
    if not key:
        raise RuntimeError(f"{path} is empty. Delete it to generate a new secret key.")
    app.config['SECRET_KEY'] = key


def _read_secret_key(path, wait_seconds=2.0):
    """Read the key file, waiting briefly if it is still empty.

    When several worker processes start at once, one creates the file and the
    others may open it in the moment before the key is written.
    """
    deadline = time.monotonic() + wait_seconds
    while True:
        with open(path, encoding='utf-8') as handle:
            key = handle.read().strip()
        if key or time.monotonic() >= deadline:
            return key
        time.sleep(0.05)


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

# Passwords that pass the length/letter/digit rule but are among the first ones guessed.
COMMON_PASSWORDS = frozenset({
    'password1', 'password12', 'password123', 'password1234', 'passw0rd', 'p@ssw0rd', 'p@ssword1',
    'qwerty123', 'qwerty12', 'qwerty1234', 'qwertyuiop1', 'qwer1234', 'asdf1234', 'zxcv1234',
    'abc12345', 'abc123456', 'abcd1234', 'abcdef12', 'abcdef123', '1234abcd', 'aa123456',
    '1qaz2wsx', '1q2w3e4r', '1q2w3e4r5t', 'zaq12wsx', 'q1w2e3r4', 'a1b2c3d4',
    '12345678a', 'a12345678', '123456789a', '1234567a', 'test1234', 'pass1234', 'user1234',
    'hello123', 'trustno1', 'iloveyou1', 'qr123456',
})
# Common words that people extend with digits or symbols ("Welcome@2026", "India123").
COMMON_PASSWORD_WORDS = frozenset({
    'password', 'passw0rd', 'qwerty', 'qwertyuiop', 'welcome', 'admin', 'administrator', 'letmein',
    'iloveyou', 'changeme', 'monkey', 'dragon', 'master', 'sunshine', 'princess', 'football',
    'cricket', 'baseball', 'superman', 'batman', 'india', 'hello', 'test', 'user', 'login', 'secret',
    'student', 'teacher', 'principal', 'college', 'school', 'attendance', 'attendqr',
})
# Identifier parts shorter than this are too short to count as "containing" them.
MIN_IDENTIFIER_PART = 4


def _identifier_parts(identifier):
    """The full identifier and, for an email, the part before the @."""
    value = str(identifier or '').strip().lower()
    parts = {value}
    if '@' in value:
        parts.add(value.split('@', 1)[0])
    return {part for part in parts if len(part) >= MIN_IDENTIFIER_PART}


def password_policy_error(password, identifiers=()):
    """Return an error message when a user-chosen password is too weak, else None."""
    min_length = current_app.config['MIN_PASSWORD_LENGTH']
    if len(password) < min_length:
        return f"Password must be at least {min_length} characters."
    if not re.search(r'[A-Za-z]', password) or not re.search(r'\d', password):
        return "Password must include at least one letter and one number."
    lowered = password.strip().lower()
    for identifier in identifiers:
        if any(part in lowered for part in _identifier_parts(identifier)):
            return "Password must not contain your email name or roll number."
    alphanumeric = re.sub(r'[^a-z0-9]', '', lowered)
    core = re.sub(r'^[\W\d_]+|[\W\d_]+$', '', lowered)
    if lowered in COMMON_PASSWORDS or alphanumeric in COMMON_PASSWORDS or core in COMMON_PASSWORD_WORDS:
        return "That password is too common and easy to guess. Choose something less predictable, such as a short phrase."
    return None


# No 0/O or 1/I/L, so a password read off a printout or a screen is typed correctly.
TEMPORARY_PASSWORD_ALPHABET = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789'  # noqa: S105 - characters to pick from, not a password


def generate_temporary_password():
    """A one-time password such as "K7RM-2XQD-9HFA" (12 random characters, about 59 bits)."""
    chars = ''.join(secrets.choice(TEMPORARY_PASSWORD_ALPHABET) for _ in range(12))
    return '-'.join(chars[i:i + 4] for i in range(0, 12, 4))


# Still memory-hard, but about ten times faster than Werkzeug's default scrypt cost.
# That default exists to slow down guessing of passwords people choose; a random
# 59-bit password that must be replaced at first login does not need it, and a
# bulk import of a few hundred students would otherwise take most of a minute.
TEMPORARY_PASSWORD_HASH_METHOD = 'scrypt:4096:8:1'  # noqa: S105 - a hashing method name, not a password


def hash_temporary_password(password):
    """Hash a password from generate_temporary_password(). Passwords people choose use the default cost."""
    return generate_password_hash(password, method=TEMPORARY_PASSWORD_HASH_METHOD)


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
        # No inline styles either: an injected style attribute or <style> block is ignored.
        "style-src 'self'",
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


# ── Idle sign-out ────────────────────────────────────────────

LAST_SEEN_KEY = '_last_seen'


def mark_session_active():
    session[LAST_SEEN_KEY] = int(time.time())


def session_idle_expired():
    """True when the signed-in session has been idle longer than SESSION_IDLE_MINUTES (0 disables the check).

    Updates the last-activity time otherwise. The session cookie is signed, so
    the time cannot be changed by the browser.
    """
    minutes = current_app.config['SESSION_IDLE_MINUTES']
    last_seen = session.get(LAST_SEEN_KEY)
    if minutes and isinstance(last_seen, int) and time.time() - last_seen > minutes * 60:
        return True
    mark_session_active()
    return False


# ── Login throttling ─────────────────────────────────────────

def client_ip():
    """The client address for throttling: normalized like scan addresses, or 'unknown'."""
    return network_client_ip() or 'unknown'


def _throttle_rules(username, ip):
    """(key, row username, failure limit, window, lock) for each throttle a failed login counts against.

    The address-wide row has no username, so one account's successful login or
    password reset does not clear it.
    """
    cfg = current_app.config
    rules = [
        (
            f"ip:{ip}|{username}",
            username,
            cfg['LOGIN_IP_FAILURE_LIMIT'],
            timedelta(minutes=cfg['LOGIN_IP_WINDOW_MINUTES']),
            timedelta(minutes=cfg['LOGIN_IP_LOCK_MINUTES']),
        ),
        (
            f"acct:{username}",
            username,
            cfg['LOGIN_ACCOUNT_FAILURE_LIMIT'],
            timedelta(minutes=cfg['LOGIN_ACCOUNT_WINDOW_MINUTES']),
            timedelta(minutes=cfg['LOGIN_ACCOUNT_LOCK_MINUTES']),
        ),
    ]
    if cfg['LOGIN_IP_TOTAL_FAILURE_LIMIT'] > 0:
        rules.append((
            f"ipall:{ip}",
            '',
            cfg['LOGIN_IP_TOTAL_FAILURE_LIMIT'],
            timedelta(minutes=cfg['LOGIN_IP_TOTAL_WINDOW_MINUTES']),
            timedelta(minutes=cfg['LOGIN_IP_TOTAL_LOCK_MINUTES']),
        ))
    return rules


def is_login_locked(username, ip):
    keys = [rule[0] for rule in _throttle_rules(username, ip)]
    return db.session.query(
        LoginThrottle.query.filter(LoginThrottle.key.in_(keys), LoginThrottle.locked_until > utc_now()).exists()
    ).scalar()


def _throttle_row(key):
    return LoginThrottle.query.filter_by(key=key).first()


def register_login_failure(username, ip):
    """Record a failed login and commit it.

    Two failed logins for the same account and address at the same moment can
    both find no throttle row and both insert one; the second insert then
    violates the unique key. Retry once: by then the row exists and is updated.
    Returns True when this failure started a lockout.
    """
    for attempt in range(2):
        try:
            # The conflict can surface on an autoflush inside, not only on commit.
            locked = record_login_failure(username, ip)
            db.session.commit()
            return locked
        except IntegrityError:
            db.session.rollback()
            if attempt:
                raise


def record_login_failure(username, ip):
    """Count a failed login against each throttle rule. Returns True if one of them now locks."""
    now = utc_now()
    locked = False
    for key, row_username, limit, window, lock in _throttle_rules(username, ip):
        row = _throttle_row(key)
        if row is None:
            row = LoginThrottle(key=key, username=row_username, failures=0, window_started_at=now)
            db.session.add(row)
        if row.window_started_at is None or now - row.window_started_at > window:
            row.failures = 0
            row.window_started_at = now
        row.failures = (row.failures or 0) + 1
        if row.failures >= limit:
            row.locked_until = now + lock
            row.failures = 0
            row.window_started_at = now
            locked = True

    stale_before = now - timedelta(days=1)
    LoginThrottle.query.filter(
        LoginThrottle.window_started_at < stale_before,
        db.or_(LoginThrottle.locked_until.is_(None), LoginThrottle.locked_until < now),
    ).delete(synchronize_session=False)
    return locked


def clear_login_failures(username):
    LoginThrottle.query.filter_by(username=username).delete(synchronize_session=False)


def locked_usernames(usernames):
    if not usernames:
        return set()
    rows = db.session.query(LoginThrottle.username).filter(
        LoginThrottle.username.in_(list(usernames)),
        LoginThrottle.locked_until > utc_now(),
    ).distinct()
    return {username for (username,) in rows}
