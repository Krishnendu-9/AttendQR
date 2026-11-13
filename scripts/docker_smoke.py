"""Drive a running AttendQR server through one real QR attendance flow.

Usage: PRINCIPAL_PASSWORD=... python scripts/docker_smoke.py http://127.0.0.1:5000

Starts from a fresh database: the seeded Principal logs in with the generated
one-time password and is forced to replace it, sets up a class, and starts a
QR session for a special class. A new student opens the scan link, logs in
with the one-time password shown to the Principal, replaces it, and marks attendance. The Principal then
sees exactly one entry. Standard library only, so it runs on the CI runner
without installing the app.
"""
import base64
import hashlib
import hmac
import http.cookiejar
import json
import os
import re
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

PRINCIPAL_EMAIL = os.environ.get('DEFAULT_PRINCIPAL_EMAIL', 'principal@example.com')
# The generated one-time password, read from the container by CI (see .github/workflows/ci.yml).
PRINCIPAL_PASSWORD = os.environ.get('PRINCIPAL_PASSWORD', '')
STUDENT = {'name': 'Smoke Student', 'roll_no': 'SMK001', 'email': 'smoke.student@example.com'}
CSRF_PATTERN = re.compile(r'name="(?:_csrf_token|csrf-token)"\s+(?:value|content)="([^"]+)"')
TEMPORARY_PASSWORD_PATTERN = re.compile(r'Temporary password: ([A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4})')
TWO_STEP_KEY_PATTERN = re.compile(r'data-two-step-key>([A-Z2-7 ]+)<')


def totp(key, at=None):
    """The 6-digit code an authenticator app shows for this key (RFC 6238), without the app."""
    secret = base64.b32decode(key.replace(' ', '') + '=' * (-len(key.replace(' ', '')) % 8))
    digest = hmac.new(secret, struct.pack('>Q', int(at or time.time()) // 30), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    return f"{(struct.unpack('>I', digest[offset:offset + 4])[0] & 0x7FFFFFFF) % 1_000_000:06d}"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Browser:
    """A cookie-keeping client that reports redirects instead of following them."""

    def __init__(self, base_url):
        self.base_url = base_url.rstrip('/')
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()), NoRedirect(),
        )
        self.csrf = None

    def request(self, path, data=None, json_body=None):
        url = path if path.startswith('http') else self.base_url + path
        headers = {}
        body = None
        if json_body is not None:
            body = json.dumps(json_body).encode()
            headers = {'Content-Type': 'application/json', 'X-CSRFToken': self.csrf}
        elif data is not None:
            body = urllib.parse.urlencode({**data, '_csrf_token': self.csrf}).encode()
        try:
            # The scheme is checked in main(), so only http(s) URLs reach here.
            req = urllib.request.Request(url, data=body, headers=headers)  # noqa: S310
            response = self.opener.open(req, timeout=10)
        except urllib.error.HTTPError as error:
            response = error
        text = response.read().decode('utf-8', 'replace')
        match = CSRF_PATTERN.search(text)
        if match:
            self.csrf = match.group(1)
        return response.status, response.headers.get('Location', ''), text

    def get(self, path):
        return self.request(path)

    def post(self, path, **data):
        if self.csrf is None:
            self.get('/login')
        return self.request(path, data=data)

    def get_json(self, path):
        status, _, text = self.get(path)
        check(status == 200, f"GET {path} returned {status}")
        return json.loads(text)


def check(condition, message):
    if not condition:
        raise SystemExit(f"FAILED: {message}")
    print(f"ok - {message}")


def expect_redirect(result, ending, what):
    status, location, _ = result
    check(status in (302, 303) and location.split('?')[0].endswith(ending), f"{what} (-> {location or status})")
    return location


