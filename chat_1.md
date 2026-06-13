venv\Scripts\activate
python app.py


python -c "import sqlite3; conn = sqlite3.connect('instance/attendance.db'); conn.execute('DELETE FROM attendance'); conn.execute('DELETE FROM class_session'); conn.commit(); conn.close(); print('Done')"



Agentic coding - OFF

---

Project Overview

The system generates a unique QR code for each class session. Students scan the QR code using their mobile phones and their attendance is automatically recorded in the database.

Features
Admin Login
Student Registration
QR Code Generation
QR Code Scanning
Attendance Recording
Attendance Reports
Export Attendance to Excel/PDF
Dashboard
Technology Stack
Component	Technology
Frontend	HTML, CSS, Bootstrap
Backend	Python (Flask)
Database	MySQL / SQLite
QR Code	qrcode library
Scanner	Mobile Camera (html5-qrcode)
ORM	SQLAlchemy

---

#### Allow read-only tools outside your workspace

---


The project folder is empty. Here's the minimal structure and implementation plan for your QR-based attendance system:

## Project Structure

```
minor_project/
├── app.py
├── models.py
├── config.py
├── requirements.txt
├── templates/
│   ├── base.html
│   ├── login.html
│   ├── dashboard.html
│   ├── students.html
│   ├── generate_qr.html
│   ├── scan_qr.html
│   └── reports.html
└── static/
    └── qrcodes/
```

## Key Files

**requirements.txt**
```
Flask
Flask-SQLAlchemy
Flask-Login
qrcode[pil]
openpyxl
reportlab
Pillow
```

**config.py**
```python
import os

class Config:
    SECRET_KEY = os.urandom(24)
    SQLALCHEMY_DATABASE_URI = 'sqlite:///attendance.db'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    QR_CODE_DIR = 'static/qrcodes'
```

**models.py**
```python
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime

db = SQLAlchemy()

class Admin(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)

class Student(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    roll_no = db.Column(db.String(20), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)

class ClassSession(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    subject = db.Column(db.String(100), nullable=False)
    token = db.Column(db.String(100), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=False)

class Attendance(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id'), nullable=False)
    session_id = db.Column(db.Integer, db.ForeignKey('class_session.id'), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    student = db.relationship('Student', backref='attendances')
    session = db.relationship('ClassSession', backref='attendances')
```

