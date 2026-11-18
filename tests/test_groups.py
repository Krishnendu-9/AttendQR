"""Groups within a class: a lab batch's session counts only for its members."""
import html
import io
from datetime import timedelta

import openpyxl
import pytest

from attendqr.extensions import db
from attendqr.models import (
    ActivityLog,
    Attendance,
    ClassGroup,
    ClassGroupMember,
    ClassSession,
    Department,
    Semester,
    Student,
)
from attendqr.services.analytics import build_class_trends
from attendqr.services.eligibility import build_eligibility
from attendqr.services.groups import equal_ranges, parse_ranges, roll_in_range
from attendqr.services.reports import (
    NOT_IN_GROUP,
    ReportFilters,
    build_attendance_matrix,
    build_shortage_report,
    build_student_attendance_summary,
)
from attendqr.utils import utc_now
from conftest import add_session, make_student, post, post_json


def page(response):
    return html.unescape(response.get_data(as_text=True))


def class_filters(data):
    """Whole-class filters. The report functions read the current user, so call them in a request context."""
    return ReportFilters(department_id=data.mca_id, semester_id=data.sem1_id, subject_id=None, parsed_from=None,
                         parsed_to=None, date_from='', date_to='', error=None)


def make_group(data, name, *student_ids, joined_days_ago=30):
    group = ClassGroup(department_id=data.mca_id, semester_id=data.sem1_id, name=name)
    db.session.add(group)
    db.session.flush()
    for student_id in student_ids:
        db.session.add(ClassGroupMember(group_id=group.id, student_id=student_id,
                                        joined_at=utc_now() - timedelta(days=joined_days_ago)))
    db.session.commit()
    return group


@pytest.fixture
def labs(data):
    """Rahul in Gr-1, Priya in Gr-2. One whole-class lecture both attended, and a Gr-1 lab Rahul attended."""
    gr1 = make_group(data, 'Gr-1', data.student_id)
    gr2 = make_group(data, 'Gr-2', data.student2_id)
    add_session(data, days_ago=2, present=[data.student_id, data.student2_id])
    lab = add_session(data, subject_id=data.subject2_id, days_ago=1, present=[data.student_id], group_id=gr1.id)
    return gr1, gr2, lab


# ── The counting rule ────────────────────────────────────────

def test_another_groups_lab_never_counts_against_a_student(app, data, labs):
    *_, lab = labs
    priya = db.session.get(Student, data.student2_id)

    summary = build_student_attendance_summary(priya)
    assert (summary['overall_attended'], summary['overall_total'], summary['overall_percentage']) == (1, 1, 100.0)

    with app.test_request_context():
        _, rows = build_attendance_matrix(class_filters(data))
        report = build_shortage_report(class_filters(data), 80)
    by_roll = {row['student'].roll_no: row for row in rows}
    assert by_roll['MCA002']['attendance'][lab.id] == NOT_IN_GROUP
    assert (by_roll['MCA002']['total'], by_roll['MCA002']['counted']) == (1, 1)
    assert (by_roll['MCA001']['total'], by_roll['MCA001']['counted']) == (2, 2)

    assert 'MCA002' not in {row['student'].roll_no for row in report['rows']}

    with app.test_request_context():
        trends = build_class_trends(data.mca_id, data.sem1_id, weeks=4)
        eligibility = {row['student'].roll_no: row for row in build_eligibility(data.mca_id, data.sem1_id).rows}
    assert {row['student'].roll_no for row in trends.at_risk} == set()
    assert trends.overall.present == trends.overall.expected == 3
    assert (eligibility['MCA002']['held'], eligibility['MCA002']['status']) == (1, 'eligible')


def test_a_member_who_missed_the_lab_is_absent(app, data, labs):
    _, gr2, _ = labs
    gr2_lab = add_session(data, subject_id=data.subject2_id, group_id=gr2.id)  # Priya missed it
    with app.test_request_context():
        _, rows = build_attendance_matrix(class_filters(data))
        shortage_rows = build_shortage_report(class_filters(data), 80)['rows']
    priya = next(row for row in rows if row['student'].id == data.student2_id)
    rahul = next(row for row in rows if row['student'].id == data.student_id)
    assert priya['attendance'][gr2_lab.id] is None and (priya['total'], priya['counted']) == (1, 2)
    assert rahul['attendance'][gr2_lab.id] == NOT_IN_GROUP

    shortage = {row['student'].roll_no: row for row in shortage_rows}
    assert (shortage['MCA002']['attended'], shortage['MCA002']['total']) == (1, 2)
    assert 'MCA001' not in shortage


