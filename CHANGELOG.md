# Changelog

Notable changes to AttendQR. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and versions follow [Semantic Versioning](https://semver.org/). Database changes name their migration;
they run by themselves at startup (or with `flask --app wsgi init-db`).

## [Unreleased]

### Fixed

- The activity log no longer cuts long entries at 255 characters. The reason for a correction comes
  last ("... Reason: phone battery died"), so it was the part that got lost. Migration
  `0026_activity_log_text`; existing entries are unchanged and the audit chain still verifies.
- Importing a routine from a broken `.xlsx` file showed a server error; it now says the file could not
  be read. The student import no longer shows the spreadsheet library's technical message for such files.
- Signing out now clears the whole session cookie, not only the sign-in and the scan grant.
- Text contrast now meets WCAG AA (4.5:1) where it did not:
  - the green, amber, red and grey badges in the light theme
  - the search box text in the top bar
  - grey text inside routine blocks (the darker shade meant for it never applied)
  - blue links and today's day in the routine, in the dark theme

### Security

- Uploaded spreadsheets are parsed with `defusedxml`, so XML entity tricks (such as a "billion laughs"
  file) are refused instead of expanded.
- One-time passwords from adding a student or resetting a password are shown on a page of their own,
  as the bulk import already did, instead of in a flashed message: those travel in the session cookie,
  which is signed but readable.

### Changed

- The Docker image runs Python 3.14 (it was 3.13). Its base image is pinned to a digest, and Dependabot
  keeps that digest current.
- Dependencies updated: SQLAlchemy 2.1.9, Alembic 1.20.1, pyotp 2.12.0, ReportLab 5.1.0, click 8.5.1,
  tzdata 2026.6. Development tools: pytest 9.2, pytest-cov 7.1, ruff 0.17.5, pyright 1.1.414,
  psycopg 3.3.9.
- `SCAN_IP_RETENTION_DAYS` is read like the other whole-number settings: a value such as `off` stops
  the app from starting with a clear message.

### Added

- Browser tests (`tests/browser`, Playwright with Chromium): a teacher starts a session, a student
  scans and confirms on a phone-sized screen, and the teacher's list updates by itself. An axe-core
  check of the main pages for each role, in both themes, fails on serious accessibility problems.
  Run them with `python -m pytest -m browser tests/browser`.
- CI: the tests also run on Python 3.14; a job checks that `static/css/app.css` matches a fresh
  Tailwind build (`python scripts/tailwind.py --check`); a job runs the browser tests. The coverage
  floor is now 90% (it was 85%).

## [1.0.0] - 2026-11-18

The state of `main` at `a41969f`, the first version with a changelog.

- QR attendance: a code that changes every 10 seconds, accepted only from the campus network, a scan
  grant tied to the phone that scanned, one phone per student, spot checks and an integrity report.
- Four roles scoped by department (Principal, HOD, Teacher, Student), an HOD who also teaches,
  two-step sign-in for staff, and a hash-chained activity log.
- Leave with a weekly limit, reports and exports, a shortage list, analytics, exam eligibility with
  condonation, and semester locks.
- The class routine: bell schedules, holidays, lab batches, versions, one-date changes, planned
  against held, subject colours, and imports from a spreadsheet.
- Settings in the app, groups and electives, students who change classes, and the MCA demo
  (`flask --app wsgi seed-demo`).
- Docker image, PostgreSQL or SQLite, CI on two databases.

[Unreleased]: https://github.com/Krishnendu-9/AttendQR/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/Krishnendu-9/AttendQR/releases/tag/v1.0.0
