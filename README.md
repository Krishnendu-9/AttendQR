# QR-Based Smart Attendance Management System

[![CI](https://github.com/Krishnendu-9/AttendQR/actions/workflows/ci.yml/badge.svg)](https://github.com/Krishnendu-9/AttendQR/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

A Flask web application for managing classroom attendance through time-bound, rotating QR sessions, role-based access control, and downloadable attendance reports.

## Overview

This project is designed for college attendance workflows where Principal, HOD, Teacher, and Student users need different levels of access. Teachers generate QR sessions for assigned subjects, students scan the QR code with their own phone to mark attendance, and the system stores attendance records digitally for reporting and shortage tracking.

The application also manages departments, semesters, subjects, teacher profiles, student records, account governance, password reset flows, and recent account activity logs.

## Core Features

- Role-based access control for Principal, HOD, Teacher, and Student
- Department, semester, subject, teacher, and student management
- Single student creation and bulk import (CSV/XLSX) with automatic student logins
- Rotating QR codes: each code is valid for 30 seconds and refreshes every 10 seconds
- One registered device per student, and one student per device
- Duplicate attendance prevention per student per session
- Manual attendance correction by staff (add or remove, reason required, labelled `P*` in reports, audit-logged)
- Shortage list of students below the requirement, overall and per subject, with Excel export
- Special class sessions without mandatory teacher assignment
- Student dashboard with subject-wise percentage and 80% attendance tracking
- Excel and PDF attendance export
- Account status toggle, password reset, and activity logging
- Mandatory password change for new or reset accounts

## Technology Stack

Python 3.11+, Flask, Flask-SQLAlchemy, Flask-Login, Flask-Migrate (Alembic), SQLite, Bootstrap (served locally), qrcode + Pillow, openpyxl, reportlab, waitress, pytest.

## Project Structure

```text
AttendQR/
  attendqr/                 Application package
    __init__.py             create_app() factory, request guards, template helpers
    config.py               Configuration (environment variables)
    models.py               SQLAlchemy models
    access.py               Role-based access rules and scoped queries
    security.py             Secret key, CSRF, redirects, password policy, headers, login throttling
    schema.py               Database setup and upgrade of pre-migration databases
    utils.py                Small shared helpers
    services/               QR tokens, devices, scan networks and IP retention, reports and exports, student import
    blueprints/             Routes: auth, main, academics, students, attendance, reports, accounts, api
  migrations/               Alembic migrations (Flask-Migrate)
  templates/                Jinja2 templates
  static/                   CSS, JavaScript, and vendored Bootstrap/fonts (no CDN needed)
  tests/                    pytest suite
  scripts/docker_smoke.py   End-to-end check run against the Docker image in CI
  run.py                    Start the server (waitress, or the debug server)
  wsgi.py                   WSGI entry point
  Dockerfile, compose.yaml  Container image and how to run it
  pyproject.toml            Project metadata, pytest and ruff settings
  requirements.txt          Direct dependencies
  requirements.lock         Exact pinned versions for reproducible installs
  requirements-dev.txt      Test and lint tools
```

More detail is in [docs/PROJECT_STRUCTURE.md](docs/PROJECT_STRUCTURE.md).

## Setup

```powershell
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.lock
Copy-Item .env.example .env   # optional; every setting has a default
```

`requirements.lock` pins exact versions. `requirements.txt` lists the direct dependencies with compatible ranges.

You do not need to set `SECRET_KEY`. On first start AttendQR generates a private key and stores it in `instance/secret_key`. If `SECRET_KEY` is set to a published placeholder value, the app refuses to start outside debug mode.

## Running the Application

Serve on the local network (uses the waitress production server):

```powershell
.\venv\Scripts\python.exe run.py
```

Phones on the same Wi-Fi can then reach `http://<this-computer's-LAN-IP>:5000`.

For development with auto-reload and the interactive debugger:

```powershell
$env:FLASK_DEBUG="1"; .\venv\Scripts\python.exe run.py
```

In debug mode the server binds to `127.0.0.1` only. Werkzeug's debugger can run arbitrary code, so it is never exposed to the network.

On Linux you can also use `gunicorn wsgi:app`. With several worker processes, set `AUTO_MIGRATE=0` and run `flask init-db` once per deployment.

### Docker

```powershell
$env:APP_BASE_URL="http://192.168.0.170:5000"   # the address phones use to reach this computer
docker compose up -d --build
```

- **`APP_BASE_URL` is required.** Inside a container the app cannot detect the host's LAN IP, so compose refuses to start without it. Other settings are read from `.env` if it exists.
- **Data:** the SQLite database and the generated secret key live in the `attendqr-data` volume. `docker compose down --volumes` deletes them.
- **What runs:** `run.py` under waitress, as a non-root user, with only the packages in `requirements.lock`. `/healthz` checks the database, and the container reports healthy once it answers.
- **Client addresses:** on Docker Desktop (Windows and macOS), and on Linux whenever Docker's userland proxy handles a connection, the app sees every client as the Docker gateway (for example `172.22.0.1`). `SCAN_ALLOWED_NETWORKS` then cannot tell campus from anywhere else: with the default ranges, everyone looks like a LAN client. Scan IP addresses in the session view will also all be the same. On a Linux server, published ports normally keep the real client address. After the first class, check the session view to confirm you see real addresses. Behind a reverse proxy, set `TRUSTED_PROXY_COUNT` so the forwarded address is used instead.

CI builds the image, starts it with compose, waits for the health check, and runs [scripts/docker_smoke.py](scripts/docker_smoke.py) against it. The script walks one QR attendance flow end to end: Principal first login and forced password change, class setup, QR session, student scan, login, password change, mark, and the Principal seeing the entry.

## Database and Migrations

- Default database: SQLite at `instance/attendance.db` (set `DATABASE_URL` to change it)
- Schema changes are managed by Alembic migrations in `migrations/`. Pending migrations run automatically at startup.
- Databases created by older versions of AttendQR are detected and upgraded automatically on first start, keeping all data. Before that first start, back up `instance/attendance.db`.

Useful commands:

```powershell
flask init-db                         # create or upgrade the database
flask db migrate -m "describe change" # generate a migration after editing models.py
flask db upgrade                      # apply migrations
```

## Initial Login

The first Principal account is created from `DEFAULT_PRINCIPAL_EMAIL` and `DEFAULT_PRINCIPAL_PASSWORD` when the database has no accounts. That password is public (it is in `.env.example`), so the Principal must choose a new one at first login.

## Account Rules

- Staff account emails must end with one of `ALLOWED_STAFF_DOMAINS` (default `@rcciit.org.in` or `@gmail.com`; remove Gmail for a real deployment)
- Principal can create Principal, HOD, and Teacher accounts; HOD can create Teacher accounts for their department
- Only one active HOD is allowed per department
- Passwords need at least 8 characters with letters and numbers, and must differ from the user's email and roll number
- New and reset accounts must change their password before using the app
- Changing or resetting a password, or deactivating an account, signs out all of that account's existing sessions
- After 5 failed logins from one device, that device is paused for 10 minutes for that account. Other devices are unaffected, so classmates cannot lock each other out. An account-wide limit (30 failures per hour) still bounds brute force. Resetting the password clears the lock.

### Student Accounts

Student accounts are created automatically when a student is added on the Students page or by bulk upload:

```text
username: student email
initial password: roll number (must be changed at first login)
```

## Bulk Student Import

Columns: `name, roll_no, email, semester`. Principal uploads also need `department` (the label shown in the downloadable template) or `department_id`. HOD and Teacher uploads are automatically scoped to their department.

## QR Attendance Flow

1. The teacher selects department, semester, subject, and duration, and the system creates a `ClassSession`.
2. The teacher's screen shows a QR code that changes every 10 seconds. Each code expires 30 seconds after it is shown, so screenshots and forwarded links stop working almost immediately.
3. The student scans with their own phone. A valid scan gives that phone's browser a 5-minute window to log in (and set a new password on first login) before confirming.
4. The system checks the session is still active, the student belongs to that department and semester, and the phone is the student's registered device.
5. Attendance is stored once per student per session. The IP address the scan came from is stored with it for 30 days (see [Scan IP addresses](#scan-ip-addresses)).

Device rules: a student's first successful attendance registers that phone, and each phone can belong to only one student. Teachers, HODs, and the Principal can reset a student's device lock from the Students page (for example after a phone change).

Sessions deactivated before anyone marked attendance are treated as void (created by mistake) and do not count as classes held. Deactivating after attendance was taken still counts the class.

### Manual Corrections

When a student was in class but could not scan (dead phone, device lock reset), the teacher who ran the session, the department HOD, or the Principal can open the session from Generate QR and mark them present. Entries can also be removed, for example if a student marked attendance without being in the room. Both actions require a short reason. Manual entries show as `P*` in the detailed report and exports, show the staff member and reason in the session view, and are recorded in the activity log.

### Special Classes

The Generate QR page also supports special classes (aptitude, workshops). Teacher selection is optional, and a custom title can be entered.

## QR Scan URL and Network Notes

Scan links use this computer's LAN IP unless overridden:

```powershell
$env:APP_BASE_URL="http://192.168.0.170:5000"   # or APP_HOST_IP / APP_PORT
```

Phones need to be on the same network as the server. When the site is served over HTTPS (recommended, since plain HTTP on shared Wi-Fi can be intercepted), set `SESSION_COOKIE_SECURE=1`. If it runs behind a reverse proxy, also set `TRUSTED_PROXY_COUNT`.

### Before deploying on a public address

On a LAN deployment the QR points to a private address (such as `192.168.x.x`) that only phones on the campus network can open. This is one of the main defences against a student forwarding the QR to an absent friend: the friend at home cannot reach the server, even with a valid link.

If `APP_BASE_URL` or `APP_HOST_IP` points to an address reachable from the internet, that defence is gone. A forwarded QR or link then works from anywhere, and what is left is:

- **Token age** (`QR_TOKEN_MAX_AGE`, 30 seconds). The token is checked only when the link is opened. Opening it starts a `SCAN_GRANT_SECONDS` (5 minute) window to log in and confirm, so the friend only has to open the link in time. A token is also not single-use, so one forwarded link works for every absent student who opens it in time.
- **Device binding.** This stops one phone marking attendance for two students, but an absent friend uses their own phone.
- **The teacher** comparing the live count with the room, and removing entries from the session view.

Shortening `QR_TOKEN_MAX_AGE` narrows the window but does not close it: pasting a link into a group chat takes a few seconds. It also has a floor. A code must stay valid for `QR_REFRESH_SECONDS` plus the time to scan and load on a slow phone, or students in the room get "QR Expired". Something like `QR_REFRESH_SECONDS=5` and `QR_TOKEN_MAX_AGE=15` is about as short as is practical.

To keep network locality on a public deployment, set `SCAN_ALLOWED_NETWORKS` (below).

### Restricting scans to the campus network

`SCAN_ALLOWED_NETWORKS` controls which client addresses may open a scan link. Staff pages, reports and student logins are not affected. Only `/scan/` is checked, because attendance can only be marked after a scan there.

| Value | Scans accepted from |
| --- | --- |
| unset or empty (default) | Private LAN addresses only (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, link-local, IPv6 ULA). Loopback is excluded. |
| `203.0.113.0/24,2001:db8:10::/48` | Only these ranges. Use the campus network's public (outgoing) ranges on a public deployment. |
| `any` | Anywhere. The app logs a warning at startup. |

On a public deployment, set it to the campus ranges. Students then have to be on campus Wi-Fi to scan, not mobile data, just as on a LAN deployment. Anyone else sees "Campus Network Required", and the refusal is written to the server log.

The setting is built to fail closed:

- **Left empty** on a public deployment, it accepts nobody from the internet, so the mistake shows up the first time a student scans, instead of quietly allowing everyone.
- **Malformed** (a typo, `10.0.0.0/33`, a host address with a prefix such as `203.0.113.7/24`, or `any` mixed with ranges): the app refuses to start and names the bad entry. Entries are never skipped.
- **Allow everything** can only be written as `any`. `0.0.0.0/0` and `::/0` are rejected, so it is always a deliberate, searchable choice.
- **Behind a reverse proxy** with `TRUSTED_PROXY_COUNT=0`, every request seems to come from the proxy. Scans carrying `X-Forwarded-For`, `X-Real-IP` or `Forwarded` are then refused until `TRUSTED_PROXY_COUNT` is set. Loopback is not in the default ranges for the same reason.

`run.py` prints the effective ranges at startup. Two things the app cannot check for you:

- With `TRUSTED_PROXY_COUNT` set, the app trusts the proxy's `X-Forwarded-For`. Make sure the app's own port is not reachable directly, or a client could send that header itself.
- A proxy on a private address that adds none of those headers looks like a LAN client to the default ranges. Set explicit ranges on any proxied deployment.

Locality is only as tight as the network. If campus Wi-Fi also covers hostels or the library, a student there can still scan a forwarded code.

### Scan IP addresses

An IP address is personal data, so AttendQR keeps as little of it as it can, for as short a time as it can, and shows it to as few people as it can.

- **What is stored:** for each attendance entry made by QR scan, the IP address the scan link was opened from (`attendance.scan_ip`). On a LAN this is the phone's address on the campus Wi-Fi. On a public deployment it is usually the network's public address, which everyone on the same campus Wi-Fi or mobile carrier shares. Manual entries have none. Nothing else about the device, browser or location is recorded.
- **Why:** so staff can spot a scan that came from a different network than the rest of the class (a forwarded QR), and see that `SCAN_ALLOWED_NETWORKS` is doing its job. Nothing automatic is decided from it.
- **Who can see it:** only the session view, which is open to the teacher who ran the session, the HOD of its department, and the Principal. It is not shown to students, and is not in reports, the detailed report, Excel/PDF exports or the reports API. Students are told on the confirmation page that it is recorded and for how long.
- **How long:** `SCAN_IP_RETENTION_DAYS`, 30 days by default. The app clears older addresses itself, on the first request after startup and then hourly; the attendance entry stays. `flask purge-scan-ips` does the same on demand. `0` stops recording and clears every stored address.

The limit is built in rather than left to a scheduled job, because attendance entries are kept for years while an address stops being useful after a few weeks.

Outside the app, copies remain in database backups until those expire, and web server or proxy access logs record every client's IP address anyway. Give those the same limit. Failed-login throttling also stores an IP address with the username, and those rows are deleted on a later failed login once they are a day old. Mention the scan IP in the institution's privacy notice for students.

## Reporting

- Session-wise attendance reports with pagination
- Detailed student-by-session matrix (uses the same subject and date filters as the report)
- Excel and PDF export
- Student-wise attendance percentage and classes needed to reach 80%
- Shortage list: students below a chosen percentage (default 80%) overall or in any subject, worst first, with the consecutive classes each needs and Excel export

## Testing

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\venv\Scripts\python.exe -m pytest
.\venv\Scripts\python.exe -m ruff check .
```

Each test runs against its own temporary database built by the real migrations; your `instance/` database is never touched. CI runs ruff, a migration/model consistency check, and the tests on Python 3.11–3.13, plus a dependency audit (see [SECURITY.md](SECURITY.md)).

## Documentation

- [docs/RBAC.md](docs/RBAC.md)
- [docs/PROJECT_STRUCTURE.md](docs/PROJECT_STRUCTURE.md)
- [SECURITY.md](SECURITY.md)

## Current Limitations

- SQLite suits a single server; use PostgreSQL or MySQL via `DATABASE_URL` for heavier multi-user traffic
- A student physically outside the classroom could still mark attendance if a classmate sends them a fresh QR within 30 seconds and they can reach the server. On a LAN deployment that means being on the campus network; on a public one it means anywhere, unless `SCAN_ALLOWED_NETWORKS` is set (see [Restricting scans to the campus network](#restricting-scans-to-the-campus-network))
- QR attendance depends on network reachability between the server and student phones

## Suggested Next Improvements

- Bulk semester promotion of students
- Attendance trend analytics

## License

This project is open source and available under the [MIT License](LICENSE).
