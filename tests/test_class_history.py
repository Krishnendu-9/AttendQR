"""Students moved to another class stay in the old class's reports for the sessions they were in it for."""
import io

import openpyxl
from werkzeug.datastructures import MultiDict

from attendqr.extensions import db
from attendqr.models import Attendance, Student, StudentClassHistory
from attendqr.services.reports import (
    NOT_IN_CLASS,
    NOT_IN_CLASS_MARK,
    build_attendance_matrix,
    build_shortage_report,
    parse_report_filters,
)
from conftest import add_session, post


def move(client, student, semester_id, **changes):
    form = {'name': student.name, 'roll_no': student.roll_no, 'email': student.email, 'semester_id': semester_id}
    form.update(changes)
    return post(client, f'/students/edit/{student.id}', form)


def report_filters(data, semester_id=None):
    return parse_report_filters(MultiDict({'department_id': data.mca_id, 'semester_id': semester_id or data.sem1_id}))


def test_moving_a_student_records_the_class_they_left(data, client_for):
    student = db.session.get(Student, data.student_id)
    client = client_for(data.hod)

    move(client, student, data.sem1_id, name='Rahul K Das')
    assert StudentClassHistory.query.count() == 0  # Same class: nothing to remember.

    move(client, student, data.sem2_id)
    history = StudentClassHistory.query.one()
    assert (history.student_id, history.department_id, history.semester_id) == (student.id, data.mca_id, data.sem1_id)
    assert db.session.get(Student, data.student_id).semester_id == data.sem2_id


def test_old_class_report_keeps_moved_student_for_earlier_sessions(data, client_for, app):
    before = add_session(data, present=[data.student_id], days_ago=1)
    move(client_for(data.hod), db.session.get(Student, data.student_id), data.sem2_id)
    after = add_session(data, present=[data.student2_id])

    with app.test_request_context():
        sessions, rows = build_attendance_matrix(report_filters(data))
    assert len(sessions) == 2
    moved = next(row for row in rows if row['student'].id == data.student_id)
    assert moved['left_at'] is not None
    assert moved['attendance'][before.id] == 'scan'
    assert moved['attendance'][after.id] == NOT_IN_CLASS
    assert (moved['total'], moved['counted'], moved['percentage']) == (1, 1, 100.0)

    staying = next(row for row in rows if row['student'].id == data.student2_id)
    assert staying['left_at'] is None and staying['counted'] == 2


def test_new_class_report_lists_moved_student_as_current(data, client_for, app):
    move(client_for(data.hod), db.session.get(Student, data.student_id), data.sem2_id)
    with app.test_request_context():
        _, rows = build_attendance_matrix(report_filters(data, data.sem2_id))
    assert {row['student'].id: row['left_at'] for row in rows}[data.student_id] is None


def test_student_moved_out_before_any_session_is_not_listed(data, client_for, app):
    move(client_for(data.hod), db.session.get(Student, data.student_id), data.sem2_id)
    add_session(data, present=[data.student2_id])
    with app.test_request_context():
        _, rows = build_attendance_matrix(report_filters(data))
    assert data.student_id not in {row['student'].id for row in rows}


def test_shortage_counts_only_sessions_before_the_move(data, client_for, app):
    add_session(data, present=[data.student2_id], days_ago=2)
    add_session(data, present=[data.student_id, data.student2_id], days_ago=1)
    move(client_for(data.hod), db.session.get(Student, data.student_id), data.sem2_id)
    for _ in range(3):
        add_session(data, present=[data.student2_id])

    with app.test_request_context():
        report = build_shortage_report(report_filters(data), 80)
    rows = {row['student'].id: row for row in report['rows']}
    # 1 of the 2 sessions before the move: 50%, not 1 of 5.
    assert (rows[data.student_id]['attended'], rows[data.student_id]['total']) == (1, 2)
    assert rows[data.student_id]['left_at'] is not None
    assert report['class_size'] == 1 and report['classes_held'] == 5


def test_old_session_can_still_be_corrected_for_moved_student(data, client_for):
    before = add_session(data, days_ago=1)
    client = client_for(data.hod)
    move(client, db.session.get(Student, data.student_id), data.sem2_id)
    after = add_session(data)

    page = client.get(f'/generate_qr?session_id={before.id}').get_data(as_text=True)
    assert 'Rahul Das' in page
    post(client, f'/sessions/{before.id}/attendance', {'student_id': data.student_id, 'note': 'Was in class'})
    assert Attendance.query.filter_by(session_id=before.id, student_id=data.student_id).count() == 1

    post(client, f'/sessions/{after.id}/attendance', {'student_id': data.student_id, 'note': 'Not in this class'})
    assert Attendance.query.filter_by(session_id=after.id).count() == 0


def test_export_marks_moved_student_and_later_sessions(data, client_for):
    add_session(data, present=[data.student_id], days_ago=1)
    client = client_for(data.hod)
    move(client, db.session.get(Student, data.student_id), data.sem2_id)
    add_session(data)

    response = client.get(f'/export/{data.mca_id}/{data.sem1_id}/excel')
    sheet = openpyxl.load_workbook(io.BytesIO(response.data)).active
    values = [[cell.value for cell in row] for row in sheet.iter_rows()]
    moved = next(row for row in values if row[0] == 'MCA001')
    assert moved[1].startswith('Rahul Das (moved out ')
    assert NOT_IN_CLASS_MARK in moved and 'P' in moved
    assert any(row[0] and 'moved to another class' in str(row[0]) for row in values)

    assert client.get(f'/export/{data.mca_id}/{data.sem1_id}/pdf').status_code == 200
    assert client.get(f'/reports/detailed?department_id={data.mca_id}&semester_id={data.sem1_id}').status_code == 200
    shortage = client.get(f'/reports/shortage?department_id={data.mca_id}&semester_id={data.sem1_id}')
    assert shortage.status_code == 200