def test_membership_history_decides_who_a_session_counted_for(app, data):
    """Removed after a session: it still counted. Added after: it did not."""
    group = make_group(data, 'Gr-1', data.student_id, joined_days_ago=10)
    lab = add_session(data, subject_id=data.subject2_id, days_ago=5, group_id=group.id)
    row = ClassGroupMember.query.filter_by(student_id=data.student_id).one()
    row.left_at = utc_now() - timedelta(days=1)
    db.session.add(ClassGroupMember(group_id=group.id, student_id=data.student2_id, joined_at=utc_now()))
    db.session.commit()

    with app.test_request_context():
        rows = build_attendance_matrix(class_filters(data))[1]
    marks = {r['student'].id: r['attendance'][lab.id] for r in rows}
    assert marks[data.student_id] is None            # was a member then: absent
    assert marks[data.student2_id] == NOT_IN_GROUP   # joined later: not theirs


# ── Who may scan or be marked ────────────────────────────────

def test_only_members_can_scan_into_a_groups_session(data, client_for, labs):
    from attendqr.services.qr import make_qr_token

    gr1, _, _ = labs
    live = add_session(data, subject_id=data.subject2_id, group_id=gr1.id)
    priya = client_for(data.student2)
    attend = priya.get(f'/scan/{make_qr_token(live.id)}', follow_redirects=True)
    assert attend.status_code == 403 and 'only for Gr-1 of' in page(attend)
    result = post_json(priya, '/mark_attendance')
    assert result.status_code == 403 and 'only for Gr-1' in result.get_json()['message']

    rahul = client_for(data.student)
    rahul.get(f'/scan/{make_qr_token(live.id)}')
    assert post_json(rahul, '/mark_attendance').get_json()['success'] is True


def test_staff_can_mark_only_members_present(data, client_for, labs):
    gr1, _, _ = labs
    live = add_session(data, subject_id=data.subject2_id, group_id=gr1.id)
    teacher = client_for(data.teacher)
    shown = page(teacher.get(f'/generate_qr?session_id={live.id}'))
    assert 'Rahul Das' in shown and 'Priya Sen' not in shown.split('manual_student')[1].split('</select>')[0]

    response = post(teacher, f'/sessions/{live.id}/attendance', {'student_id': data.student2_id, 'note': 'x'},
                    follow_redirects=True)
    assert "this session's group (Gr-1)" in page(response)
    assert Attendance.query.filter_by(session_id=live.id).count() == 0

    post(teacher, f'/sessions/{live.id}/attendance', {'student_id': data.student_id, 'note': 'Dead phone'})
    assert Attendance.query.filter_by(session_id=live.id, student_id=data.student_id).count() == 1


def test_a_student_added_to_the_group_late_can_be_marked(app, data, client_for, labs):
    gr1, _, lab = labs
    db.session.add(ClassGroupMember(group_id=gr1.id, student_id=data.student2_id, joined_at=utc_now()))
    db.session.commit()
    post(client_for(data.hod), f'/sessions/{lab.id}/attendance', {'student_id': data.student2_id, 'note': 'Left off the list'})
    assert Attendance.query.filter_by(session_id=lab.id, student_id=data.student2_id).count() == 1
    with app.test_request_context():
        rows = build_attendance_matrix(class_filters(data))[1]
    priya = next(r for r in rows if r['student'].id == data.student2_id)
    assert priya['attendance'][lab.id] == 'manual'  # attended, so it counts


# ── Starting a group's session ───────────────────────────────

