from pathlib import Path
import io
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from werkzeug.security import generate_password_hash

import app


def assert_status(client, path, expected):
    response = client.get(path)
    actual = response.status_code
    if actual != expected:
        raise AssertionError(f"{path}: expected {expected}, got {actual}")


def csrf_data(client, data=None):
    data = dict(data or {})
    with client.session_transaction() as sess:
        token = sess.get("_csrf_token")
    if not token:
        client.get("/login")
        with client.session_transaction() as sess:
            token = sess.get("_csrf_token")
    data["_csrf_token"] = token
    return data


def login(client, username, password):
    client.get("/login")
    response = client.post(
        "/login",
        data=csrf_data(client, {"username": username, "password": password}),
        follow_redirects=False,
    )
    if response.status_code != 302:
        raise AssertionError(f"Login failed for {username}: {response.status_code}")


def upsert_account(username, role, password="test123", **kwargs):
    account = app.Admin.query.filter_by(username=username).first()
    if account is None:
        account = app.Admin(username=username, role=role, password=generate_password_hash(password))
        app.db.session.add(account)
    account.role = role
    account.password = generate_password_hash(password)
    for key, value in kwargs.items():
        setattr(account, key, value)
    app.db.session.commit()
    return account


def main():
    ctx = app.app.app_context()
    ctx.push()

    app.ensure_rbac_columns()
    app.ensure_existing_student_accounts()

    department = app.Department.query.first()
    teacher = app.Teacher.query.first()
    student = app.Student.query.first()

    if not department or not teacher or not student:
        raise AssertionError("Smoke test requires at least one department, teacher, and student.")

    upsert_account(
        "_smoke_hod@gmail.com",
        "hod",
        department_id=department.id,
        teacher_id=None,
        student_id=None,
    )
    upsert_account(
        "_smoke_teacher@gmail.com",
        "teacher",
        department_id=teacher.department_id,
        teacher_id=teacher.id,
        student_id=None,
    )
    upsert_account(
        "_smoke_student_access@gmail.com",
        "student",
        department_id=student.department_id,
        teacher_id=None,
        student_id=student.id,
    )
    upsert_account(
        "_smoke_principal_root",
        "super_admin",
        department_id=None,
        teacher_id=None,
        student_id=None,
    )

    next_client = app.app.test_client()
    protected_response = next_client.get("/add_admin", follow_redirects=False)
    redirected_login = protected_response.headers.get("Location", "")
    if protected_response.status_code != 302 or "/login?next=%2Fadd_admin" not in redirected_login:
        raise AssertionError("Protected routes should redirect unauthenticated users to login with a next target.")

    post_login_redirect = next_client.post(
        "/login?next=%2Fadd_admin",
        data=csrf_data(next_client, {"username": "_smoke_principal_root", "password": "test123", "next": "/add_admin"}),
        follow_redirects=False,
    )
    if post_login_redirect.status_code != 302 or not post_login_redirect.headers.get("Location", "").endswith("/add_admin"):
        raise AssertionError("Successful login should redirect to the requested next page.")

    cases = [
        ("principal", "_smoke_principal_root", "test123", {
            "/": 200,
            "/account": 200,
            "/students": 200,
            "/students/import-template": 200,
            "/add_admin": 200,
            "/generate_qr": 200,
            "/reports": 200,
        }),
        ("hod", "_smoke_hod@gmail.com", "test123", {
            "/": 200,
            "/account": 200,
            "/students": 200,
            "/students/import-template": 200,
            "/add_admin": 200,
            "/generate_qr": 200,
            "/reports": 200,
        }),
        ("teacher", "_smoke_teacher@gmail.com", "test123", {
            "/": 200,
            "/account": 200,
            "/students": 200,
            "/students/import-template": 200,
            "/generate_qr": 200,
            "/reports": 200,
        }),
    ]

    for label, username, password, expectations in cases:
        client = app.app.test_client()
        login(client, username, password)
        for path, expected in expectations.items():
            assert_status(client, path, expected)
        print(f"{label}: ok")

    principal_client = app.app.test_client()
    login(principal_client, "_smoke_principal_root", "test123")
    blocked_student = principal_client.post(
        "/add_admin",
        data=csrf_data(principal_client, {
            "username": "blocked.student@example.com",
            "role": "student",
            "password": "test123",
            "confirm_password": "test123",
            "student_id": student.id,
        }),
        follow_redirects=True,
    )
    if "Invalid role selected" not in blocked_student.get_data(as_text=True):
        raise AssertionError("Student account creation from Accounts page should be blocked.")

    student_account = app.Admin.query.filter_by(student_id=student.id).first()
    if student_account:
        delete_student_account = principal_client.post(
            f"/delete_admin/{student_account.id}",
            data=csrf_data(principal_client)
        )
        if delete_student_account.status_code != 403:
            raise AssertionError("Student accounts should not be deleted from Accounts page.")

    hod_client = app.app.test_client()
    login(hod_client, "_smoke_hod@gmail.com", "test123")
    hod_accounts_html = hod_client.get("/add_admin").get_data(as_text=True)
    if "<option value=\"student\"" in hod_accounts_html or "<option value=\"hod\"" in hod_accounts_html:
        raise AssertionError("HOD Accounts page should not offer Student or HOD account creation.")

    fresh_principal_client = app.app.test_client()
    login(fresh_principal_client, "_smoke_principal_root", "test123")

    principal_accounts_html = fresh_principal_client.get("/add_admin").get_data(as_text=True)
    if "<option value=\"student\"" in principal_accounts_html:
        raise AssertionError("Accounts page should not offer Student creation.")

    app.Admin.query.filter_by(username="_smoke_principal").delete()
    app.db.session.commit()

    created_principal = fresh_principal_client.post(
        "/add_admin",
        data=csrf_data(fresh_principal_client, {
            "username": "_smoke_principal",
            "role": "super_admin",
            "password": "test123",
            "confirm_password": "test123",
        }),
        follow_redirects=True,
    )
    created_principal_html = created_principal.get_data(as_text=True)
    if created_principal.status_code != 200 or "Principal account" not in created_principal_html or "_smoke_principal" not in created_principal_html:
        raise AssertionError("Principal account creation from Accounts page should be allowed.")

    current_principal = app.Admin.query.filter_by(username="_smoke_principal_root", role="super_admin").first()
    if current_principal and fresh_principal_client.post(
        f"/delete_admin/{current_principal.id}",
        data=csrf_data(fresh_principal_client)
    ).status_code != 403:
        raise AssertionError("Principal accounts should be protected from Accounts deletion.")

    extra_principal = app.Admin.query.filter_by(username="_smoke_principal", role="super_admin").first()
    if not extra_principal:
        raise AssertionError("Created Principal account should exist.")
    delete_extra_principal = fresh_principal_client.post(
        f"/delete_admin/{extra_principal.id}",
        data=csrf_data(fresh_principal_client),
        follow_redirects=False
    )
    if delete_extra_principal.status_code != 302:
        raise AssertionError("Additional Principal accounts should be deletable by Principal.")

    teacher_client = app.app.test_client()
    login(teacher_client, "_smoke_teacher@gmail.com", "test123")
    teacher_department = app.Department.query.get(teacher.department_id)
    teacher_semester = app.Semester.query.filter_by(department_id=teacher.department_id).first()
    if not teacher_semester:
        raise AssertionError("Smoke test requires a semester in the smoke teacher department.")

    import_roll = "_SMOKE2026"
    import_email = "_smoke_student@example.com"
    app.Admin.query.filter_by(username=import_email).delete()
    app.Student.query.filter_by(email=import_email).delete()
    app.db.session.commit()

    csv_data = "name,roll_no,email,semester\nSmoke Student,{},{},{}\n".format(
        import_roll,
        import_email,
        teacher_semester.name,
    )
    import_response = teacher_client.post(
        "/students/import",
        data=csrf_data(teacher_client, {"student_file": (io.BytesIO(csv_data.encode("utf-8")), "students.csv")}),
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    if import_response.status_code != 200 or "Imported 1 student profile" not in import_response.get_data(as_text=True):
        raise AssertionError("Teacher bulk student import should create one student.")

    imported_student = app.Student.query.filter_by(email=import_email).first()
    imported_account = app.Admin.query.filter_by(username=import_email, role="student").first()
    if not imported_student or not imported_account:
        raise AssertionError("Bulk import should create both student profile and student login.")
    if imported_student.department_id != teacher_department.id:
        raise AssertionError("Teacher import should be scoped to the teacher department.")

    teachers_html = fresh_principal_client.get("/teachers").get_data(as_text=True)
    if "Add Teacher" in teachers_html or "teacher_name" in teachers_html or "teacher_email" in teachers_html:
        raise AssertionError("Teachers page should not offer teacher creation controls.")

    dashboard_html = fresh_principal_client.get("/").get_data(as_text=True)
    if 'href="/account"' not in dashboard_html:
        raise AssertionError("Sidebar profile card should link to the My Account page.")

    teacher_create_client = app.app.test_client()
    login(teacher_create_client, "_smoke_principal_root", "test123")

    temp_teacher_email = "_smoke_new_teacher@gmail.com"
    app.Admin.query.filter_by(username=temp_teacher_email).delete()
    app.Teacher.query.filter_by(email=temp_teacher_email).delete()
    app.db.session.commit()

    created_teacher = teacher_create_client.post(
        "/add_admin",
        data=csrf_data(teacher_create_client, {
            "username": temp_teacher_email,
            "role": "teacher",
            "teacher_name": "Smoke New Teacher",
            "department_id": department.id,
            "password": "test123",
            "confirm_password": "test123",
        }),
        follow_redirects=True,
    )
    created_teacher_html = created_teacher.get_data(as_text=True)
    if created_teacher.status_code != 200 or "Teacher account" not in created_teacher_html or temp_teacher_email not in created_teacher_html:
        raise AssertionError("Accounts page should create teacher profiles and teacher accounts together.")

    created_teacher_profile = app.Teacher.query.filter_by(email=temp_teacher_email).first()
    created_teacher_account = app.Admin.query.filter_by(username=temp_teacher_email, role="teacher").first()
    if not created_teacher_profile or not created_teacher_account:
        raise AssertionError("Teacher creation from Accounts should create both profile and account.")
    if created_teacher_account.teacher_id != created_teacher_profile.id:
        raise AssertionError("Teacher account should be linked to the created teacher profile.")

    password_client = app.app.test_client()
    login(password_client, "_smoke_teacher@gmail.com", "test123")

    password_change = password_client.post(
        "/account",
        data=csrf_data(password_client, {
            "current_password": "test123",
            "new_password": "test456",
            "confirm_password": "test456",
        }),
        follow_redirects=True,
    )
    if password_change.status_code != 200 or "Password updated successfully." not in password_change.get_data(as_text=True):
        raise AssertionError("My Account page should allow the logged-in user to change password.")

    old_login_client = app.app.test_client()
    old_login_client.get("/login")
    old_login_response = old_login_client.post(
        "/login",
        data=csrf_data(old_login_client, {"username": "_smoke_teacher@gmail.com", "password": "test123"}),
        follow_redirects=False,
    )
    if old_login_response.status_code == 302:
        raise AssertionError("Old password should stop working after password change.")

    updated_teacher_client = app.app.test_client()
    login(updated_teacher_client, "_smoke_teacher@gmail.com", "test456")
    if updated_teacher_client.get("/account").status_code != 200:
        raise AssertionError("Teacher should be able to log in with the updated password.")

    app.Admin.query.filter_by(username=import_email).delete()
    app.Student.query.filter_by(email=import_email).delete()
    app.Admin.query.filter_by(username="_smoke_principal").delete()
    app.Admin.query.filter_by(username="_smoke_principal_root").delete()
    app.Admin.query.filter_by(username="_smoke_student_access@gmail.com").delete()
    app.Admin.query.filter_by(username=temp_teacher_email).delete()
    app.Teacher.query.filter_by(email=temp_teacher_email).delete()
    app.Admin.query.filter(app.Admin.username.in_(["_smoke_hod@gmail.com", "_smoke_teacher@gmail.com"])).delete(
        synchronize_session=False
    )
    app.db.session.commit()

    print("Smoke test passed.")


if __name__ == "__main__":
    main()
