"""Load test: a whole class marks attendance within a few seconds.

    python scripts/load_test.py                               # 120 students within 10 s, SQLite
    python scripts/load_test.py --students 240 --window 15
    python scripts/load_test.py --database-url postgresql+psycopg://user:pass@localhost/loadtest --reset-database

What it does, against the real server (run.py under waitress, in its own process):

1. Builds a throwaway database: a SQLite file in a temporary folder, or the
   --database-url you give (its `public` schema is emptied; --reset-database is
   required so this never happens by accident). Seeds one class of N students,
   each with a real scrypt password hash, so every sign-in costs what it costs in use.
2. A teacher signs in and starts a QR session. A "projector" thread fetches a fresh
   QR every 10 seconds, as the screen in class does.
3. N students arrive at random moments within --window seconds, each with their own
   cookies (their own phone), and go through the whole flow: open the scan link,
   sign in, open the confirmation page, confirm.
4. Checks that the server recorded exactly N attendances, and prints latency
   percentiles per step. --json writes the numbers to a file.

Standard library only (plus the app itself for seeding).
"""
import argparse
import http.cookiejar
import json
import os
import random
import re
import secrets
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASSWORD = 'Load-Test-Pass-2026'  # noqa: S105 - throwaway accounts in a throwaway database
CSRF_PATTERN = re.compile(r'name="(?:_csrf_token|csrf-token)"\s+(?:value|content)="([^"]+)"')
STEPS = ('open scan link', 'sign-in page', 'sign in', 'confirmation page', 'confirm attendance')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Phone:
    """One browser: its own cookies, timing every request."""

    def __init__(self, base_url, timings):
        self.base_url = base_url
        self.timings = timings
        self.csrf = None
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()), NoRedirect())

    def request(self, step, path, form=None, json_body=None):
        url = path if path.startswith('http') else self.base_url + path
        headers, body = {}, None
        if json_body is not None:
            body = json.dumps(json_body).encode()
            headers = {'Content-Type': 'application/json', 'X-CSRFToken': self.csrf or ''}
        elif form is not None:
            body = urllib.parse.urlencode({**form, '_csrf_token': self.csrf or ''}).encode()
        started = time.perf_counter()
        try:
            response = self.opener.open(urllib.request.Request(url, data=body, headers=headers), timeout=60)  # noqa: S310
        except urllib.error.HTTPError as error:
            response = error
        text = response.read().decode('utf-8', 'replace')
        if step:
            self.timings[step].append((time.perf_counter() - started) * 1000)
        match = CSRF_PATTERN.search(text)
        if match:
            self.csrf = match.group(1)
        return response.status, response.headers.get('Location', ''), text


# ── Setup ────────────────────────────────────────────────────

def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def empty_database(url):
    import sqlalchemy as sa

    engine = sa.create_engine(url)
    with engine.begin() as connection:
        connection.exec_driver_sql('DROP SCHEMA IF EXISTS public CASCADE')
        connection.exec_driver_sql('CREATE SCHEMA public')
    engine.dispose()


def seed(database_url, students):
    """One department, semester, subject and teacher, and `students` students. Returns the teacher's email."""
    os.environ['DATABASE_URL'] = database_url
    from werkzeug.security import generate_password_hash

    from attendqr import create_app
    from attendqr.extensions import db
    from attendqr.models import Department, Semester, Student, Subject, Teacher, User

    app = create_app({'SECRET_KEY': secrets.token_hex(16), 'SQLALCHEMY_DATABASE_URI': database_url,
                      'INITIAL_PRINCIPAL_PASSWORD_FILE': os.path.join(tempfile.gettempdir(), 'attendqr-loadtest-pw')})
    with app.app_context():
        # Real cost: the same scrypt parameters as passwords people choose.
        password_hash = generate_password_hash(PASSWORD)
        department = Department(name='Load Test (LT)', branch='LT')
        db.session.add(department)
        db.session.flush()
        semester = Semester(name='Semester 1', department_id=department.id)
        teacher = Teacher(name='Load Teacher', email='teacher@loadtest.example', department_id=department.id)
        db.session.add_all([semester, teacher])
        db.session.flush()
        db.session.add(Subject(name='Load', paper_code='LT101', department_id=department.id,
                               semester_id=semester.id, teacher_id=teacher.id))
        db.session.add(User(username=teacher.email, password=password_hash, role='teacher', is_active=True,
                            department_id=department.id, teacher_id=teacher.id))
        for number in range(1, students + 1):
            student = Student(name=f'Student {number}', roll_no=f'LT{number:04d}', email=f's{number}@loadtest.example',
                              department_id=department.id, semester_id=semester.id)
            db.session.add(student)
            db.session.flush()
            db.session.add(User(username=student.email, password=password_hash, role='student', is_active=True,
                                department_id=department.id, student_id=student.id))
        teacher_email = teacher.email
        db.session.commit()
        db.engine.dispose()
    return teacher_email


