"""The app in a real browser: Chromium, driven by Playwright, against a live server.

The rest of the suite uses Flask's test client, which never runs the pages' JavaScript.
These tests do: the session form that fills its lists from the API, the QR that refreshes,
the student's confirm button, and the teacher's list that updates by itself. They also run
axe-core on the main pages for each role and fail on serious accessibility problems.

They need the browser test tools, which a plain `pytest` run leaves out (marker `browser`):

    python -m pip install -r requirements-dev.txt
    python -m playwright install chromium
    python -m pytest -m browser tests/browser
"""
import re
import threading

import pytest
from axe_playwright_python.sync_playwright import Axe
from playwright.sync_api import Page, expect
from werkzeug.serving import make_server

from attendqr import create_app
from attendqr.extensions import db
from attendqr.services.demo import DEFAULT_DEMO_PASSWORD, seed_demo

pytestmark = pytest.mark.browser

PRINCIPAL = 'principal@example.com'
HOD = 'manas.ghosh@college.example'
TEACHER = 'ananya.mukherjee@college.example'  # teaches MCA C101, Programming Concept with C
STUDENT = 'mca2024003@college.example'  # Chocolate, MCA Semester 1
STUDENT_NAME = 'Chocolate'

# axe-core impact levels that fail a test. "minor" and "moderate" findings are printed only.
FAILING_IMPACTS = ('serious', 'critical')
# Long enough for the QR page's refresh (every QR_REFRESH_SECONDS, 10 by default) to notice.
LIVE_UPDATE_TIMEOUT_MS = 25_000


@pytest.fixture(scope='module')
def live_server(tmp_path_factory):
    """The app served by Werkzeug in a thread, on a free local port, with the demo college."""
    folder = tmp_path_factory.mktemp('browser')
    app = create_app({
        'SECRET_KEY': 'browser-tests-secret-key-not-for-real-use',
        'SQLALCHEMY_DATABASE_URI': f"sqlite:///{(folder / 'demo.db').as_posix()}",
        'DEFAULT_PRINCIPAL_EMAIL': PRINCIPAL,
        'DEFAULT_PRINCIPAL_PASSWORD': 'Initial123',
        'INITIAL_PRINCIPAL_PASSWORD_FILE': str(folder / 'initial_principal_password'),
        'ALLOWED_STAFF_DOMAINS': ['@college.example'],
        # The browser connects from 127.0.0.1, which the default (private ranges) leaves out.
        'SCAN_ALLOWED_NETWORKS': '127.0.0.0/8',
        'TOTP_REQUIRED_ROLES': (),
        'ACCESS_LOG': False,
    })
    with app.app_context():
        seed_demo(DEFAULT_DEMO_PASSWORD, weeks=1)
        db.session.remove()

    server = make_server('127.0.0.1', 0, app, threaded=True)
    base_url = f'http://127.0.0.1:{server.server_port}'
    app.config['APP_BASE_URL'] = base_url  # the address the QR code points at
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield base_url
    server.shutdown()
    thread.join(timeout=10)
    with app.app_context():
        db.engine.dispose()


def sign_in(page: Page, base_url, username):
    page.goto(f'{base_url}/login')
    page.locator('#username').fill(username)
    page.locator('#password').fill(DEFAULT_DEMO_PASSWORD)
    page.get_by_role('button', name='Sign in').click()
    expect(page).not_to_have_url(re.compile(r'/login'))


def start_session(page: Page, base_url):
    """As the teacher: start a 30-minute C101 session from the form; returns the scan link shown."""
    page.goto(f'{base_url}/generate_qr')
    department = page.locator('#dept_select option', has_text='MCA').first.get_attribute('value')
    page.locator('#dept_select').select_option(department)
    # The semesters and subjects come from the API after each choice.
    expect(page.locator('#sem_select option', has_text='Semester 1')).to_have_count(1)
    page.locator('#sem_select').select_option(label='Semester 1')
    subject = page.locator('#sub_select option', has_text='Programming Concept with C')
    expect(subject).to_have_count(1)
    page.locator('#sub_select').select_option(subject.get_attribute('value'))
    page.get_by_role('button', name='Start session').click()

    expect(page.get_by_role('img', name='Attendance QR code')).to_be_visible()
    scan_url = page.locator('#scanUrlText').inner_text().strip()
    assert scan_url.startswith(f'{base_url}/scan/')
    return scan_url