**app.py**
```python
from flask import Flask, render_template, redirect, url_for, request, jsonify, send_file
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from models import db, Admin, Student, ClassSession, Attendance
from config import Config
from datetime import datetime, timedelta
import qrcode, uuid, os, io
import openpyxl
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Table

app = Flask(__name__)
app.config.from_object(Config)
db.init_app(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

@login_manager.user_loader
def load_user(user_id):
    return Admin.query.get(int(user_id))

@app.route('/')
@login_required
def dashboard():
    return render_template('dashboard.html',
        students=Student.query.count(),
        sessions=ClassSession.query.count(),
        attendance=Attendance.query.count())

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        admin = Admin.query.filter_by(username=request.form['username']).first()
        if admin and check_password_hash(admin.password, request.form['password']):
            login_user(admin)
            return redirect(url_for('dashboard'))
    return render_template('login.html')

@app.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/students', methods=['GET', 'POST'])
@login_required
def students():
    if request.method == 'POST':
        s = Student(name=request.form['name'], roll_no=request.form['roll_no'], email=request.form['email'])
        db.session.add(s)
        db.session.commit()
    return render_template('students.html', students=Student.query.all())

@app.route('/generate_qr', methods=['GET', 'POST'])
@login_required
def generate_qr():
    qr_image = None
    session_id = None
    if request.method == 'POST':
        token = str(uuid.uuid4())
        cs = ClassSession(
            subject=request.form['subject'],
            token=token,
            expires_at=datetime.utcnow() + timedelta(minutes=int(request.form.get('duration', 30)))
        )
        db.session.add(cs)
        db.session.commit()
        scan_url = request.host_url + f'scan/{token}'
        img = qrcode.make(scan_url)
        path = f"static/qrcodes/{token}.png"
        img.save(path)
        qr_image = path
        session_id = cs.id
    return render_template('generate_qr.html', qr_image=qr_image, session_id=session_id)

@app.route('/scan/<token>')
def scan_qr(token):
    return render_template('scan_qr.html', token=token)

@app.route('/mark_attendance', methods=['POST'])
def mark_attendance():
    data = request.json
    cs = ClassSession.query.filter_by(token=data['token']).first()
    if not cs or datetime.utcnow() > cs.expires_at:
        return jsonify({'success': False, 'message': 'QR code expired or invalid'})
    student = Student.query.filter_by(roll_no=data['roll_no']).first()
    if not student:
        return jsonify({'success': False, 'message': 'Student not found'})
    exists = Attendance.query.filter_by(student_id=student.id, session_id=cs.id).first()
    if exists:
        return jsonify({'success': False, 'message': 'Attendance already marked'})
    db.session.add(Attendance(student_id=student.id, session_id=cs.id))
    db.session.commit()
    return jsonify({'success': True, 'message': f'Attendance marked for {student.name}'})

@app.route('/reports')
@login_required
def reports():
    sessions = ClassSession.query.order_by(ClassSession.created_at.desc()).all()
    selected = request.args.get('session_id', type=int)
    records = []
    if selected:
        records = Attendance.query.filter_by(session_id=selected).all()
    return render_template('reports.html', sessions=sessions, records=records, selected=selected)

@app.route('/export/<int:session_id>/<fmt>')
@login_required
def export(session_id, fmt):
    records = Attendance.query.filter_by(session_id=session_id).all()
    cs = ClassSession.query.get(session_id)
    data = [['Name', 'Roll No', 'Email', 'Time']] + \
           [[r.student.name, r.student.roll_no, r.student.email, r.timestamp.strftime('%Y-%m-%d %H:%M')] for r in records]
    if fmt == 'excel':
        wb = openpyxl.Workbook()
        ws = wb.active
        for row in data: ws.append(row)
        buf = io.BytesIO()
        wb.save(buf); buf.seek(0)
        return send_file(buf, download_name=f'{cs.subject}_attendance.xlsx', as_attachment=True)
    elif fmt == 'pdf':
        buf = io.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=letter)
        doc.build([Table(data)])
        buf.seek(0)
        return send_file(buf, download_name=f'{cs.subject}_attendance.pdf', as_attachment=True)

with app.app_context():
    db.create_all()
    if not Admin.query.first():
        db.session.add(Admin(username='admin', password=generate_password_hash('admin123')))
        db.session.commit()

if __name__ == '__main__':
    os.makedirs('static/qrcodes', exist_ok=True)
    app.run(debug=True)
```

---

## Summary of How It Works

1. Admin logs in → default creds: `admin` / `admin123`
2. Register students with name, roll number, email
3. Generate QR for a class session (has expiry time) → QR encodes a unique URL
4. Students scan QR with phone → opens scan page → enter roll number → attendance marked via API
5. Admin views reports filtered by session, exports to Excel/PDF

---

Do you want me to generate the HTML templates next, or would you like me to start with a specific part like the scan page or dashboard?

---

step by step how to implements

---

#### Allow read-only tools outside your workspace

---


# Step-by-Step Implementation Guide

---

## Step 1: Setup Project Folder & Virtual Environment

Open terminal in `e:\minor_project` and run:

```bash
python -m venv venv
venv\Scripts\activate
```

---

## Step 2: Install Dependencies

Create `requirements.txt`:

```
Flask
Flask-SQLAlchemy
Flask-Login
qrcode[pil]
openpyxl
reportlab
Pillow
```

Then install:

```bash
pip install -r requirements.txt
```

---

## Step 3: Create Folder Structure

```bash
mkdir templates static static\qrcodes
```

Final structure:
```
minor_project/
├── app.py
├── models.py
├── config.py
├── requirements.txt
├── templates/
│   ├── base.html
│   ├── login.html
│   ├── dashboard.html
│   ├── students.html
│   ├── generate_qr.html
│   ├── scan_qr.html
│   └── reports.html
└── static/
    └── qrcodes/
```

---

## Step 4: Create `config.py`

```python
import os

class Config:
    SECRET_KEY = os.urandom(24)
    SQLALCHEMY_DATABASE_URI = 'sqlite:///attendance.db'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
```

---

## Step 5: Create `models.py`

