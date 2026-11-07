"""Departments, semesters, and subjects: edits that would corrupt existing attendance are blocked."""
from attendqr.extensions import db
from attendqr.models import Subject
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