def test_a_student_marks_attendance_from_a_phone_and_the_teacher_sees_it(live_server, browser, playwright):
    teacher = browser.new_page()
    sign_in(teacher, live_server, TEACHER)
    scan_url = start_session(teacher, live_server)
    attendance = teacher.locator('#qr-attendance')
    expect(attendance).to_have_attribute('data-present', '0')

    phone_context = browser.new_context(**playwright.devices['Pixel 7'])
    phone = phone_context.new_page()
    # Opening the QR before signing in: the scan is kept through the sign-in.
    phone.goto(scan_url)
    expect(phone).to_have_url(re.compile(r'/login'))
    phone.locator('#username').fill(STUDENT)
    phone.locator('#password').fill(DEFAULT_DEMO_PASSWORD)
    phone.get_by_role('button', name='Sign in').click()

    confirm = phone.get_by_role('button', name='Mark My Attendance')
    expect(confirm).to_be_visible()
    confirm.click()
    expect(phone.locator('#msg')).to_contain_text(f'Attendance marked for {STUDENT_NAME}')
    expect(confirm).to_have_count(0)

    # The teacher's screen updates without a reload: the section is replaced with the new count,
    # the student is listed as present (no longer offered for marking by hand), and the code has moved on.
    expect(attendance).to_have_attribute('data-present', '1', timeout=LIVE_UPDATE_TIMEOUT_MS)
    expect(attendance).to_contain_text(STUDENT_NAME)
    expect(teacher.locator('#manual_student option', has_text=STUDENT_NAME)).to_have_count(0)
    expect(teacher.locator('#scanUrlText')).not_to_have_text(scan_url)

    # The same scan cannot be used twice.
    phone.goto(scan_url)
    expect(phone.get_by_role('button', name='Mark My Attendance')).to_have_count(0)
    phone_context.close()
    teacher.close()


def test_a_scan_link_with_a_bad_code_shows_a_clear_page(live_server, browser):
    """The scan link carries a signed token; a wrong one is explained, not an error page."""
    page = browser.new_page()
    page.goto(f'{live_server}/scan/not-a-real-token')
    expect(page.get_by_role('heading', name='Invalid QR')).to_be_visible()
    page.close()


# ── Accessibility ────────────────────────────────────────────

def serious_violations(page: Page):
    """axe-core's findings on the page that are serious or critical, as readable lines."""
    results = Axe().run(page)
    lines = []
    for violation in results.response['violations']:
        if violation['impact'] not in FAILING_IMPACTS:
            continue
        targets = ', '.join(' '.join(node['target']) for node in violation['nodes'][:5])
        lines.append(f"{violation['id']} ({violation['impact']}): {violation['help']} -> {targets}")
    return lines


PAGES = [
    (None, '/login'),
    (STUDENT, '/'),
    (STUDENT, '/leave'),
    (STUDENT, '/routine/me'),
    (TEACHER, '/'),
    (TEACHER, '/generate_qr'),
    (TEACHER, '/students'),
    (TEACHER, '/routine/me'),
    (HOD, '/'),
    (HOD, '/leave'),
    (HOD, '/reports'),
    (HOD, '/reports/analytics'),
    (HOD, '/reports/shortage'),
    (HOD, '/reports/integrity'),
    (HOD, '/reports/eligibility'),
    (PRINCIPAL, '/accounts'),
    (PRINCIPAL, '/settings'),
]


@pytest.mark.parametrize('theme', ['light', 'dark'])
@pytest.mark.parametrize(('username', 'path'), PAGES, ids=[f"{u.split('@')[0] if u else 'anyone'}{p}" for u, p in PAGES])
def test_pages_have_no_serious_accessibility_problems(live_server, browser, username, path, theme):
    context = browser.new_context(color_scheme=theme)
    page = context.new_page()
    if username:
        sign_in(page, live_server, username)
    response = page.goto(f'{live_server}{path}')
    assert response is not None and response.ok, f'{path} answered {response and response.status}'
    page.wait_for_load_state('networkidle')
    problems = serious_violations(page)
    context.close()
    assert not problems, f'{path} ({theme}):\n' + '\n'.join(problems)