def test_a_session_can_be_started_for_a_group(data, client_for, labs):
    gr1, _, _ = labs
    teacher = client_for(data.teacher)
    form = {'class_mode': 'regular', 'department_id': data.mca_id, 'semester_id': data.sem1_id,
            'subject_id': data.subject2_id, 'duration': 30, 'group_id': gr1.id}
    assert post(teacher, '/generate_qr', form).status_code == 302
    created = ClassSession.query.order_by(ClassSession.id.desc()).first()
    assert created.group_id == gr1.id
    assert 'MCA102 - Networks · Gr-1' in page(teacher.get(f'/generate_qr?session_id={created.id}'))
    assert "for Gr-1." in ActivityLog.query.filter_by(action='qr_session_created').one().description

    groups = teacher.get(f'/api/groups/{data.sem1_id}').get_json()
    assert [(g['name'], g['members']) for g in groups] == [('Gr-1', 1), ('Gr-2', 1)]


def test_a_group_from_another_class_is_refused(data, client_for):
    other = ClassGroup(department_id=data.mca_id, semester_id=data.sem2_id, name='Gr-1')
    db.session.add(other)
    db.session.commit()
    form = {'class_mode': 'regular', 'department_id': data.mca_id, 'semester_id': data.sem1_id,
            'subject_id': data.subject_id, 'duration': 30, 'group_id': other.id}
    response = post(client_for(data.teacher), '/generate_qr', form, follow_redirects=True)
    assert 'does not belong to the selected semester' in page(response)
    assert ClassSession.query.count() == 0


# ── Managing groups ──────────────────────────────────────────

def test_hod_creates_a_group_and_sets_its_members(data, client_for):
    hod = client_for(data.hod)
    response = post(hod, f'/semesters/{data.sem1_id}/groups', {'name': ' Gr-1 '})
    group = ClassGroup.query.one()
    assert group.name == 'Gr-1' and response.headers['Location'].endswith(f'/groups/{group.id}/members')
    assert 'already has a group called Gr-1' in page(post(hod, f'/semesters/{data.sem1_id}/groups', {'name': 'gr-1'},
                                                          follow_redirects=True))

    other_class_student = db.session.get(Student, db.session.query(Student.id).filter_by(email=data.sem2_student).scalar())
    post(hod, f'/groups/{group.id}/members', {'student_id': [data.student_id, data.student2_id, other_class_student.id]})
    assert {m.student_id for m in ClassGroupMember.query.filter_by(left_at=None)} == {data.student_id, data.student2_id}

    post(hod, f'/groups/{group.id}/members', {'student_id': [data.student_id]})
    priya = ClassGroupMember.query.filter_by(student_id=data.student2_id).one()
    assert priya.left_at is not None  # the row stays, with when she left
    assert 'added 0, removed 1' in ActivityLog.query.filter_by(action='group_members_changed').order_by(
        ActivityLog.id.desc()).first().description

    shown = page(hod.get(f'/semesters/{data.sem1_id}/groups'))
    assert 'Gr-1' in shown and 'Rahul Das' in shown and '1 member ' in shown


def test_renaming_and_deleting_groups(data, client_for, labs):
    gr1, gr2, _ = labs
    hod = client_for(data.hod)
    post(hod, f'/groups/{gr2.id}/rename', {'name': 'Batch B'})
    assert db.session.get(ClassGroup, gr2.id).name == 'Batch B'

    response = post(hod, f'/groups/{gr1.id}/delete', follow_redirects=True)
    assert 'has 1 session(s)' in page(response)
    assert db.session.get(ClassGroup, gr1.id) is not None

    post(hod, f'/groups/{gr2.id}/delete')
    assert db.session.get(ClassGroup, gr2.id) is None
    assert ClassGroupMember.query.filter_by(student_id=data.student2_id).count() == 0


def test_splitting_a_class_by_roll_number(data, client_for):
    department, semester = db.session.get(Department, data.mca_id), db.session.get(Semester, data.sem1_id)
    for n in range(3, 11):
        make_student(f'Student {n}', f'MCA{n:03d}', f's{n}@example.com', department, semester)
    db.session.commit()
    hod = client_for(data.hod)

    post(hod, f'/semesters/{data.sem1_id}/groups/split', {'mode': 'ranges', 'ranges': 'Gr-1: 1-4\nGr-2: 5-10'})
    sizes = {g.name: ClassGroupMember.query.filter_by(group_id=g.id).count() for g in ClassGroup.query}
    assert sizes == {'Gr-1': 4, 'Gr-2': 6}

    post(hod, f'/semesters/{data.sem1_id}/groups/split', {'mode': 'equal', 'count': '3', 'prefix': 'Batch '})
    sizes = {g.name: ClassGroupMember.query.filter_by(group_id=g.id).count() for g in ClassGroup.query}
    assert (sizes['Batch 1'], sizes['Batch 2'], sizes['Batch 3']) == (4, 3, 3)

    response = post(hod, f'/semesters/{data.sem1_id}/groups/split', {'mode': 'ranges', 'ranges': 'nonsense'},
                    follow_redirects=True)
    assert 'should look like' in page(response)


