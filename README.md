# QR-Based Smart Attendance Management System

A Flask-based web application for managing classroom attendance through time-bound QR sessions, role-based access control, and downloadable attendance reports.

## Overview

This project is designed for college attendance workflows where Principal, HOD, Teacher, and Student users need different levels of access. Teachers generate QR sessions for assigned subjects, students scan the QR code to mark attendance, and the system stores attendance records digitally for reporting and shortage tracking.

The application also manages departments, semesters, subjects, teacher profiles, student records, account governance, password reset flows, and recent account activity logs.

## Core Features

- Role-based access control for Principal, HOD, Teacher, and Student
- Department, semester, subject, teacher, and student management
- Principal/HOD account management from the Accounts page
- Teacher subject assignment with subject-level access control
- Single student creation and bulk student import from the Students page
- Automatic student login creation from student records
- QR-based attendance session generation with expiry handling
- Duplicate attendance prevention per student per session
- Special class session support without mandatory teacher assignment
- Student dashboard with subject-wise percentage and 80% attendance tracking
- Excel and PDF attendance export
- Account status toggle, password reset, and activity logging
- Password change reminder for newly created or reset accounts

## Technology Stack

- Python
- Flask
- Flask-SQLAlchemy
- Flask-Login
- SQLite
- Bootstrap
- qrcode with Pillow
- openpyxl
- reportlab
- pytz

## Project Structure

```text
AttendQR/
  app.py                  Main Flask application and routes
  models.py               SQLAlchemy database models
  config.py               Configuration
  run.py                  Development entrypoint
  wsgi.py                 WSGI entrypoint
  requirements.txt        Python dependencies
  README.md               Project overview and setup guide
  LICENSE                 Open source MIT license
  SECURITY.md             Vulnerability reporting policy
  .env.example            Example environment values
  templates/              Jinja2 templates
  static/                 CSS and generated QR code assets
  instance/               Runtime SQLite database
  docs/                   RBAC and structure notes
  tests/                  Smoke tests
  .github/workflows/      CI test automation
```

## Database

The project uses SQLite through Flask-SQLAlchemy.

- Default database URL: `sqlite:///attendance.db`
- Effective runtime database file: `instance/attendance.db`
- Main tables: `Admin`, `Department`, `Semester`, `Subject`, `Teacher`, `Student`, `ClassSession`, `Attendance`, `ActivityLog`

The application also performs lightweight startup schema updates through helper functions in `app.py` such as `ensure_rbac_columns()` and `ensure_class_session_columns()`.

## Role Summary

### Principal

- Full system access
- Can create Principal, HOD, and Teacher accounts
- Can activate, deactivate, edit, reset, or delete staff accounts
- Can view all account activity logs
- Can manage all departments and academic data

### HOD

- Access limited to the assigned department
- Can create and manage Teacher accounts for that department
- Can manage department-level students, semesters, and subjects
- Can view department teacher activity, not full Principal activity

### Teacher

- Can access only assigned subjects
- Can generate QR sessions only for those subjects
- Can view own attendance sessions and reports
- Can manage student records in the allowed scope
- Cannot see admin activity logs of other users

### Student

- Can log in with student email
- Can view only personal attendance dashboard
- Can track percentage, shortage, and required classes for the 80% target

More detail is available in [docs/RBAC.md](docs/RBAC.md).

## Setup

Create a virtual environment and install dependencies:

```powershell
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
```

Create a local environment file from the example:

```powershell
Copy-Item .env.example .env
```

Update `.env` if you want to change the secret key, database URL, LAN URL settings, or initial Principal account.

## Running the Application

Recommended local run:

```powershell
.\venv\Scripts\python.exe run.py
```

Then open:

```text
http://127.0.0.1:5000
```

### Note About Debug Reloader

If you run the project from a synced folder such as OneDrive, Flask's debug reloader can sometimes behave inconsistently during restart. If that happens, run without the reloader:

```powershell
.\venv\Scripts\python.exe -c "from app import app; app.run(host='0.0.0.0', port=5000, debug=True, use_reloader=False)"
```

Or run without debug mode:

```powershell
.\venv\Scripts\python.exe -c "from app import app; app.run(host='0.0.0.0', port=5000, debug=False)"
```

## Initial Login

The first Principal account is created from these environment variables when the database has no accounts yet:

```text
DEFAULT_PRINCIPAL_EMAIL
DEFAULT_PRINCIPAL_PASSWORD
```

For a local demo, copy `.env.example` to `.env` and use the values defined there. Change the password before sharing or deploying the project.

## Account Rules

### Staff Accounts

- Staff account email must end with `@rcciit.org.in` or `@gmail.com`
- Principal can create Principal, HOD, and Teacher accounts
- HOD can create Teacher accounts for the assigned department
- Only one active HOD is allowed per department
- Newly created or reset accounts are marked with a password-change reminder

### Student Accounts

Student accounts are not created from the Accounts page.

They are created automatically when a student record is added from the Students page.

```text
username: student email
password: roll number
```

Students can also be added through bulk upload.

## Bulk Student Import

Supported fields:

```text
name, roll_no, email, semester
```

Principal uploads should also include department information such as:

```text
department or department_id
```

HOD and Teacher uploads are automatically scoped to their assigned department.

A sample import template can be downloaded from the Students page.

## QR Attendance Flow

1. Teacher selects department, semester, subject, and duration.
2. System creates a `ClassSession` with a unique token.
3. QR image is generated from the scan URL.
4. Student scans the code and submits attendance.
5. System validates session expiry and duplicate attendance.
6. Attendance is stored in the `Attendance` table.

### Special Classes

The Generate QR page also supports special classes such as aptitude or spoken English sessions.

- Teacher selection is optional for special classes
- A custom special class title can be entered

## QR Scan URL and Network Notes

QR links are generated using the laptop's LAN IP unless overridden.

Useful environment values:

```powershell
$env:APP_BASE_URL="http://192.168.0.170:5000"
$env:APP_HOST_IP="192.168.0.170"
$env:APP_PORT="5000"
```

After changing these, restart the app and generate a fresh QR code.

For mobile scanning to work, phones usually need to be on the same Wi-Fi or local network as the laptop running the server.

## Reporting

The project supports:

- session-wise attendance reports
- detailed class attendance view
- Excel export
- PDF export
- student-wise attendance percentage tracking

## Verification

Run the smoke test:

```powershell
.\venv\Scripts\python.exe tests\smoke_test.py
```

This verifies application import and core route access for major roles.

## Documentation

- [docs/RBAC.md](docs/RBAC.md)
- [docs/PROJECT_STRUCTURE.md](docs/PROJECT_STRUCTURE.md)

## Current Limitations

- SQLite is used for simplicity and local deployment, not heavy multi-user production traffic
- Startup schema updates are handled manually in code instead of Flask-Migrate/Alembic
- QR attendance depends on network reachability between the laptop and student devices

## Suggested Next Improvements

- institutional email verification for staff accounts
- stronger attendance authenticity checks such as location or campus network validation
- deployment on a dedicated internal server
- richer analytics for attendance trends and shortage forecasting

## License

This project is open source and available under the [MIT License](LICENSE).