```python
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime

db = SQLAlchemy()

class Admin(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)

class Student(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    roll_no = db.Column(db.String(20), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)

class ClassSession(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    subject = db.Column(db.String(100), nullable=False)
    token = db.Column(db.String(100), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=False)

class Attendance(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id'), nullable=False)
    session_id = db.Column(db.Integer, db.ForeignKey('class_session.id'), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    student = db.relationship('Student', backref='attendances')
    session = db.relationship('ClassSession', backref='attendances')
```

---

## Step 6: Create `app.py`

```python
from flask import Flask, render_template, redirect, url_for, request, jsonify, send_file
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from models import db, Admin, Student, ClassSession, Attendance
from config import Config
from datetime import datetime, timedelta
import qrcode, uuid, os, io
import openpyxl
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Table

app = Flask(__name__)
app.config.from_object(Config)
db.init_app(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

@login_manager.user_loader
def load_user(user_id):
    return Admin.query.get(int(user_id))

@app.route('/')
@login_required
def dashboard():
    return render_template('dashboard.html',
        students=Student.query.count(),
        sessions=ClassSession.query.count(),
        attendance=Attendance.query.count())

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        admin = Admin.query.filter_by(username=request.form['username']).first()
        if admin and check_password_hash(admin.password, request.form['password']):
            login_user(admin)
            return redirect(url_for('dashboard'))
    return render_template('login.html')

@app.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/students', methods=['GET', 'POST'])
@login_required
def students():
    if request.method == 'POST':
        s = Student(name=request.form['name'], roll_no=request.form['roll_no'], email=request.form['email'])
        db.session.add(s)
        db.session.commit()
    return render_template('students.html', students=Student.query.all())

@app.route('/generate_qr', methods=['GET', 'POST'])
@login_required
def generate_qr():
    qr_image = None
    session_id = None
    if request.method == 'POST':
        token = str(uuid.uuid4())
        cs = ClassSession(
            subject=request.form['subject'],
            token=token,
            expires_at=datetime.utcnow() + timedelta(minutes=int(request.form.get('duration', 30)))
        )
        db.session.add(cs)
        db.session.commit()
        scan_url = request.host_url + f'scan/{token}'
        img = qrcode.make(scan_url)
        path = f"static/qrcodes/{token}.png"
        img.save(path)
        qr_image = path
        session_id = cs.id
    return render_template('generate_qr.html', qr_image=qr_image, session_id=session_id)

@app.route('/scan/<token>')
def scan_qr(token):
    return render_template('scan_qr.html', token=token)

@app.route('/mark_attendance', methods=['POST'])
def mark_attendance():
    data = request.json
    cs = ClassSession.query.filter_by(token=data['token']).first()
    if not cs or datetime.utcnow() > cs.expires_at:
        return jsonify({'success': False, 'message': 'QR code expired or invalid'})
    student = Student.query.filter_by(roll_no=data['roll_no']).first()
    if not student:
        return jsonify({'success': False, 'message': 'Student not found'})
    exists = Attendance.query.filter_by(student_id=student.id, session_id=cs.id).first()
    if exists:
        return jsonify({'success': False, 'message': 'Attendance already marked'})
    db.session.add(Attendance(student_id=student.id, session_id=cs.id))
    db.session.commit()
    return jsonify({'success': True, 'message': f'Attendance marked for {student.name}'})

@app.route('/reports')
@login_required
def reports():
    sessions = ClassSession.query.order_by(ClassSession.created_at.desc()).all()
    selected = request.args.get('session_id', type=int)
    records = []
    if selected:
        records = Attendance.query.filter_by(session_id=selected).all()
    return render_template('reports.html', sessions=sessions, records=records, selected=selected)

@app.route('/export/<int:session_id>/<fmt>')
@login_required
def export(session_id, fmt):
    records = Attendance.query.filter_by(session_id=session_id).all()
    cs = ClassSession.query.get(session_id)
    data = [['Name', 'Roll No', 'Email', 'Time']] + \
           [[r.student.name, r.student.roll_no, r.student.email, r.timestamp.strftime('%Y-%m-%d %H:%M')] for r in records]
    if fmt == 'excel':
        wb = openpyxl.Workbook()
        ws = wb.active
        for row in data: ws.append(row)
        buf = io.BytesIO()
        wb.save(buf); buf.seek(0)
        return send_file(buf, download_name=f'{cs.subject}_attendance.xlsx', as_attachment=True)
    elif fmt == 'pdf':
        buf = io.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=letter)
        doc.build([Table(data)])
        buf.seek(0)
        return send_file(buf, download_name=f'{cs.subject}_attendance.pdf', as_attachment=True)

with app.app_context():
    db.create_all()
    if not Admin.query.first():
        db.session.add(Admin(username='admin', password=generate_password_hash('admin123')))
        db.session.commit()

if __name__ == '__main__':
    os.makedirs('static/qrcodes', exist_ok=True)
    app.run(debug=True)
```