def main(base_url):
    if urllib.parse.urlparse(base_url).scheme not in ('http', 'https'):
        raise SystemExit(f"Expected an http(s) URL, got {base_url!r}")
    if not PRINCIPAL_PASSWORD:
        raise SystemExit("Set PRINCIPAL_PASSWORD to the Principal's first password.")
    check(json.loads(Browser(base_url).get('/healthz')[2]) == {'status': 'ok'}, "health check reports ok")

    principal = Browser(base_url)
    principal.get('/login')
    check(not Browser(base_url).post('/login', username=PRINCIPAL_EMAIL, password='ChangeMe123!')[1],
          "the old published password does not work")
    expect_redirect(principal.post('/login', username=PRINCIPAL_EMAIL, password=PRINCIPAL_PASSWORD),
                    '/', "seeded Principal logs in with the generated password")
    expect_redirect(principal.get('/departments'), '/account', "Principal must replace the one-time password first")
    new_password = 'Smoke-Harbor-2026'
    principal.get('/account')
    expect_redirect(principal.post('/account', current_password=PRINCIPAL_PASSWORD,
                                   new_password=new_password, confirm_password=new_password),
                    '/account', "Principal sets a private password")
    expect_redirect(principal.get('/departments'), '/account/two-step', "Principal must set up two-step sign-in next")
    match = TWO_STEP_KEY_PATTERN.search(principal.get('/account/two-step')[2])
    check(match is not None, "two-step setup shows the key for an authenticator app")
    status, _, page = principal.post('/account/two-step', action='enable', code=totp(match.group(1)))
    check(status == 200 and 'recovery codes' in page.lower(), "Principal turns on two-step sign-in and gets recovery codes")

    principal.post('/departments', branch='SMOKE', name='Smoke Testing')
    page = principal.get('/generate_qr')[2]
    match = re.search(r'<option value="(\d+)">[^<]*smoke', page, re.IGNORECASE)
    check(match is not None, "department created")
    department_id = match.group(1)

    principal.post('/semesters', department_id=department_id, name='Semester 1')
    semesters = principal.get_json(f'/api/semesters/{department_id}')
    check(len(semesters) == 1, "semester created")
    semester_id = semesters[0]['id']

    principal.post('/students', department_id=department_id, semester_id=semester_id, **STUDENT)
    match = TEMPORARY_PASSWORD_PATTERN.search(principal.get('/students')[2])
    check(match is not None, "student added and their one-time password shown once")
    student_first_password = match.group(1)
    location = expect_redirect(
        # A special class needs no teacher account; scanning and marking work the same way.
        principal.post('/generate_qr', class_mode='special', special_title='Smoke test class',
                       department_id=department_id, semester_id=semester_id, duration=10),
        '/generate_qr', "QR session started",
    )
    session_id = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)['session_id'][0]
    scan_url = principal.get_json(f'/api/session/{session_id}/qr')['scan_url']
    check(scan_url.startswith(f"{base_url.rstrip('/')}/scan/"), f"QR points at APP_BASE_URL ({scan_url[:40]}...)")

    phone = Browser(base_url)
    expect_redirect(phone.get(scan_url), '/attend', "student's phone opens the scan link")
    expect_redirect(phone.get('/attend'), '/login', "not logged in yet, so the scan waits for login")
    phone.get('/login?next=/attend')
    expect_redirect(phone.post('/login', username=STUDENT['email'], password=student_first_password, next='/attend'),
                    '/attend', "student logs in with the one-time password")
    expect_redirect(phone.get('/attend'), '/account', "student must replace the one-time password")
    phone.get('/account?next=/attend')
    student_password = 'Smoke-Student-2026'
    expect_redirect(phone.post('/account', current_password=student_first_password, new_password=student_password,
                               confirm_password=student_password, next='/attend'),
                    '/attend', "student sets a private password and returns to the scan")
    status, _, page = phone.get('/attend')
    check(status == 200 and 'Mark My Attendance' in page, "attendance confirmation page shown")
    status, _, body = phone.request('/mark_attendance', json_body={})
    check(status == 200 and json.loads(body).get('success') is True, f"attendance marked ({body.strip()})")

    attendance = principal.get_json(f'/api/session_attendance/{session_id}')
    check(attendance['total'] == 1 and attendance['attendances'][0]['student_roll'] == STUDENT['roll_no'],
          "Principal sees exactly one entry, for the student")
    print("Smoke test passed.")


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    main(sys.argv[1])