def start_server(database_url, port, threads, log_path):
    env = {**os.environ, 'DATABASE_URL': database_url, 'FLASK_RUN_PORT': str(port), 'FLASK_DEBUG': '0',
           'FLASK_RUN_HOST': '127.0.0.1',
           'APP_BASE_URL': f'http://127.0.0.1:{port}', 'SCAN_ALLOWED_NETWORKS': '127.0.0.0/8',
           'SECRET_KEY': secrets.token_hex(32), 'ACCESS_LOG': '0', 'WAITRESS_THREADS': str(threads)}
    # Server output goes to a file: an unread pipe fills up and then blocks the server.
    with open(log_path, 'wb') as log:
        server = subprocess.Popen([sys.executable, str(ROOT / 'run.py')], cwd=ROOT, env=env,  # noqa: S603
                                  stdout=log, stderr=subprocess.STDOUT)
    for _ in range(100):
        try:
            urllib.request.urlopen(f'http://127.0.0.1:{port}/healthz', timeout=1)
            return server
        except OSError:
            if server.poll() is not None:
                raise SystemExit('The server stopped:\n' + Path(log_path).read_text(errors='replace')) from None
            time.sleep(0.2)
    server.kill()
    raise SystemExit(f'The server did not start within 20 seconds; see {log_path}.')


class FlowError(Exception):
    """A step of the attendance flow did not answer as it should."""


def expect(condition, message):
    if not condition:
        raise FlowError(message)


# ── The class ────────────────────────────────────────────────

def start_session(base_url, teacher_email):
    teacher = Phone(base_url, defaultdict(list))
    teacher.request(None, '/login')
    status, location, _ = teacher.request(None, '/login', {'username': teacher_email, 'password': PASSWORD})
    expect(status == 302, f'teacher sign-in failed ({status})')
    teacher.request(None, '/generate_qr')
    status, location, _ = teacher.request(None, '/generate_qr', {
        'class_mode': 'regular', 'department_id': 1, 'semester_id': 1, 'subject_id': 1, 'duration': 30})
    match = re.search(r'session_id=(\d+)', location)
    if status != 302 or not match:
        raise FlowError(f'could not start a session ({status} {location})')
    return teacher, int(match.group(1))


class Projector(threading.Thread):
    """Shows a fresh QR every 10 seconds, like the teacher's screen."""

    def __init__(self, teacher, session_id):
        super().__init__(daemon=True)
        self.teacher, self.session_id = teacher, session_id
        self.scan_url = None
        self.stop = threading.Event()
        self.refresh()

    def refresh(self):
        _, _, text = self.teacher.request(None, f'/api/session/{self.session_id}/qr')
        self.scan_url = json.loads(text)['scan_url']

    def run(self):
        while not self.stop.wait(10):
            self.refresh()