---

## Step 7: Create Templates

**`templates/base.html`**
```html
<!DOCTYPE html>
<html>
<head>
  <title>Attendance System</title>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css">
</head>
<body>
<nav class="navbar navbar-dark bg-dark px-3">
  <a class="navbar-brand" href="/">QR Attendance</a>
  <div>
    <a class="text-white me-3" href="/students">Students</a>
    <a class="text-white me-3" href="/generate_qr">Generate QR</a>
    <a class="text-white me-3" href="/reports">Reports</a>
    <a class="text-white" href="/logout">Logout</a>
  </div>
</nav>
<div class="container mt-4">{% block content %}{% endblock %}</div>
</body>
</html>
```

**`templates/login.html`**
```html
<!DOCTYPE html>
<html>
<head>
  <title>Login</title>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css">
</head>
<body class="d-flex justify-content-center align-items-center vh-100 bg-light">
<div class="card p-4" style="width:350px">
  <h4 class="mb-3">Admin Login</h4>
  <form method="POST">
    <input class="form-control mb-2" name="username" placeholder="Username" required>
    <input class="form-control mb-3" name="password" type="password" placeholder="Password" required>
    <button class="btn btn-primary w-100">Login</button>
  </form>
</div>
</body>
</html>
```

**`templates/dashboard.html`**
```html
{% extends 'base.html' %}
{% block content %}
<h3>Dashboard</h3>
<div class="row mt-3">
  <div class="col-md-4"><div class="card text-center p-3"><h5>Students</h5><h2>{{ students }}</h2></div></div>
  <div class="col-md-4"><div class="card text-center p-3"><h5>Sessions</h5><h2>{{ sessions }}</h2></div></div>
  <div class="col-md-4"><div class="card text-center p-3"><h5>Attendance Records</h5><h2>{{ attendance }}</h2></div></div>
</div>
{% endblock %}
```

**`templates/students.html`**
```html
{% extends 'base.html' %}
{% block content %}
<h3>Students</h3>
<form method="POST" class="row g-2 mb-4">
  <div class="col"><input class="form-control" name="name" placeholder="Name" required></div>
  <div class="col"><input class="form-control" name="roll_no" placeholder="Roll No" required></div>
  <div class="col"><input class="form-control" name="email" placeholder="Email" required></div>
  <div class="col-auto"><button class="btn btn-success">Add Student</button></div>
</form>
<table class="table table-bordered">
  <thead><tr><th>Name</th><th>Roll No</th><th>Email</th></tr></thead>
  <tbody>
    {% for s in students %}
    <tr><td>{{ s.name }}</td><td>{{ s.roll_no }}</td><td>{{ s.email }}</td></tr>
    {% endfor %}
  </tbody>
</table>
{% endblock %}
```

**`templates/generate_qr.html`**
```html
{% extends 'base.html' %}
{% block content %}
<h3>Generate QR Code</h3>
<form method="POST" class="row g-2 mb-4">
  <div class="col"><input class="form-control" name="subject" placeholder="Subject" required></div>
  <div class="col"><input class="form-control" name="duration" type="number" placeholder="Duration (mins)" value="30"></div>
  <div class="col-auto"><button class="btn btn-primary">Generate</button></div>
</form>
{% if qr_image %}
  <div class="text-center">
    <h5>Scan this QR Code</h5>
    <img src="/{{ qr_image }}" width="250">
    <div class="mt-2">
      <a href="/export/{{ session_id }}/excel" class="btn btn-success btn-sm">Export Excel</a>
      <a href="/export/{{ session_id }}/pdf" class="btn btn-danger btn-sm">Export PDF</a>
    </div>
  </div>
{% endif %}
{% endblock %}
```

