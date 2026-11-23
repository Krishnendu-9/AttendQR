"""Students who left the college: their time in the class ends, and every record stays."""
import html

from werkzeug.datastructures import MultiDict

from attendqr.extensions import db
from attendqr.models import ActivityLog, ClassGroup, ClassGroupMember, Semester, Student, User
from attendqr.services.eligibility import build_eligibility
from attendqr.services.reports import build_attendance_matrix, build_shortage_report, parse_report_filters
from attendqr.utils import utc_now
from conftest import add_session, login, post


def page(response):
    return html.unescape(response.get_data(as_text=True))


def report_filters(data):
    return parse_report_filters(MultiDict({'department_id': data.mca_id, 'semester_id': data.sem1_id}))


def mark_left(client, student_id, note='Completed the course', **kwargs):
    return post(client, f'/students/{student_id}/left', {'note': note}, **kwargs)


def test_marking_a_student_as_left_needs_a_reason_and_keeps_their_records(app, data, client_for):
    group = ClassGroup(department_id=data.mca_id, semester_id=data.sem1_id, name='Gr-1')
    db.session.add(group)
    db.session.flush()
    db.session.add(ClassGroupMember(group_id=group.id, student_id=data.student_id))
    db.session.get(Student, data.student_id).device_id = '0b0b0b0b-0000-4000-8000-000000000000'
    held = add_session(data, present=[data.student_id], days_ago=1)
    hod = client_for(data.hod)

    assert 'Say why' in page(mark_left(hod, data.student_id, note='', follow_redirects=True))
    assert db.session.get(Student, data.student_id).left_at is None
    text = page(mark_left(hod, data.student_id, follow_redirects=True))
    assert 'is marked as left' in text and 'Left the college on' in text and 'Completed the course' in text

    db.session.expire_all()
    student = db.session.get(Student, data.student_id)
    assert student.left_at is not None and student.device_id is None
    assert not User.query.filter_by(student_id=student.id).one().is_active
    assert ClassGroupMember.query.filter_by(student_id=student.id).one().left_at is not None
    assert held.attendances  # the record stays
    assert 'Completed the course' in ActivityLog.query.filter_by(action='student_left').one().description
    assert login(app.test_client(), data.student, expect_success=False).status_code == 200


def test_reports_end_their_time_in_the_class_when_they_left(data, client_for, app):
    add_session(data, present=[data.student_id], days_ago=2)
    mark_left(client_for(data.hod), data.student_id)
    add_session(data, present=[data.student2_id])

    with app.test_request_context():
        _, rows = build_attendance_matrix(report_filters(data))
        shortage = build_shortage_report(report_filters(data), 80)
        eligibility = build_eligibility(data.mca_id, data.sem1_id)
    rahul = next(row for row in rows if row['student'].id == data.student_id)
    assert (rahul['total'], rahul['counted']) == (1, 1) and rahul['left_at'] is not None  # the later class is not theirs
    assert data.student_id not in {row['student'].id for row in shortage['rows']}  # nobody to chase
    assert shortage['class_size'] == 1
    assert data.student_id not in {row['student'].id for row in eligibility.rows}


def test_a_student_who_left_can_be_restored_from_now_on(data, client_for, app):
    add_session(data, present=[data.student_id], days_ago=3)
    hod = client_for(data.hod)
    mark_left(hod, data.student_id)
    add_session(data, days_ago=0)  # held while they were away

    text = page(post(hod, f'/students/{data.student_id}/restore', follow_redirects=True))
    assert 'is a student again' in text
    db.session.expire_all()
    student = db.session.get(Student, data.student_id)
    assert student.left_at is None and User.query.filter_by(student_id=student.id).one().is_active
    with app.test_request_context():
        _, rows = build_attendance_matrix(report_filters(data))
    rahul = next(row for row in rows if row['student'].id == data.student_id)
    assert rahul['counted'] == 1  # the class held while they were away is not theirs
    login(app.test_client(), data.student)


def test_a_student_who_left_is_not_moved_but_restored_first(data, client_for):
    hod = client_for(data.hod)
    mark_left(hod, data.student_id)
    text = page(post(client_for(data.principal), f'/students/edit/{data.student_id}', {
        'name': 'Rahul Das', 'roll_no': 'MCA001', 'email': data.student, 'department_id': data.mca_id,
        'semester_id': data.sem2_id}, follow_redirects=True))
    assert 'Restore them before moving them' in text
    assert db.session.get(Student, data.student_id).semester_id == data.sem1_id


def test_a_locked_semester_keeps_everyone_who_was_in_it_when_it_was_locked(data, client_for, app):
    """Promoting a class after its exams, or marking a graduate as left, must not empty its final eligibility."""
    add_session(data, present=[data.student_id, data.student2_id], days_ago=1)
    hod = client_for(data.hod)
    post(hod, f'/semesters/{data.sem1_id}/lock')
    with app.test_request_context():
        before = {row['student'].id for row in build_eligibility(data.mca_id, data.sem1_id).rows}
    assert before == {data.student_id, data.student2_id}

    mark_left(hod, data.student_id)  # allowed while locked: it changes nothing final
    post(client_for(data.principal), f'/students/edit/{data.student2_id}', {
        'name': 'Priya Sen', 'roll_no': 'MCA002', 'email': data.student2, 'department_id': data.mca_id,
        'semester_id': data.sem2_id})
    db.session.expire_all()
    assert db.session.get(Student, data.student_id).left_at is not None
    assert db.session.get(Student, data.student2_id).semester_id == data.sem2_id
    with app.test_request_context():
        after = {row['student'].id for row in build_eligibility(data.mca_id, data.sem1_id).rows}
    assert after == before

    # Coming back to the locked class would add them to it again: refused.
    text = page(post(hod, f'/students/{data.student_id}/restore', follow_redirects=True))
    assert 'cannot come back to a locked semester' in text
    assert db.session.get(Student, data.student_id).left_at is not None


def test_students_who_left_before_the_lock_stay_out_of_it(data, client_for, app):
    add_session(data, present=[data.student_id, data.student2_id], days_ago=1)
    hod = client_for(data.hod)
    mark_left(hod, data.student_id)
    semester = db.session.get(Semester, data.sem1_id)
    semester.attendance_locked_at = utc_now()
    db.session.commit()
    with app.test_request_context():
        assert {row['student'].id for row in build_eligibility(data.mca_id, data.sem1_id).rows} == {data.student2_id}


def test_the_students_list_shows_current_students_unless_asked(data, client_for):
    hod = client_for(data.hod)
    mark_left(hod, data.student_id)
    rahul, priya = f'data-href="/students/{data.student_id}"', f'data-href="/students/{data.student2_id}"'
    current = page(hod.get('/students'))
    assert rahul not in current and priya in current
    left = page(hod.get('/students?status=left'))
    assert rahul in left and priya not in left and 'Left the college' in left
    everyone = page(hod.get('/students?status=all'))
    assert rahul in everyone and priya in everyone
