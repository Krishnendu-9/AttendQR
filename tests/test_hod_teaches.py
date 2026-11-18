"""An HOD who also teaches: a teacher profile linked to the HOD account gives them their own classes
(Today's classes, My routine) on top of their HOD rights."""
from datetime import timedelta

import pytest

from attendqr.extensions import db
from attendqr.models import ClassSession, Subject, Teacher, Timetable, TimetableEntry, User
from attendqr.services.occurrences import occurrences
from attendqr.utils import local_now
from conftest import post


def edit_hod(client, account, data, **values):
    form = {'username': account.username, 'department_id': data.mca_id}
    form.update(values)
    return post(client, f'/edit_admin/{account.id}', form, follow_redirects=True)


def hod_account(data):
    return User.query.filter_by(username=data.hod).one()


def test_the_principal_links_a_teacher_profile_to_an_hod(data, client_for):
    principal = client_for(data.principal)
    account = hod_account(data)
    edit_hod(principal, account, data, teacher_name='Dr. Manas Ghosh', teacher_short_name='MG')
    db.session.refresh(account)
    assert account.teacher is not None
    assert (account.teacher.name, account.teacher.email, account.teacher.department_id, account.teacher.short_name) == (
        'Dr. Manas Ghosh', data.hod, data.mca_id, 'MG')

    # Leaving the name empty unlinks it; the profile stays, with whatever it taught.
    profile_id = account.teacher_id
    edit_hod(principal, account, data, teacher_name='')
    db.session.refresh(account)
    assert account.teacher_id is None and db.session.get(Teacher, profile_id) is not None


def test_an_hod_created_with_a_teacher_name_teaches(data, client_for):
    principal = client_for(data.principal)
    old = hod_account(data)
    old.is_active = False  # one active HOD per department
    db.session.commit()
    post(principal, '/accounts', {'username': 'manas.ghosh@college.example', 'role': 'hod', 'department_id': data.mca_id,
                                  'teacher_name': 'Dr. Manas Ghosh', 'password': 'Mca-hod-2026x',
                                  'confirm_password': 'Mca-hod-2026x'})
    account = User.query.filter_by(username='manas.ghosh@college.example').one()
    assert account.role == 'hod' and account.teacher.name == 'Dr. Manas Ghosh'


def test_an_hod_without_a_teacher_name_does_not_teach(data, client_for):
    account = hod_account(data)
    assert account.teacher is None
    html = client_for(data.hod).get('/').get_data(as_text=True)
    assert "Today's classes" not in html and 'My routine' not in html
    assert client_for(data.hod).get('/routine/me').status_code == 403


def test_a_teaching_hod_sees_and_starts_their_own_class(app, data, client_for):
    now = local_now()
    if now.hour == 0 or now.hour >= 22:
        pytest.skip("Needs a class that can start now, on the same day.")
    account = hod_account(data)
    teacher = Teacher(name='Dr. Manas Ghosh', email=data.hod, department_id=data.mca_id)
    db.session.add(teacher)
    db.session.flush()
    account.teacher_id = teacher.id
    subject = db.session.get(Subject, data.subject_id)
    subject.teacher_id = teacher.id
    app.config['WORKING_DAYS'] = (0, 1, 2, 3, 4, 5, 6)
    version = Timetable(department_id=data.mca_id, semester_id=data.sem1_id, status='published',
                        effective_from=now.date() - timedelta(days=7))
    db.session.add(version)
    start = (now - timedelta(minutes=10)).time().replace(second=0, microsecond=0)
    end = (now + timedelta(minutes=40)).time().replace(second=0, microsecond=0)
    entry = TimetableEntry(timetable=version, weekday=now.weekday(), start_time=start, end_time=end, subject=subject)
    db.session.add(entry)
    entry.teachers = [teacher]
    db.session.commit()

    client = client_for(data.hod)
    html = client.get('/').get_data(as_text=True)
    assert "Today's classes" in html and 'Start session' in html
    assert '<span class="avatar">MG</span>' in html  # from the teacher name, not the email
    assert client.get('/routine/me').status_code == 200

    occurrence = next(iter(occurrences(now.date(), now.date(), semester_ids=[data.sem1_id])))
    post(client, '/routine/occurrences/start', {'key': occurrence.key})
    session = ClassSession.query.one()
    assert session.teacher_id == teacher.id and session.timetable_entry_id == entry.id


def test_a_teaching_hod_keeps_department_wide_rights(data, client_for):
    account = hod_account(data)
    teacher = Teacher(name='Dr. Manas Ghosh', email=data.hod, department_id=data.mca_id)
    db.session.add(teacher)
    db.session.flush()
    account.teacher_id = teacher.id
    db.session.commit()
    client = client_for(data.hod)
    # Still sees every subject of the department, not only the ones they teach.
    html = client.get('/subjects').get_data(as_text=True)
    assert 'DBMS' in html and 'Networks' in html
    assert client.get('/accounts').status_code == 200