def student_flow(base_url, number, projector, delay, timings, errors):
    time.sleep(delay)
    phone = Phone(base_url, timings)
    started = time.perf_counter()
    try:
        status, location, _ = phone.request('open scan link', projector.scan_url)
        expect(status == 302 and location.endswith('/attend'), f'scan -> {status} {location}')
        phone.request('sign-in page', '/login?next=/attend')
        status, location, _ = phone.request('sign in', '/login?next=/attend',
                                            {'username': f's{number}@loadtest.example', 'password': PASSWORD})
        expect(status == 302, f'sign in -> {status}')
        status, _, text = phone.request('confirmation page', '/attend')
        expect(status == 200 and 'Mark Attendance' in text, f'attend -> {status}')
        status, _, text = phone.request('confirm attendance', '/mark_attendance', json_body={})
        expect(status == 200 and json.loads(text).get('success'), f'confirm -> {status} {text[:120]}')
        timings['whole flow'].append((time.perf_counter() - started) * 1000)
    except (FlowError, OSError, ValueError) as error:  # every failure is reported; none stops the test
        errors.append(f'student {number}: {error}')


def percentile(values, p):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(p / 100 * (len(ordered) - 1)))]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--students', type=int, default=120)
    parser.add_argument('--window', type=float, default=10.0, help='seconds over which students arrive')
    parser.add_argument('--threads', type=int, default=8, help='waitress worker threads (WAITRESS_THREADS)')
    parser.add_argument('--database-url', help='default: a SQLite file in a temporary folder')
    parser.add_argument('--reset-database', action='store_true', help='allow emptying --database-url')
    parser.add_argument('--seed', type=int, default=1, help='random seed for arrival times')
    parser.add_argument('--json', help='also write the results to this file')
    args = parser.parse_args()

    workdir = tempfile.mkdtemp(prefix='attendqr-load-')
    database_url = args.database_url or f"sqlite:///{Path(workdir, 'load.db').as_posix()}"
    if args.database_url:
        if not args.reset_database:
            raise SystemExit('--database-url is emptied first; pass --reset-database to confirm.')
        empty_database(database_url)

    print(f'Seeding {args.students} students ...')
    teacher_email = seed(database_url, args.students)
    port = free_port()
    server = start_server(database_url, port, args.threads, Path(workdir, 'server.log'))
    base_url = f'http://127.0.0.1:{port}'
    try:
        teacher, session_id = start_session(base_url, teacher_email)
        projector = Projector(teacher, session_id)
        projector.start()

        rng = random.Random(args.seed)  # noqa: S311 - arrival times, not security
        timings, errors = defaultdict(list), []
        print(f'{args.students} students arriving within {args.window:g} s ({args.threads} server threads) ...')
        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=args.students) as pool:
            for number in range(1, args.students + 1):
                pool.submit(student_flow, base_url, number, projector, rng.uniform(0, args.window), timings, errors)
        elapsed = time.perf_counter() - started
        projector.stop.set()

        _, _, text = teacher.request(None, f'/api/session_attendance/{session_id}')
        recorded = json.loads(text)['total']
    finally:
        server.terminate()
        server.wait(timeout=10)

    database = 'PostgreSQL' if database_url.startswith('postgresql') else 'SQLite'
    requests = sum(len(timings[step]) for step in STEPS)
    print(f'\n{database}, {args.students} students, arrivals over {args.window:g} s, {args.threads} threads')
    print(f'Marked: {recorded}/{args.students}   errors: {len(errors)}   wall time: {elapsed:.1f} s   '
          f'requests: {requests} ({requests / elapsed:.0f}/s)\n')
    print(f"{'step':<20}{'p50 ms':>9}{'p95 ms':>9}{'p99 ms':>9}{'max ms':>9}")
    results = {'database': database, 'students': args.students, 'window_s': args.window, 'threads': args.threads,
               'marked': recorded, 'errors': errors, 'wall_time_s': round(elapsed, 2), 'steps': {}}
    for step in (*STEPS, 'whole flow'):
        values = timings.get(step) or [0]
        row = {'p50': percentile(values, 50), 'p95': percentile(values, 95),
               'p99': percentile(values, 99), 'max': max(values), 'mean': statistics.fmean(values)}
        results['steps'][step] = {key: round(value, 1) for key, value in row.items()}
        print(f"{step:<20}{row['p50']:>9.0f}{row['p95']:>9.0f}{row['p99']:>9.0f}{row['max']:>9.0f}")
    for error in errors[:10]:
        print('  ' + error)
    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=2), encoding='utf-8')
    if errors or recorded != args.students:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
