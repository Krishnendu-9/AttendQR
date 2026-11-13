"""Departments, semesters, subjects and teachers: scope rules, validation, and edits that
would corrupt existing attendance are blocked."""
import html

from attendqr.extensions import db
from attendqr.models import ActivityLog, Department, Semester, Subject, Teacher, User
from conftest import add_session, post


def edit_subject(client, data, **changes):
    form = {'name': 'DBMS', 'paper_code': 'MCA101', 'semester_id': data.sem1_id, 'teacher_id': data.teacher_id}
    form.update(changes)
    return post(client, f'/subjects/edit/{data.subject_id}', form, follow_redirects=True)


def test_subject_with_sessions_cannot_move_to_another_semester(data, client_for):
    add_session(data, present=[data.student_id])
    response = edit_subject(client_for(data.hod), data, semester_id=data.sem2_id)
    assert 'cannot move to another semester or department' in response.get_data(as_text=True)
    db.session.expire_all()
    assert db.session.get(Subject, data.subject_id).semester_id == data.sem1_id


def test_subject_with_sessions_can_still_be_renamed(data, client_for):
    add_session(data)
    edit_subject(client_for(data.hod), data, name='Database Systems')
    db.session.expire_all()
    assert db.session.get(Subject, data.subject_id).name == 'Database Systems'


def test_subject_without_sessions_can_move(data, client_for):
    edit_subject(client_for(data.hod), data, semester_id=data.sem2_id)
    db.session.expire_all()
    assert db.session.get(Subject, data.subject_id).semester_id == data.sem2_id


# ── Helpers ──────────────────────────────────────────────────

def page(response):
    return html.unescape(response.get_data(as_text=True))


def send(client, url, form):
    return post(client, url, form, follow_redirects=True)


# ── Departments ──────────────────────────────────────────────

def test_principal_creates_a_department_and_it_is_logged(data, client_for):
    send(client_for(data.principal), '/departments', {'branch': 'm.tech', 'name': 'Data Science (DS)'})

    department = Department.query.filter_by(name='Data Science (DS)').one()
    assert department.branch == 'M.TECH'
    assert ActivityLog.query.filter_by(action='department_created', target_id=department.id).count() == 1


def test_department_names_are_unique_ignoring_case(data, client_for):
    response = send(client_for(data.principal), '/departments',
                    {'branch': 'MCA', 'name': 'master of computer application (mca)'})
    assert 'already exists' in page(response)
    assert Department.query.count() == 2


def test_department_needs_branch_and_name(data, client_for):
    response = send(client_for(data.principal), '/departments', {'branch': '', 'name': 'Physics'})
    assert 'Branch and course name are required' in page(response)
    assert Department.query.count() == 2


def test_hod_cannot_create_or_edit_departments(data, client_for):
    hod = client_for(data.hod)
    assert post(hod, '/departments', {'branch': 'X', 'name': 'New (N)'}).status_code == 403
    assert post(hod, f'/departments/edit/{data.mca_id}', {'branch': 'X', 'name': 'Renamed'}).status_code == 403


def test_department_rename_cannot_take_another_departments_name(data, client_for):
    response = send(client_for(data.principal), f'/departments/edit/{data.mca_id}',
                    {'branch': 'MCA', 'name': 'Bachelor of Technology (CSE)'})
    assert 'already exists' in page(response)
    db.session.expire_all()
    assert db.session.get(Department, data.mca_id).name == 'Master of Computer Application (MCA)'


def test_department_with_records_cannot_be_deleted_but_an_empty_one_can(data, client_for):
    client = client_for(data.principal)
    response = page(send(client, f'/departments/delete/{data.mca_id}', {}))
    assert 'Cannot delete' in response and 'staff accounts' in response

    empty = Department(name='Empty (E)', branch='E')
    db.session.add(empty)
    db.session.commit()
    send(client, f'/departments/delete/{empty.id}', {})
    db.session.expire_all()
    assert db.session.get(Department, empty.id) is None
    assert db.session.get(Department, data.mca_id) is not None


# ── Semesters ────────────────────────────────────────────────

def test_hod_creates_semesters_only_in_their_own_department(data, client_for):
    # The department in the form is ignored for an HOD: their own is used.
    send(client_for(data.hod), '/semesters', {'name': 'Semester 3', 'department_id': data.other_id})

    assert Semester.query.filter_by(name='Semester 3').one().department_id == data.mca_id


def test_semester_names_are_unique_per_department_and_limited_in_length(data, client_for):
    client = client_for(data.principal)
    duplicate = send(client, '/semesters', {'name': 'semester 1', 'department_id': data.mca_id})
    too_long = send(client, '/semesters', {'name': 'S' * 51, 'department_id': data.mca_id})
    # The same name in another department is fine.
    send(client, '/semesters', {'name': 'Semester 2', 'department_id': data.other_id})

    assert 'already exists' in page(duplicate)
    assert 'at most 50 characters' in page(too_long)
    assert Semester.query.filter_by(department_id=data.other_id, name='Semester 2').count() == 1


def test_hod_cannot_edit_or_delete_another_departments_semester(data, client_for):
    hod = client_for(data.hod)
    assert post(hod, f'/semesters/edit/{data.other_sem_id}', {'name': 'Hacked'}).status_code == 403
    assert post(hod, f'/semesters/delete/{data.other_sem_id}', {}).status_code == 403


