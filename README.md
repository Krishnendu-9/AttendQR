# AttendQR

[![CI](https://github.com/Krishnendu-9/AttendQR/actions/workflows/ci.yml/badge.svg)](https://github.com/Krishnendu-9/AttendQR/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

College attendance by QR code, built so that marking attendance for an absent friend is hard. The teacher's screen shows a code that changes every 10 seconds. A student scans it with their own phone, on the campus network, and confirms with their own account. Departments, roles, leave, reports, shortage lists and analytics are built around it, and every record can be audited: random spot checks in class, an integrity report that flags proxy patterns, and a tamper-evident activity log.

Flask · SQLAlchemy · PostgreSQL or SQLite · server-rendered pages with htmx and Alpine.js · Docker · 330+ tests (90% coverage) in CI on two databases

| The teacher's screen | The student's phone |
| --- | --- |
| ![Live QR session with the attendance list](docs/screenshots/live-session.png) | ![Confirming attendance on a phone](docs/screenshots/phone-confirm.png) |

## Try it in two minutes

```powershell
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.lock
$env:DATABASE_URL = "sqlite:///demo.db"          # a separate database: instance/demo.db
.\venv\Scripts\flask.exe --app wsgi seed-demo    # a sample college with 6 weeks of attendance
.\venv\Scripts\python.exe run.py                 # http://localhost:5000
```

The Principal and HOD must set up two-step sign-in the first time: scan the QR code shown with any authenticator app (Google, Microsoft, Zoho...). To skip that in a quick demo, run `$env:TOTP_REQUIRED_ROLES = "none"` before `run.py`.

Every demo account uses the password `AttendQR@demo1`:

| Role | Sign in as | Try |
| --- | --- | --- |
| Principal | `principal@example.com` | Everything: all departments, accounts, reports |
| HOD | `hod.partha.sarkar@college.example` | Integrity report, exam eligibility, analytics, approving leave |
| Teacher | `ananya.mukherjee@college.example` | Start a QR session and scan it from a phone on the same Wi-Fi |
| Student | `aditi.kundu@students.college.example` | Own attendance, applying for leave |

`seed-demo` refuses to touch a database that already has records. Docker, PostgreSQL, production settings and backups are in [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## What stops proxy attendance

```mermaid
sequenceDiagram
    autonumber
    participant T as Teacher's screen
    participant P as Student's phone
    participant S as AttendQR
    T->>S: Every 10 s: a new QR (signed token, valid 30 s)
    P->>S: Open the scan link
    S->>S: On the campus network? Token signed and fresh? Session live?
    S-->>P: A one-time scan grant, tied to this phone's device cookie
    P->>S: Sign in, then confirm
    S->>S: Same phone? Still on campus? Right class? Phone registered to this student?
    S->>S: Store once (unique per student and session, and per scan grant)
    S-->>T: The list updates live
```

| Trick | Why it fails |
| --- | --- |
| Photograph the QR and send it to a friend | The code is replaced every 10 s and expires 30 s after it is shown. At home, the friend is not on the campus network the server accepts scans from. |
| Copy the scan cookie to another browser | The scan grant is bound to the phone that scanned, the network is checked again, and each grant can mark attendance only once. |
| Mark two students from one phone | Each phone is registered to one student, and each student to one phone. |
| Get a friend to scan for you in time | **Spot checks**: the teacher looks for 3 students picked at random from those marked present. Anyone not in the room loses the attendance, and the **integrity report** flags them, along with pairs who keep scanning seconds apart from one address. |
| Let a teacher fix it quietly | Manual entries need a reason, show as `P*` in every report and export, and are in the activity log. |
| Change records afterwards | The activity log is hash-chained, so `flask verify-audit` finds any edited or deleted entry, and a **locked** semester cannot change at all. |

Each rule has tests. What is *not* covered, such as registering a friend's phone before they do, is listed in the [threat model](docs/THREAT_MODEL.md), along with the fix (passkeys, which browsers allow only over HTTPS).

## Features

- **Four roles, scoped by department.** Principal, HOD, Teacher and Student. Every query and page is limited to what the role may see ([docs/RBAC.md](docs/RBAC.md)).
- **QR sessions.** Rotating codes, a live attendance list, manual corrections with reasons, and special classes (workshops) that are shown but not counted.
- **Leave.** Students apply; the HOD or Principal approves or rejects with a note. Classes during approved leave count neither way.
- **Reports.** A per-session list, a student-by-session matrix, Excel and PDF exports, and a shortage list showing the classes each student needs to reach 80%.
- **Analytics.** The weekly class rate against the target, each subject's rate, and the students slipping in the last two weeks. Server-rendered SVG charts with tooltips and a table view.
- **Integrity.** Spot checks during class, and a report that explains each flag: failed spot checks, buddy pairs, repeated new phones, frequent manual entries.
- **Exam eligibility.** Eligible, needs condonation or detained against configurable limits (75% and 65% by default), condonation with a reason, and an Excel export.
- **Final records.** A semester's attendance can be locked after exams, and the activity log is tamper-evident.
- **Students moved between classes** stay in their old class's reports for the sessions they were part of.
- **Accounts.** Two-step sign-in with any authenticator app (required for Principal and HOD), random one-time passwords, forced password changes, brute-force throttling per device, and every session signed out on a password change.

| Analytics | Shortage list | Leave requests |
| --- | --- | --- |
| ![Weekly attendance, subjects and students at risk](docs/screenshots/analytics.png) | ![Students below 80% with the classes they need](docs/screenshots/shortage.png) | ![Leave requests waiting for the HOD](docs/screenshots/leave.png) |

| Integrity report | Exam eligibility |
| --- | --- |
| ![Students flagged with the reasons, and a buddy pair](docs/screenshots/integrity.png) | ![Eligible, needs condonation and detained students](docs/screenshots/eligibility.png) |

## Measured performance

`scripts/load_test.py` runs the real server (waitress, one process, 8 threads) and sends a whole class through the flow: open the scan link, sign in with real scrypt password hashes, open the confirmation page, confirm. Each simulated phone has its own cookies. Measured on a Windows 11 PC with 12 logical CPUs, with the server and the phones on the same machine. Bursts were run 2-3 times; the table gives the range.

| Scenario | Recorded | Median per student | 95th percentile |
| --- | --- | --- | --- |
| 120 students arriving over 60 s | 120 / 120 | 0.25 s | 0.38 s |
| 120 students within 10 s, SQLite | 120 / 120 | 5.0 - 9.3 s | 7.6 - 12.3 s |
| 120 students within 10 s, PostgreSQL | 120 / 120 | 4.9 - 7.3 s | 6.3 - 9.1 s |
| 240 students within 10 s, SQLite | 240 / 240 | 19.7 s | 26.0 s |

**What this shows:**

- **Correctness holds under load.** No attendance was lost or recorded twice in any run.
- **One process handles about 35 requests per second, roughly 7 students per second.** A class scanning over a minute sees quarter-second responses. A whole class within 10 seconds queues, and students wait several seconds.
- **The limit is CPU in one process, not the database.** PostgreSQL gives the same result as SQLite. About two thirds of each student's server time is the scrypt password check at sign-in, which is deliberately slow. The fix is more worker processes (`gunicorn -w 4` on PostgreSQL; SQLite allows one writer at a time), not a cheaper password hash.

## Engineering

- **Tests.** pytest, with each test on its own database built by the real migrations. CI runs them on Python 3.11 to 3.13 and on PostgreSQL. It also runs a migration-versus-model check on both databases, pyright, ruff (including bandit's security rules), a coverage floor, `pip-audit` on the pinned dependencies, and a Docker smoke test that walks the attendance flow against the built image.
- **Migrations.** Alembic, with tests that upgrade and downgrade with data in place, and an automatic upgrade path for databases created before migrations existed.
- **Time.** Stored in UTC and shown in the college's time zone (`TIMEZONE`).
- **Security.** Two-step sign-in (TOTP, each code usable once), CSRF on every form, a CSP with no inline scripts or styles, signed QR tokens, network allowlisting that fails closed, an HMAC-chained activity log, and a 30-day retention for scan IP addresses. See the [threat model](docs/THREAT_MODEL.md).
- **Operations.** Request ids in every log line, optional JSON logs, an access log without IPs or tokens, Prometheus metrics behind a token, a health check, safe online backups, and a non-root Docker image.

```text
attendqr/
  blueprints/     routes: auth, attendance (QR), reports and analytics, leave, students, academics, accounts, api
  services/       QR tokens, reports, analytics, leave, spot checks, integrity, eligibility, audit chain,
                  two-step sign-in, semester locks, enrollment history, imports, backups, demo data
  access.py       roles and department scope        security.py   CSRF, CSP, passwords, throttling
  models.py       SQLAlchemy models                  observability.py   request ids, logs, metrics
migrations/       Alembic migrations                 templates/, static/   pages, Tailwind CSS, vendored JS
tests/            pytest suite                       scripts/      load test, Docker smoke test, CSS build
```

More: [docs/PROJECT_STRUCTURE.md](docs/PROJECT_STRUCTURE.md).

## Documentation

| Document | For |
| --- | --- |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | Installing, configuring, Docker, PostgreSQL, network settings, backups, logs and metrics |
| [docs/GUIDE.md](docs/GUIDE.md) | How the app behaves: accounts and two-step sign-in, the QR flow, corrections, spot checks, leave, locks, eligibility, reports |
| [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md) | Attacks, defences and the gaps that remain |
| [docs/RBAC.md](docs/RBAC.md) | What each role can do |
| [SECURITY.md](SECURITY.md) | Reporting vulnerabilities, dependency auditing |

## Running the tests

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\venv\Scripts\python.exe -m pytest
.\venv\Scripts\python.exe -m ruff check .
.\venv\Scripts\pyright.exe
$env:TEST_DATABASE_URL = "postgresql+psycopg://postgres:test@localhost:5432/attendqr_test"   # optional: the suite on PostgreSQL
```

## Known limits and next steps

- **The device lock is a cookie.** It stops one phone serving two students, but not a student registering a friend's phone first. The next step is passkeys (WebAuthn): a key on the phone that cannot be copied. Browsers allow passkeys only over HTTPS, so they come with an HTTPS deployment.
- **Two-step secrets are stored as they are** in the database. Encrypting them with a key kept outside the database would come next.
- **The newest end of the audit chain** can be cut off together with its head record by someone with database access; keep the head that `flask verify-audit` prints with your backups.
- **One process by default.** See *Measured performance* for when to add workers on PostgreSQL.

## License

[MIT](LICENSE)