**`templates/scan_qr.html`**
```html
<!DOCTYPE html>
<html>
<head>
  <title>Mark Attendance</title>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css">
</head>
<body class="d-flex justify-content-center align-items-center vh-100 bg-light">
<div class="card p-4" style="width:350px">
  <h5 class="mb-3">Mark Your Attendance</h5>
  <input class="form-control mb-3" id="roll_no" placeholder="Enter Roll Number">
  <button class="btn btn-primary w-100" onclick="markAttendance()">Submit</button>
  <div id="msg" class="mt-3"></div>
</div>
<script>
function markAttendance() {
  const roll_no = document.getElementById('roll_no').value;
  fetch('/mark_attendance', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({token: '{{ token }}', roll_no: roll_no})
  })
  .then(r => r.json())
  .then(data => {
    document.getElementById('msg').innerHTML =
      `<div class="alert alert-${data.success ? 'success' : 'danger'}">${data.message}</div>`;
  });
}
</script>
</body>
</html>
```

**`templates/reports.html`**
```html
{% extends 'base.html' %}
{% block content %}
<h3>Attendance Reports</h3>
<form method="GET" class="row g-2 mb-4">
  <div class="col">
    <select class="form-select" name="session_id">
      <option value="">-- Select Session --</option>
      {% for s in sessions %}
      <option value="{{ s.id }}" {% if selected == s.id %}selected{% endif %}>
        {{ s.subject }} - {{ s.created_at.strftime('%Y-%m-%d %H:%M') }}
      </option>
      {% endfor %}
    </select>
  </div>
  <div class="col-auto"><button class="btn btn-primary">View</button></div>
  {% if selected %}
  <div class="col-auto">
    <a href="/export/{{ selected }}/excel" class="btn btn-success">Excel</a>
    <a href="/export/{{ selected }}/pdf" class="btn btn-danger">PDF</a>
  </div>
  {% endif %}
</form>
{% if records %}
<table class="table table-bordered">
  <thead><tr><th>Name</th><th>Roll No</th><th>Email</th><th>Time</th></tr></thead>
  <tbody>
    {% for r in records %}
    <tr><td>{{ r.student.name }}</td><td>{{ r.student.roll_no }}</td><td>{{ r.student.email }}</td><td>{{ r.timestamp.strftime('%Y-%m-%d %H:%M') }}</td></tr>
    {% endfor %}
  </tbody>
</table>
{% endif %}
{% endblock %}
```

---

## Step 8: Run the App

```bash
python app.py
```

Visit `http://127.0.0.1:5000` — login with `admin` / `admin123`

---

## Step 9: Test the Full Flow

| Step | Action |
|------|--------|
| 1 | Login as admin |
| 2 | Go to Students → add students |
| 3 | Go to Generate QR → enter subject & duration → generate |
| 4 | Open generated QR on phone (must be on same WiFi) using `http://<your-pc-ip>:5000` |
| 5 | Student enters roll number → attendance marked |
| 6 | Admin views Reports → export Excel/PDF |

> For phone scanning to work, run Flask with `app.run(host='0.0.0.0', debug=True)` and use your PC's local IP (e.g. `192.168.1.x`) instead of `localhost`

---

Want me to help with any specific step or add features like bulk student import via CSV?

---

venv\Scripts\activate : File 
E:\minor_project\venv\Scripts\Activate.ps1 cannot be loaded 
because running scripts is disabled on this system. For more 
information, see about_Execution_Policies at 
https:/go.microsoft.com/fwlink/?LinkID=135170.
At line:1 char:1
+ venv\Scripts\activate
+ ~~~~~~~~~~~~~~~~~~~~~
    + CategoryInfo          : SecurityError: (:) [], PSSecurityEx 
   ception
    + FullyQualifiedErrorId : UnauthorizedAccess

---


This is a Windows PowerShell execution policy restriction. Fix it with one of these options:

## Option 1: Change Execution Policy (Recommended)

Run this in PowerShell as Administrator:

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

Then activate normally:

```powershell
venv\Scripts\activate
```

---

## Option 2: Use Command Prompt (cmd) Instead

Switch from PowerShell to cmd and run:

