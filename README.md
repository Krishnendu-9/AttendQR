# QR-Based Smart Attendance Management System

A Flask-based attendance management system with QR session generation, role-based access control, student attendance tracking, and Excel/PDF reporting.

## Features

- Principal, HOD, Teacher, and Student role-based access control
- Department, semester, subject, teacher, and student management
- Principal, HOD, and Teacher login creation from the Accounts page
- Single and bulk student creation from the Students page
- Dynamic QR code generation for class attendance
- QR expiry and duplicate attendance prevention
- Student dashboard with subject-wise attendance percentage
- 80% attendance target and classes-needed calculation
- Department-scoped HOD access
- Teacher-scoped QR sessions and reports
- Excel and PDF attendance exports

## Tech Stack

- Python
- Flask
- Flask-SQLAlchemy
- Flask-Login
- SQLite
- Bootstrap
- qrcode / Pillow
- openpyxl
- reportlab

## Project Structure

```text
minor_project/
  app.py                  Main Flask application and routes
  models.py               Database models
  config.py               Configuration
  run.py                  Local development entrypoint
  wsgi.py                 WSGI entrypoint
  requirements.txt        Python dependencies
  .env.example            Environment variable template
  templates/              Jinja2 templates
  static/                 CSS and generated QR folder
  instance/               Runtime SQLite database
  docs/                   Project documentation
  tests/                  Smoke tests
```

## Setup

Create and activate a virtual environment:

```powershell
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
```

Run the application:

```powershell
.\venv\Scripts\python.exe run.py
```

Open:

```text
http://127.0.0.1:5000
```

## Default Login

```text
username: admin
password: admin123
role: Principal
```

Student accounts are auto-created from student records:

```text
username: student email
password: roll number
```

Student logins are not manually created from the Accounts page. Add students from the Students page, or import a CSV/XLSX class list, and the system creates their login automatically.

Teacher profiles and teacher login accounts are created from the Accounts page. The Teachers page is used for teacher profile details and search.

Bulk upload columns:

```text
name, roll_no, email, semester
```

Principal uploads should also include `department` or `department_id`. HOD and Teacher uploads are automatically scoped to their assigned department.

A sample CSV can be downloaded from the Students page with the Template button.

## QR Code Notes

QR scan links are generated with the active LAN IP of the laptop running the server. Phones must be able to reach that IP, usually by being on the same Wi-Fi/LAN.

Optional override:

```powershell
$env:APP_BASE_URL="http://192.168.0.170:5000"
```

Then restart the app and generate a fresh QR code.

## Verification

Run the smoke test:

```powershell
.\venv\Scripts\python.exe tests\smoke_test.py
```

This checks app import, route access for major roles, and key pages.
