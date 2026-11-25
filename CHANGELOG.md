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
- A failure while saving a student import could leave the students saved without the activity log
  entry for their import, after saying that none were added. Both are now saved together.
- Deleting a student erased all their attendance without asking why. A student with attendance is now
  deleted from their own page, with a reason that is logged with the number of records erased.
- A student of a locked semester could be deleted, erasing part of its final records. A student of a
  locked semester, now or before a move, can no longer be deleted.
- Leave decisions checked only the lock of the student's current semester, so approving or reopening
  leave for the last weeks of a semester they had just been promoted out of changed its locked
  records. Every semester the leave days fall in is now checked.
- Changing a student's email moved their login without telling anyone. The page now says the student
  signs in with the new address, the activity log records the change, and failed sign-ins counted
  under the old address are cleared.
- Promoting a class after its semester was locked (the usual order: exams, lock, promotion) removed
  every promoted student from the locked semester's exam eligibility and analytics. A locked semester
  now keeps everyone who was in it when it was locked.
- A double click on Generate, or the browser sending the form again, started a second session for
  the same class, and everyone who scanned only one was absent in the other. Each form now starts one
  session, and a second submission opens it. Migration `0028_session_create_token`.
- Starting an extra class from the routine twice at the same moment created two sessions, and the
  second took the class from the first. It now gives one session.
- Changing the time zone in Settings moved approved leave by hours, which could change past
  percentages. Approval now fixes the leave's window, and leave approved earlier gets the window it has
  now. Migration `0030_leave_window`.
- Cancelled classes in My routine and Today's classes were faded, which took their grey text below
  WCAG AA contrast. They are now crossed out and badged at full contrast, with the darker grey on the
  grey block in the week grid. The browser tests check My routine on the week of the demo's cancelled
  class, so the check no longer passes or fails with the day of the week it runs.

### Security

- Uploaded spreadsheets are parsed with `defusedxml`, so XML entity tricks (such as a "billion laughs"
  file) are refused instead of expanded.
- One-time passwords from adding a student or resetting a password are shown on a page of their own,
  as the bulk import already did, instead of in a flashed message: those travel in the session cookie,
  which is signed but readable.
- A failed sign-in took about 100 ms less for an account still on its one-time password than for an
  unknown email, which showed which accounts exist and have never signed in. Every attempt now checks
  one hash at each cost, so the time is the same.
- The database enforces what the app already checks: CHECK constraints for roles, attendance sources,
  statuses, routine kinds and week patterns, weekdays, and dates and times that end after they start,
  and group names unique within a semester ignoring case. Login names and emails from older versions
  are stored lower case (roll numbers upper case), unless that would clash, so sign-in compares them
  directly. Migration `0027_data_constraints`, which stops with a list of rows to correct if any break
  a constraint.
- `requirements.lock` carries the hashes of every published file of each pin, so pip refuses a
  download that differs; the Docker image installs with `--require-hashes`, and CI checks every pin
  has hashes (`python scripts/lock_hashes.py --check`).
- CI jobs get a read-only token: they cannot push, comment or change the repository's settings.

### Changed

- The Docker image runs Python 3.14 (it was 3.13). Its base image is pinned to a digest, and Dependabot
  keeps that digest current.
- Dependencies updated: SQLAlchemy 2.1.9, Alembic 1.20.1, pyotp 2.12.0, ReportLab 5.1.0, click 8.5.1,
  tzdata 2026.6. Development tools: pytest 9.2, pytest-cov 7.1, ruff 0.17.5, pyright 1.1.414,
  psycopg 3.3.9.
- `SCAN_IP_RETENTION_DAYS` is read like the other whole-number settings: a value such as `off` stops
  the app from starting with a clear message.
- A guard on every database write refuses any change to a locked semester's attendance, sessions,
  spot checks, leave, students or counted subjects, so a page that forgot its own check cannot change
  them.
- Students can no longer be added to, or moved into, a locked semester. The Students page and the
  import say why.
- Starting a session from the Generate form and from the routine share one service
  (`services/sessions.py`).
- The attendance routes are split into the teacher's (`sessions.py`) and the student's (`scan.py`);
  URLs and endpoint names are unchanged.
- The routine routes are split by area (schedules, drafts, views, changes); URLs and endpoint names
  are unchanged.
- The PostgreSQL driver (`psycopg`) is a regular dependency, in the Docker image too.
- `compose.yaml` has an optional PostgreSQL service (`docker compose --profile postgres up`).

### Added

- Browser tests (`tests/browser`, Playwright with Chromium): a teacher starts a session, a student
  scans and confirms on a phone-sized screen, and the teacher's list updates by itself. An axe-core
  check of the main pages for each role, in both themes, fails on serious accessibility problems.
  Run them with `python -m pytest -m browser tests/browser`.
- CI: the tests also run on Python 3.14; a job checks that `static/css/app.css` matches a fresh
  Tailwind build (`python scripts/tailwind.py --check`); a job runs the browser tests. The coverage
  floor is now 90% (it was 85%).
- **Mark as left** on a student's page, with a reason, for a student who leaves the college. Their
  records stay, their time in the class ends that day, and they leave the Students list, the shortage
  list and exam eligibility; their login is turned off. **Restore** brings them back from that
  moment. Migration `0029_student_left`.
- A filter on the Students list for current students (the default), those who have left, or
  everyone.

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
