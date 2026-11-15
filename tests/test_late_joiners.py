"""Students who join a class late: sessions held before they joined are not theirs.

Also checks that the three places that count attendance (the student dashboard, the report
matrix and the shortage list) give the same numbers, since they share one rule (services/counting.py).
"""
import csv
import io
from datetime import timedelta

from werkzeug.datastructures import MultiDict

from attendqr.extensions import db
from attendqr.models import (
    Attendance,
    ClassGroup,
    ClassGroupMember,
    Department,
    LeaveRequest,
    Semester,
    Student,
    StudentClassHistory,
)
from attendqr.services.enrollment import class_roster
from attendqr.services.reports import (
    NOT_IN_CLASS,
    NOT_IN_GROUP,
    build_attendance_matrix,
    build_export_rows,
    build_shortage_report,
    build_student_attendance_summary,
    parse_report_filters,
)
from attendqr.utils import to_local, utc_now
from conftest import add_session, make_student, post


def report_filters(data):
    return parse_report_filters(MultiDict({'department_id': data.mca_id, 'semester_id': data.sem1_id}))


def late_joiner(data, days_ago):
    student = make_student('Neha Roy', 'MCA003', 'neha@example.com', db.session.get(Department, data.mca_id),
                           db.session.get(Semester, data.sem1_id))
    student.class_joined_at = utc_now() - timedelta(days=days_ago)
    db.session.commit()
    return student


def test_sessions_before_a_student_joined_do_not_count(app, data):
    before = add_session(data, present=[data.student_id], days_ago=3)
    student = late_joiner(data, days_ago=2)
    after = add_session(data, present=[data.student_id], days_ago=1)

    with app.test_request_context():
        _, rows = build_attendance_matrix(report_filters(data))
        summary = build_student_attendance_summary(student)
    row = next(r for r in rows if r['student'].id == student.id)
    assert row['attendance'][before.id] == NOT_IN_CLASS
    assert row['attendance'][after.id] is None  # absent, and it counts
    assert (row['total'], row['counted'], row['joined_late']) == (0, 1, student.class_joined_at)
    assert (summary['overall_attended'], summary['overall_total']) == (0, 1)

    # Students in the class from the start are unchanged.
    rahul = next(r for r in rows if r['student'].id == data.student_id)
    assert (rahul['total'], rahul['counted'], rahul['joined_late']) == (2, 2, None)


def test_a_class_attended_before_joining_still_counts(app, data):
    """Staff may mark a late joiner present for a class they attended before their account existed."""
    before = add_session(data, days_ago=3)
    student = late_joiner(data, days_ago=2)
    db.session.add(Attendance(student_id=student.id, session_id=before.id, timestamp=before.created_at,
                              source=Attendance.SOURCE_MANUAL, note='Admitted that morning'))
    db.session.commit()

    with app.test_request_context():
        _, rows = build_attendance_matrix(report_filters(data))
    row = next(r for r in rows if r['student'].id == student.id)
    assert row['attendance'][before.id] == 'manual'
    assert (row['total'], row['counted']) == (1, 1)


def test_late_joiner_can_be_marked_for_an_earlier_session(data, client_for):
    before = add_session(data, days_ago=3)
    late_joiner(data, days_ago=2)
    html = client_for(data.hod).get(f'/generate_qr?session_id={before.id}').get_data(as_text=True)
    assert 'Neha Roy' in html


def test_adding_and_importing_students_records_when_they_joined(data, client_for):
    client = client_for(data.hod)
    post(client, '/students', {'name': 'New One', 'roll_no': 'MCA050', 'email': 'new1@example.com',
                               'semester_id': data.sem1_id})
    added = Student.query.filter_by(email='new1@example.com').one()
    assert added.class_joined_at is not None and utc_now() - added.class_joined_at < timedelta(minutes=1)

    template = client.get('/students/import-template').get_data(as_text=True)
    headers, sample = list(csv.reader(io.StringIO(template)))
    row = dict(zip(headers, sample, strict=True))
    row.update(name='Imported One', roll_no='MCA051', email='new2@example.com')
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=headers)
    writer.writeheader()
    writer.writerow(row)
    post(client, '/students/import', {'student_file': (io.BytesIO(out.getvalue().encode()), 'students.csv')},
         content_type='multipart/form-data')
    imported = Student.query.filter_by(email='new2@example.com').one()
    assert imported.class_joined_at is not None