```cmd
venv\Scripts\activate.bat
```

---

## Option 3: Bypass Policy for Current Session Only

```powershell
powershell -ExecutionPolicy Bypass -File venv\Scripts\Activate.ps1
```

---

## Option 4: Use Python directly without activating

If none of the above work, just use the venv's Python directly:

```cmd
venv\Scripts\python.exe app.py
venv\Scripts\pip.exe install -r requirements.txt
```

---

**Recommended:** Go with Option 1 or Option 2 (cmd). Option 2 is the quickest — just open `cmd` instead of PowerShell and run `venv\Scripts\activate.bat`.

---

how to scan on phone step by step guide

---


## Scanning QR Code on Phone - Step by Step

---

## Step 1: Find Your PC's Local IP Address

Open **cmd** and run:
```cmd
ipconfig
```
Look for **IPv4 Address** under your WiFi adapter:
```
IPv4 Address. . . . . . . . : 192.168.1.5  ← your IP
```

---

## Step 2: Update `app.py` to Allow Network Access

Change the last line in `app.py` from:
```python
app.run(debug=True)
```
to:
```python
app.run(host='0.0.0.0', port=5000, debug=True)
```

---

## Step 3: Allow Flask Through Windows Firewall

Run in **cmd as Administrator**:
```cmd
netsh advfirewall firewall add rule name="Flask App" dir=in action=allow protocol=TCP localport=5000
```

---

## Step 4: Start the Flask App

```cmd
venv\Scripts\activate.bat
python app.py
```

You should see:
```
Running on http://0.0.0.0:5000
Running on http://192.168.1.5:5000  ← this is your network URL
```

---

## Step 5: Connect Phone to Same WiFi

- Go to phone **Settings → WiFi**
- Connect to the **same WiFi network** as your PC

---

## Step 6: Test Access from Phone Browser

On your phone browser, type:
```
http://192.168.1.5:5000
```
(replace with your actual IP from Step 1)

You should see the login page — this confirms phone can reach your PC.

---

## Step 7: Generate QR Code from Admin Panel

1. Login at `http://192.168.1.5:5000`
2. Go to **Generate QR**
3. Enter subject name and duration
4. Click **Generate**
5. A QR code image will appear on screen

---

## Step 8: Student Scans QR Code

| Action | Detail |
|--------|--------|
| Open phone camera | Point at QR code on PC screen |
| Tap the link | Phone browser opens automatically |
| Enter Roll Number | Type roll number in the input field |
| Click Submit | Attendance gets marked instantly |

---

## Step 9: Verify Attendance

On admin panel:
1. Go to **Reports**
2. Select the session from dropdown
3. Click **View** — you'll see the student's record

---

## Common Issues & Fixes

| Problem | Fix |
|---------|-----|
| Phone can't open the URL | Make sure both devices are on same WiFi |
| QR code link shows `127.0.0.1` | Regenerate QR **after** setting `host='0.0.0.0'` |
| Firewall blocking | Run the firewall command in Step 3 |
| Camera doesn't scan | Use phone's default camera or Google Lens |
| Page not loading on phone | Disable mobile data, use WiFi only |

---

> The QR code URL is auto-generated as `http://<your-ip>:5000/scan/<token>` — so as long as Flask is running with `host='0.0.0.0'`, the phone scan will work directly.

---

not working phone scan

---

Let me check your current `app.py` to diagnose the issue.

---


Found the issue. The QR code URL is being generated using `request.host_url` which gives `127.0.0.1` — so the phone gets a QR code pointing to localhost instead of your PC's IP.

Fix this in `app.py` — replace the `generate_qr` route's URL generation:

**Change this line:**
```python
scan_url = request.host_url + f'scan/{token}'
```

**To this:**
```python
import socket
local_ip = socket.gethostbyname(socket.gethostname())
scan_url = f'http://{local_ip}:5000/scan/{token}'
```

Full updated `generate_qr` route:

