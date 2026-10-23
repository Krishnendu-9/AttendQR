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
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def staff_domains_from_env():
    raw = os.environ.get('ALLOWED_STAFF_DOMAINS', '@rcciit.org.in,@gmail.com')
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
    DEFAULT_PRINCIPAL_PASSWORD = os.environ.get('DEFAULT_PRINCIPAL_PASSWORD', 'ChangeMe123!')

    ALLOWED_STAFF_DOMAINS = staff_domains_from_env()

    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16 MB max upload size

    SESSION_COOKIE_HTTPONLY = True
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

    ATTENDANCE_TARGET = 80
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

    STUDENTS_PER_PAGE = 50
    SESSIONS_PER_PAGE = 50