def test_semester_with_students_cannot_change_department(data, client_for):
    response = send(client_for(data.principal), f'/semesters/edit/{data.sem1_id}',
                    {'name': 'Semester One', 'department_id': data.other_id})
    assert 'before changing its department' in page(response)
    db.session.expire_all()
    assert db.session.get(Semester, data.sem1_id).department_id == data.mca_id


def test_semester_rename_is_saved(data, client_for):
    send(client_for(data.hod), f'/semesters/edit/{data.sem2_id}', {'name': 'Semester II'})
    db.session.expire_all()
    assert db.session.get(Semester, data.sem2_id).name == 'Semester II'


def test_semester_with_students_cannot_be_deleted_but_an_empty_one_can(data, client_for):
    client = client_for(data.principal)
    assert 'Cannot delete' in page(send(client, f'/semesters/delete/{data.sem1_id}', {}))
    send(client, f'/semesters/delete/{data.other_sem_id}', {})
    db.session.expire_all()
    assert db.session.get(Semester, data.other_sem_id) is None
    assert db.session.get(Semester, data.sem1_id) is not None


# ── Subjects ─────────────────────────────────────────────────

def new_subject(**changes):
    form = {'name': 'Compilers', 'paper_code': 'mca103', 'semester_id': '', 'teacher_id': ''}
    form.update(changes)
    return form


def test_hod_creates_a_subject_with_a_teacher(data, client_for):
    send(client_for(data.hod), '/subjects', new_subject(semester_id=data.sem1_id, teacher_id=data.teacher_id))

    subject = Subject.query.filter_by(name='Compilers').one()
    assert (subject.paper_code, subject.department_id, subject.teacher_id) == ('MCA103', data.mca_id, data.teacher_id)


def test_subject_name_and_paper_code_must_be_unique(data, client_for):
    client = client_for(data.hod)
    same_name = send(client, '/subjects', new_subject(name='dbms', semester_id=data.sem1_id))
    same_code = send(client, '/subjects', new_subject(paper_code='mca101', semester_id=data.sem2_id))

    assert "Subject 'dbms' already exists" in page(same_name)
    assert "Paper code 'MCA101' already exists" in page(same_code)
    assert Subject.query.filter_by(department_id=data.mca_id).count() == 2


def test_teachers_cannot_create_subjects(data, client_for):
    assert post(client_for(data.teacher), '/subjects', new_subject(semester_id=data.sem1_id)).status_code == 403


def test_subject_cannot_be_assigned_a_teacher_from_another_department(data, client_for):
    other_teacher = Teacher.query.filter_by(email='ravi@rcciit.org.in').one()
    response = post(client_for(data.principal), '/subjects', new_subject(
        semester_id=data.sem1_id, teacher_id=other_teacher.id, department_id=data.mca_id))
    assert response.status_code == 403
    assert Subject.query.filter_by(name='Compilers').count() == 0


def test_subject_with_sessions_cannot_be_deleted_but_one_without_can(data, client_for):
    add_session(data)
    client = client_for(data.hod)

    assert 'Cannot delete' in page(send(client, f'/subjects/delete/{data.subject_id}', {}))
    send(client, f'/subjects/delete/{data.subject2_id}', {})
    db.session.expire_all()
    assert db.session.get(Subject, data.subject2_id) is None


# ── Teachers ─────────────────────────────────────────────────

def edit_teacher(client, data, **changes):
    form = {'name': 'Asha Teacher', 'email': 'asha@rcciit.org.in', 'department_id': data.mca_id}
    form.update(changes)
    return send(client, f'/teachers/edit/{data.teacher_id}', form)


def test_editing_a_teacher_email_also_renames_their_login(data, client_for):
    edit_teacher(client_for(data.hod), data, name='Asha Rao', email='asha.rao@rcciit.org.in')

    db.session.expire_all()
    assert db.session.get(Teacher, data.teacher_id).name == 'Asha Rao'
    assert User.query.filter_by(teacher_id=data.teacher_id).one().username == 'asha.rao@rcciit.org.in'


def test_teacher_email_must_use_a_staff_domain_and_be_unused(data, client_for):
    client = client_for(data.hod)
    outside = edit_teacher(client, data, email='asha@example.org')
    taken = edit_teacher(client, data, email=data.hod)

    assert 'Use a valid email ending with' in page(outside)
    assert 'already exists' in page(taken)


def test_teacher_with_subjects_cannot_move_department_or_be_deleted(data, client_for):
    client = client_for(data.principal)
    moved = edit_teacher(client, data, department_id=data.other_id)
    deleted = send(client, f'/teachers/delete/{data.teacher_id}', {})

    assert 'Cannot delete' in page(moved) and 'Cannot delete' in page(deleted)
    db.session.expire_all()
    assert db.session.get(Teacher, data.teacher_id).department_id == data.mca_id


def test_deleting_an_unused_teacher_removes_their_login(data, client_for):
    Subject.query.filter_by(teacher_id=data.teacher_id).update({'teacher_id': None})
    db.session.commit()
    send(client_for(data.hod), f'/teachers/delete/{data.teacher_id}', {})

    db.session.expire_all()
    assert db.session.get(Teacher, data.teacher_id) is None
    assert User.query.filter_by(username='asha@rcciit.org.in').first() is None


def test_hod_cannot_edit_another_departments_teacher(data, client_for):
    other_teacher = Teacher.query.filter_by(email='ravi@rcciit.org.in').one()
    response = post(client_for(data.hod), f'/teachers/edit/{other_teacher.id}',
                    {'name': 'X', 'email': 'x@rcciit.org.in'})
    assert response.status_code == 403