def test_range_parsing_and_matching():
    assert parse_ranges('Gr-1: 1-37\n\nGr-2: MCA038 \u2013 MCA075') == [('Gr-1', '1', '37'), ('Gr-2', 'MCA038', 'MCA075')]
    assert roll_in_range('MCA037', '1', '37') and not roll_in_range('MCA038', '1', '37')
    assert roll_in_range('MCA9', 'MCA1', 'MCA10')  # natural order, not text order
    assert not roll_in_range('ABC', '1', '37')
    with pytest.raises(ValueError, match='too few'):
        equal_ranges([], 2)


def test_moving_a_student_ends_their_groups_in_the_old_class(data, client_for, labs):
    hod = client_for(data.hod)
    post(hod, f'/students/edit/{data.student_id}', {
        'name': 'Rahul Das', 'roll_no': 'MCA001', 'email': 'rahul@example.com', 'semester_id': data.sem2_id,
    })
    assert ClassGroupMember.query.filter_by(student_id=data.student_id).one().left_at is not None
    rahul = db.session.get(Student, data.student_id)
    assert 'Groups' in page(hod.get(f'/students/{rahul.id}'))


def test_scope_of_the_groups_pages(data, client_for, labs):
    gr1, _, _ = labs
    teacher = client_for(data.teacher)  # teaches in Semester 1
    assert teacher.get(f'/semesters/{data.sem1_id}/groups').status_code == 200
    assert 'New group' not in page(teacher.get(f'/semesters/{data.sem1_id}/groups'))
    assert teacher.get(f'/semesters/{data.sem2_id}/groups').status_code == 403
    assert post(teacher, f'/semesters/{data.sem1_id}/groups', {'name': 'X'}).status_code == 403
    assert post(teacher, f'/groups/{gr1.id}/members', {'student_id': []}).status_code == 403

    other_hod = client_for(data.other_teacher)
    assert other_hod.get(f'/semesters/{data.sem1_id}/groups').status_code == 403
    assert client_for(data.student).get(f'/semesters/{data.sem1_id}/groups').status_code == 403
    assert client_for(data.other_teacher).get(f'/api/groups/{data.sem1_id}').status_code == 403


def test_another_departments_hod_cannot_manage_groups(data, client_for, labs):
    from conftest import make_account

    gr1, _, _ = labs
    make_account('hod2@college.example', 'hod', department_id=data.other_id)
    db.session.commit()
    other = client_for('hod2@college.example')
    assert other.get(f'/semesters/{data.sem1_id}/groups').status_code == 403
    assert post(other, f'/groups/{gr1.id}/rename', {'name': 'Mine'}).status_code == 403


def test_exports_mark_other_groups_sessions_with_a_legend(data, client_for, labs):
    response = client_for(data.teacher).get(f'/export/{data.mca_id}/{data.sem1_id}/excel')
    sheet = openpyxl.load_workbook(io.BytesIO(response.data)).active
    headers = [cell.value for cell in sheet[1]]
    assert any(header and 'Gr-1' in header for header in headers)
    lab_column = next(i for i, header in enumerate(headers) if header and 'Gr-1' in header)
    rows = {row[0]: row for row in sheet.iter_rows(min_row=2, values_only=True) if row[0]}
    assert rows['MCA002'][lab_column] == '\u2013' and rows['MCA001'][lab_column] == 'P'
    notes = [row[0] for row in sheet.iter_rows(min_col=1, max_col=1, values_only=True) if row[0]]
    assert any('for a group the student was not in' in note for note in notes)

    matrix = page(client_for(data.teacher).get(f'/reports/detailed?department_id={data.mca_id}&semester_id={data.sem1_id}'))
    assert 'Held for a group the student was not in' in matrix