```python
@app.route('/generate_qr', methods=['GET', 'POST'])
@login_required
def generate_qr():
    qr_image = None
    session_id = None
    if request.method == 'POST':
        token = str(uuid.uuid4())
        cs = ClassSession(
            subject=request.form['subject'],
            token=token,
            expires_at=datetime.utcnow() + timedelta(minutes=int(request.form.get('duration', 30)))
        )
        db.session.add(cs)
        db.session.commit()
        local_ip = socket.gethostbyname(socket.gethostname())
        scan_url = f'http://{local_ip}:5000/scan/{token}'
        img = qrcode.make(scan_url)
        path = f"static/qrcodes/{token}.png"
        img.save(path)
        qr_image = path
        session_id = cs.id
    return render_template('generate_qr.html', qr_image=qr_image, session_id=session_id)
```

Also add `import socket` at the top of `app.py` with the other imports.

---

Then:
1. Save `app.py`
2. Restart Flask: `python app.py`
3. Generate a **new** QR code (old ones still point to `127.0.0.1`)
4. Scan the new QR — it will now point to your PC's real IP

---

**Still not working after this?** Tell me what exactly happens:
- Phone camera doesn't detect QR?
- QR scans but page doesn't load?
- Page loads but attendance submission fails?

---

where to past import socket
local_ip = socket.gethostbyname(socket.gethostname())
scan_url = f&#39;http://{local_ip}:5000/scan/{token}&#39;

---


Open `app.py` and look at the top imports section:

**Line 7 — add `socket` here:**
```python
import qrcode, uuid, os, io, socket
```

That's it. Just add `, socket` to the existing import line. No new line needed.

---

Then find this line (around line 63):
```python
scan_url = request.host_url + f'scan/{token}'
```

Replace it with:
```python
local_ip = socket.gethostbyname(socket.gethostname())
scan_url = f'http://{local_ip}:5000/scan/{token}'
```

---

So your final top of file looks like:
```python
import qrcode, uuid, os, io, socket   # ← socket added here
```

And inside `generate_qr` route:
```python
local_ip = socket.gethostbyname(socket.gethostname())
scan_url = f'http://{local_ip}:5000/scan/{token}'   # ← replaces old line
img = qrcode.make(scan_url)
```

Save → restart Flask → generate a new QR code → scan from phone.

---

give upgraded full code generate_qr

---


Here's the upgraded `generate_qr.html` with a better UI, QR display, scan URL shown, and a print/download button:

```html
{% extends 'base.html' %}
{% block content %}
<h3>Generate QR Code</h3>

<form method="POST" class="row g-2 mb-4">
  <div class="col-md-4">
    <input class="form-control" name="subject" placeholder="Subject Name" required>
  </div>
  <div class="col-md-3">
    <input class="form-control" name="duration" type="number" placeholder="Duration (mins)" value="30" min="1" required>
  </div>
  <div class="col-auto">
    <button class="btn btn-primary">Generate QR</button>
  </div>
</form>

{% if qr_image %}
<div class="card shadow p-4 text-center mx-auto" style="max-width:420px">
  <h5 class="mb-1">{{ subject }}</h5>
  <p class="text-muted mb-3">Expires in {{ duration }} minutes</p>

  <div class="border rounded p-3 bg-white d-inline-block mb-3">
    <img src="/{{ qr_image }}" width="280" id="qrImg">
  </div>

  <div class="mb-3">
    <small class="text-muted">Scan URL:</small><br>
    <code class="text-break">{{ scan_url }}</code>
  </div>

  <div class="d-flex justify-content-center gap-2 flex-wrap">
    <button class="btn btn-secondary btn-sm" onclick="printQR()">🖨 Print QR</button>
    <a href="/{{ qr_image }}" download class="btn btn-info btn-sm">⬇ Download QR</a>
    <a href="/export/{{ session_id }}/excel" class="btn btn-success btn-sm">📊 Export Excel</a>
    <a href="/export/{{ session_id }}/pdf" class="btn btn-danger btn-sm">📄 Export PDF</a>
  </div>
</div>

<script>
function printQR() {
  const win = window.open('');
  win.document.write('<img src="/{{ qr_image }}" style="width:100%">');
  win.document.close();
  win.print();
}
</script>
{% endif %}
{% endblock %}
```

Now update `generate_qr` route in `app.py` to pass `subject`, `duration`, and `scan_url` to the template:

