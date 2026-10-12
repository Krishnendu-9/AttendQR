"""Rotating QR tokens, scan URLs, scan grants, and student device binding.

Flow: the teacher's screen shows a QR whose signed token is valid for
QR_TOKEN_MAX_AGE seconds. Opening it stores a short-lived "scan grant" in the
student's own (signed) session, which survives logging in and changing an
initial password. Attendance is then marked against that grant.
"""
import base64
import io
import re
import secrets
import socket
import time
import uuid

import qrcode
from flask import current_app, request, session, url_for
from itsdangerous import BadSignature, URLSafeTimedSerializer

from ..models import Student

QR_TOKEN_SALT = 'qr-scan-salt'
SCAN_GRANT_KEY = 'scan_grant'
DEVICE_COOKIE = 'device_id'
DEVICE_COOKIE_MAX_AGE = 10 * 365 * 24 * 3600
_DEVICE_ID_PATTERN = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')

DEVICE_LOCKED_MESSAGE = (
    'This account is locked to a different device. You can only mark attendance from your registered device.'
)
DEVICE_TAKEN_MESSAGE = (
    'This device is already registered to another student. Each student must use their own phone; '
    'ask your teacher to reset the device lock if this is a mistake.'
)


# ── Tokens and URLs ──────────────────────────────────────────

def _serializer():
    return URLSafeTimedSerializer(current_app.config['SECRET_KEY'], salt=QR_TOKEN_SALT)


def make_qr_token(class_session_id):
    return _serializer().dumps({'session_id': class_session_id, 'nonce': secrets.token_hex(4)})


def read_qr_token(token):
    """Return the class session id from a fresh QR token.

    Raises itsdangerous.SignatureExpired for stale tokens and BadSignature for anything else invalid.
    """
    payload = _serializer().loads(token, max_age=current_app.config['QR_TOKEN_MAX_AGE'])
    class_session_id = payload.get('session_id') if isinstance(payload, dict) else None
    if not isinstance(class_session_id, int):
        raise BadSignature('QR token does not reference a class session')
    return class_session_id


def get_lan_ip():
    """Return the LAN IP reachable by phones on the same network."""
    override = current_app.config.get('APP_HOST_IP')
    if override:
        return override
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # No packet is sent; this only asks the OS which interface routes outward.
        sock.connect(('8.8.8.8', 80))
        return sock.getsockname()[0]
    except OSError:
        return socket.gethostbyname(socket.gethostname())
    finally:
        sock.close()


def build_scan_url(token):
    path = url_for('attendance.scan', token=token)
    base_url = current_app.config.get('APP_BASE_URL')
    if base_url:
        return f"{base_url.rstrip('/')}{path}"
    return f"http://{get_lan_ip()}:{current_app.config['APP_PORT']}{path}"


def qr_data_uri(text):
    buffer = io.BytesIO()
    qrcode.make(text).save(buffer, format='PNG')
    return 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode('ascii')


# ── Scan grants ──────────────────────────────────────────────

def grant_scan(class_session_id):
    session[SCAN_GRANT_KEY] = {
        'session_id': class_session_id,
        'expires': int(time.time()) + current_app.config['SCAN_GRANT_SECONDS'],
    }


def current_scan_grant():
    """The class session id from an unexpired scan grant, or None."""
    grant = session.get(SCAN_GRANT_KEY)
    if not isinstance(grant, dict):
        return None
    class_session_id = grant.get('session_id')
    expires = grant.get('expires')
    if not isinstance(class_session_id, int) or not isinstance(expires, int) or expires < time.time():
        session.pop(SCAN_GRANT_KEY, None)
        return None
    return class_session_id


def clear_scan_grant():
    session.pop(SCAN_GRANT_KEY, None)


# ── Devices ──────────────────────────────────────────────────

def read_device_cookie():
    value = request.cookies.get(DEVICE_COOKIE, '')
    return value if _DEVICE_ID_PATTERN.fullmatch(value) else None


def new_device_id():
    return str(uuid.uuid4())


def set_device_cookie(response, device_id):
    response.set_cookie(
        DEVICE_COOKIE,
        device_id,
        max_age=DEVICE_COOKIE_MAX_AGE,
        httponly=True,
        samesite='Lax',
        secure=current_app.config['SESSION_COOKIE_SECURE'],
    )


def ensure_device_cookie(response):
    if read_device_cookie() is None:
        set_device_cookie(response, new_device_id())
    return response


def device_binding_error(student, device_id):
    """Why this student cannot use this device, or None if it is allowed."""
    if student.device_id:
        return None if device_id == student.device_id else DEVICE_LOCKED_MESSAGE
    if device_id and Student.query.filter(Student.device_id == device_id, Student.id != student.id).first():
        return DEVICE_TAKEN_MESSAGE
    return None