def test_moving_keeps_when_they_joined_the_old_class(app, data, client_for):
    student = db.session.get(Student, data.student_id)
    joined = utc_now() - timedelta(days=30)
    student.class_joined_at = joined
    db.session.commit()

    form = {'name': student.name, 'roll_no': student.roll_no, 'email': student.email, 'semester_id': data.sem2_id}
    post(client_for(data.hod), f'/students/edit/{student.id}', form)

    history = StudentClassHistory.query.one()
    assert history.joined_at == joined and history.left_at is not None
    moved = db.session.get(Student, data.student_id)
    assert moved.class_joined_at == history.left_at  # joined the new class as they left the old

    old = next(m for m in class_roster(data.mca_id, data.sem1_id) if m.student.id == student.id)
    assert not old.is_current and old.spans == [(joined, history.left_at)]
    assert not old.in_class(joined - timedelta(days=1)) and old.in_class(joined + timedelta(days=1))


def test_a_student_who_comes_back_counts_for_both_stays(app, data):
    """Left the class and came back: sessions in either stay count, the ones in between do not."""
    student = db.session.get(Student, data.student_id)
    now = utc_now()
    db.session.add(StudentClassHistory(student_id=student.id, department_id=data.mca_id, semester_id=data.sem1_id,
                                       joined_at=None, left_at=now - timedelta(days=4)))
    student.class_joined_at = now - timedelta(days=2)
    db.session.commit()
    first = add_session(data, present=[student.id], days_ago=5)
    away = add_session(data, days_ago=3)
    back = add_session(data, days_ago=1)

    with app.test_request_context():
        _, rows = build_attendance_matrix(report_filters(data))
    marks = next(r for r in rows if r['student'].id == student.id)['attendance']
    assert (marks[first.id], marks[away.id], marks[back.id]) == ('scan', NOT_IN_CLASS, None)


def test_exports_name_late_joiners(app, data):
    add_session(data, days_ago=3)
    late_joiner(data, days_ago=2)
    with app.test_request_context():
        sessions, rows = build_attendance_matrix(report_filters(data))
    _, lines = build_export_rows(sessions, rows)
    joined_on = f"{to_local(utc_now() - timedelta(days=2)):%d-%m-%Y}"
    assert any(line[1] == f"Neha Roy (joined {joined_on})" for line in lines)


def test_dashboard_matrix_and_shortage_agree(app, data):
    """One mixed class: a late joiner, a lab for one group, approved leave and a manual entry."""
    now = utc_now()
    group = ClassGroup(department_id=data.mca_id, semester_id=data.sem1_id, name='Gr-1')
    db.session.add(group)
    db.session.flush()
    db.session.add(ClassGroupMember(group_id=group.id, student_id=data.student_id, joined_at=now - timedelta(days=30)))
    db.session.commit()

    late = late_joiner(data, days_ago=3.5)
    add_session(data, present=[data.student_id], days_ago=6)
    lab = add_session(data, present=[data.student_id], days_ago=5, group_id=group.id)
    add_session(data, present=[data.student2_id], days_ago=4)
    add_session(data, subject_id=data.subject2_id, present=[data.student_id], days_ago=2)
    add_session(data, subject_id=data.subject2_id, present=[late.id, data.student2_id], days_ago=1)
    early = add_session(data, days_ago=6.5)
    db.session.add(Attendance(student_id=late.id, session_id=early.id, timestamp=early.created_at,
                              source=Attendance.SOURCE_MANUAL, note='Before the account existed'))
    leave_day = to_local(now - timedelta(days=2)).date()
    db.session.add(LeaveRequest(student_id=data.student2_id, start_date=leave_day, end_date=leave_day, kind='medical',
                                reason='Fever', status=LeaveRequest.APPROVED))
    db.session.commit()

    with app.test_request_context():
        _, matrix_rows = build_attendance_matrix(report_filters(data))
        shortage = {row['student'].id: row for row in build_shortage_report(report_filters(data), 99)['rows']}
        summaries = {s.id: build_student_attendance_summary(s)
                     for s in Student.query.filter_by(semester_id=data.sem1_id)}

    matrix = {row['student'].id: row for row in matrix_rows}
    assert matrix[data.student2_id]['attendance'][lab.id] == NOT_IN_GROUP
    for student_id, summary in summaries.items():
        row = matrix[student_id]
        expected = (row['total'], row['counted'])
        assert (summary['overall_attended'], summary['overall_total']) == expected, student_id
        if student_id in shortage:
            assert (shortage[student_id]['attended'], shortage[student_id]['total']) == expected, student_id
    # The numbers themselves: Rahul every class, Priya excused one and not in the lab,
    # Neha only from when she joined plus the class she was marked present for before.
    assert (matrix[data.student_id]['total'], matrix[data.student_id]['counted']) == (3, 6)
    assert (matrix[data.student2_id]['total'], matrix[data.student2_id]['counted']) == (2, 4)
    assert (matrix[late.id]['total'], matrix[late.id]['counted']) == (2, 3)
    assert set(shortage) == {data.student_id, data.student2_id, late.id}