```python
@app.route('/generate_qr', methods=['GET', 'POST'])
@login_required
def generate_qr():
    qr_image = None
    session_id = None
    scan_url = None
    subject = None
    duration = None
    if request.method == 'POST':
        token = str(uuid.uuid4())
        subject = request.form['subject']
        duration = int(request.form.get('duration', 30))
        cs = ClassSession(
            subject=subject,
            token=token,
            expires_at=datetime.utcnow() + timedelta(minutes=duration)
        )
        db.session.add(cs)
        db.session.commit()
        local_ip = socket.gethostbyname(socket.gethostname())
        scan_url = f'http://{local_ip}:5000/scan/{token}'
        img = qrcode.make(scan_url)
        path = f"static/qrcodes/{token}.png"
        img.save(path)
        qr_image = path
        session_id = cs.id
    return render_template('generate_qr.html',
        qr_image=qr_image, session_id=session_id,
        scan_url=scan_url, subject=subject, duration=duration)
```

---

What's new in this upgrade:

- Shows subject name and expiry duration on the QR card
- Displays the actual scan URL below the QR (so you can verify it has your PC's IP)
- Print QR button — opens print dialog with just the QR image
- Download QR button — saves QR image to phone/PC
- Cleaner card layout with shadow

---

@app.route(&#39;/generate_qr&#39;, methods=[&#39;GET&#39;, &#39;POST&#39;])
@login_required
def generate_qr():
    qr_image = None
    session_id = None
    if request.method == &#39;POST&#39;:
        token = str(uuid.uuid4())
        cs = ClassSession(
            subject=request.form[&#39;subject&#39;],
            token=token,
            expires_at=datetime.utcnow() + timedelta(minutes=int(request.form.get(&#39;duration&#39;, 30)))
        )
        db.session.add(cs)
        db.session.commit()
        local_ip = socket.gethostbyname(socket.gethostname())
        scan_url = f&#39;http://{local_ip}:5000/scan/{token}&#39;
        img = qrcode.make(scan_url)
        path = f&quot;static/qrcodes/{token}.png&quot;
        img.save(path)
        qr_image = path
        session_id = cs.id
    return render_template(&#39;generate_qr.html&#39;, qr_image=qr_image, session_id=session_id) where to past

---


In `app.py`, find and **replace** the entire old `generate_qr` route with this new one:

**Find this (old code):**
```python
@app.route('/generate_qr', methods=['GET', 'POST'])
@login_required
def generate_qr():
    qr_image = None
    session_id = None
    if request.method == 'POST':
        token = str(uuid.uuid4())
        cs = ClassSession(
            subject=request.form['subject'],
            token=token,
            expires_at=datetime.utcnow() + timedelta(minutes=int(request.form.get('duration', 30)))
        )
        db.session.add(cs)
        db.session.commit()
        local_ip = socket.gethostbyname(socket.gethostname())
        scan_url = f'http://{local_ip}:5000/scan/{token}'
        img = qrcode.make(scan_url)
        path = f"static/qrcodes/{token}.png"
        img.save(path)
        qr_image = path
        session_id = cs.id
    return render_template('generate_qr.html', qr_image=qr_image, session_id=session_id)
```

**Replace with (new code):**
```python
@app.route('/generate_qr', methods=['GET', 'POST'])
@login_required
def generate_qr():
    qr_image = None
    session_id = None
    scan_url = None
    subject = None
    duration = None
    if request.method == 'POST':
        token = str(uuid.uuid4())
        subject = request.form['subject']
        duration = int(request.form.get('duration', 30))
        cs = ClassSession(
            subject=subject,
            token=token,
            expires_at=datetime.utcnow() + timedelta(minutes=duration)
        )
        db.session.add(cs)
        db.session.commit()
        local_ip = socket.gethostbyname(socket.gethostname())
        scan_url = f'http://{local_ip}:5000/scan/{token}'
        img = qrcode.make(scan_url)
        path = f"static/qrcodes/{token}.png"
        img.save(path)
        qr_image = path
        session_id = cs.id
    return render_template('generate_qr.html',
        qr_image=qr_image, session_id=session_id,
        scan_url=scan_url, subject=subject, duration=duration)
```

---

Your full `app.py` route order should look like this so you know exactly where it sits:

```
login route
logout route
students route
generate_qr route   ← replace this one
scan_qr route
mark_attendance route
reports route
export route
```

Save → restart Flask → generate new QR.