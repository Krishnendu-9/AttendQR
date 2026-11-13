import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in ('1', 'true', 'yes', 'on')


def env_int(name, default):
    """An integer setting. A value that is not a whole number stops the app instead of being ignored."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError:
        raise ValueError(f"{name} must be a whole number (got {raw!r}).") from None


def staff_domains_from_env():
    # No default: which domains may hold staff accounts is the institution's decision.
    raw = os.environ.get('ALLOWED_STAFF_DOMAINS', '')
    domains = []
    for item in raw.split(','):
        domain = item.strip().lower()
        if not domain:
            continue
        # Always compare against "@domain" so "evil-rcciit.org.in" cannot match "rcciit.org.in".
        domains.append(domain if domain.startswith('@') else f'@{domain}')
    return domains


class Config:
    # Leave SECRET_KEY unset to have a private key generated in instance/secret_key.
    SECRET_KEY = os.environ.get('SECRET_KEY') or None

    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL', 'sqlite:///attendance.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Run pending database migrations on startup. Disable when running several
    # worker processes and run `flask db upgrade` once during deployment instead.
    AUTO_MIGRATE = env_bool('AUTO_MIGRATE', True)

    DEFAULT_PRINCIPAL_EMAIL = os.environ.get('DEFAULT_PRINCIPAL_EMAIL', 'principal@example.com')
    # Leave unset: a random first password is generated and written to
    # instance/initial_principal_password. The old published placeholder is ignored.
    DEFAULT_PRINCIPAL_PASSWORD = os.environ.get('DEFAULT_PRINCIPAL_PASSWORD', '')
    # Where that generated password is written; None means the instance folder.
    INITIAL_PRINCIPAL_PASSWORD_FILE = None

    ALLOWED_STAFF_DOMAINS = staff_domains_from_env()

    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16 MB max upload size
    # Rows accepted in one bulk student import; larger files are refused before any row is added.
    MAX_IMPORT_ROWS = 2000

    SESSION_COOKIE_HTTPONLY = True
    # Sign out a session after this many minutes without a request (0 = never). The
    # cookie also ends when the browser closes. Staff often use shared lab machines.
    SESSION_IDLE_MINUTES = env_int('SESSION_IDLE_MINUTES', 60)
    SESSION_COOKIE_SAMESITE = 'Lax'
    # Turn on once the app is served over HTTPS.
    SESSION_COOKIE_SECURE = env_bool('SESSION_COOKIE_SECURE', False)
    # Number of reverse proxies in front of the app (for correct client IPs and scheme).
    TRUSTED_PROXY_COUNT = env_int('TRUSTED_PROXY_COUNT', 0)

    # QR scan URLs. Leave empty to detect the LAN IP automatically.
    # A public base URL removes network locality as a defence against forwarded QRs
    # unless SCAN_ALLOWED_NETWORKS is set; see "Before deploying on a public address" in README.md.
    APP_BASE_URL = os.environ.get('APP_BASE_URL', '').strip()
    APP_HOST_IP = os.environ.get('APP_HOST_IP', '').strip()
    APP_PORT = os.environ.get('APP_PORT', '5000').strip() or '5000'

    # Client IP ranges (CIDR, comma-separated) allowed to open a scan link, or "any".
    # Empty means private LAN addresses only. A malformed value stops the app from
    # starting. See "Restricting scans to the campus network" in README.md.
    SCAN_ALLOWED_NETWORKS = os.environ.get('SCAN_ALLOWED_NETWORKS', '').strip()

    # Days to keep the IP address of each QR scan; 0 records none and clears existing ones.
    # See "Scan IP addresses" in README.md.
    SCAN_IP_RETENTION_DAYS = os.environ.get('SCAN_IP_RETENTION_DAYS', '30')

    # A QR token is accepted for this many seconds after it is shown on screen.
    QR_TOKEN_MAX_AGE = env_int('QR_TOKEN_MAX_AGE', 30)
    QR_REFRESH_SECONDS = env_int('QR_REFRESH_SECONDS', 10)
    # After a valid scan, the student has this long to log in and confirm attendance.
    SCAN_GRANT_SECONDS = env_int('SCAN_GRANT_SECONDS', 300)

    # The college's time zone (IANA name). Timestamps are stored in UTC and shown in this zone;
    # report date filters and "today" use its calendar days.
    TIMEZONE = os.environ.get('TIMEZONE', '').strip() or 'Asia/Kolkata'

    ATTENDANCE_TARGET = 80
    # Exam eligibility (the eligibility report): at or above ELIGIBILITY_PERCENT a student may sit
    # the exams; between CONDONATION_PERCENT and it, only if the HOD or Principal condones it with a
    # reason; below CONDONATION_PERCENT they are detained. Universities set their own numbers.
    ELIGIBILITY_PERCENT = env_int('ELIGIBILITY_PERCENT', 75)
    CONDONATION_PERCENT = env_int('CONDONATION_PERCENT', 65)
    MIN_QR_DURATION = 1
    MAX_QR_DURATION = 180
    MIN_PASSWORD_LENGTH = 8

    # Failed logins are throttled per (account, client IP) so that one person
    # cannot lock another user out from a different device, with a higher
    # account-wide ceiling to bound brute force from many addresses.
    LOGIN_IP_FAILURE_LIMIT = 5
    LOGIN_IP_WINDOW_MINUTES = 10
    LOGIN_IP_LOCK_MINUTES = 10
    LOGIN_ACCOUNT_FAILURE_LIMIT = 30
    LOGIN_ACCOUNT_WINDOW_MINUTES = 60
    LOGIN_ACCOUNT_LOCK_MINUTES = 15
    # Failed logins from one address across all accounts, so one machine cannot try a
    # few passwords against every student. 0 turns it off. Where many users share one
    # public address (a public deployment behind campus NAT), raise it or set 0.
    LOGIN_IP_TOTAL_FAILURE_LIMIT = env_int('LOGIN_IP_TOTAL_FAILURE_LIMIT', 100)
    LOGIN_IP_TOTAL_WINDOW_MINUTES = 10
    LOGIN_IP_TOTAL_LOCK_MINUTES = 10

    # Logs: 'text' (default) or 'json' (one object per line, for log collectors). Every line
    # carries the request id. The access log records route patterns only, never IPs or tokens.
    LOG_FORMAT = 'json' if os.environ.get('LOG_FORMAT', '').strip().lower() == 'json' else 'text'
    ACCESS_LOG = env_bool('ACCESS_LOG', True)
    # /metrics (Prometheus) is served only when this is set, to requests sending
    # `Authorization: Bearer <METRICS_TOKEN>`. Use a long random value.
    METRICS_TOKEN = os.environ.get('METRICS_TOKEN', '').strip()

    # Roles that must set up two-step sign-in (an authenticator app code) before using the app.
    # Other staff may turn it on from their account page. Empty (TOTP_REQUIRED_ROLES=none) requires nobody.
    TOTP_REQUIRED_ROLES = tuple(
        role.strip() for role in os.environ.get('TOTP_REQUIRED_ROLES', 'principal,hod').split(',')
        if role.strip() and role.strip() != 'none'
    )
    # The name authenticator apps show next to the code.
    TOTP_ISSUER = os.environ.get('TOTP_ISSUER', '').strip() or 'AttendQR'

    STUDENTS_PER_PAGE = 50
    SESSIONS_PER_PAGE = 50
