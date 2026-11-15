"""Subject options: subjects that do not count, co-teachers, a default group; teacher short names."""
import html
import io

import openpyxl

from attendqr.access import teacher_short_name
from attendqr.extensions import db
from attendqr.models import ActivityLog, Attendance, ClassGroup, ClassSession, Student, Subject, Teacher
from attendqr.services.analytics import build_class_trends
from attendqr.services.eligibility import build_eligibility
from attendqr.services.reports import (
    ReportFilters,
    build_attendance_matrix,
    build_shortage_report,
    build_student_attendance_summary,
)
from conftest import add_session, make_account, post


def page(response):
    return html.unescape(response.get_data(as_text=True))


def class_filters(data):
    return ReportFilters(department_id=data.mca_id, semester_id=data.sem1_id, subject_id=None, parsed_from=None,
                         parsed_to=None, date_from='', date_to='', error=None)


def subject_form(data, **values):
    form = {'paper_code': 'MCA190', 'name': 'Intel AI', 'department_id': data.mca_id, 'semester_id': data.sem1_id,
            'teacher_id': data.teacher_id}
    form.update(values)
    return form


# ── Counts towards attendance ────────────────────────────────

def test_a_subject_can_be_created_that_does_not_count(data, client_for):
    hod = client_for(data.hod)
    post(hod, '/subjects', subject_form(data, counts_towards_attendance=['0']))
    subject = Subject.query.filter_by(paper_code='MCA190').one()
    assert subject.counts_towards_attendance is False
    assert 'does not count towards attendance' in ActivityLog.query.filter_by(action='subject_created').one().description

    post(hod, f'/subjects/edit/{subject.id}', subject_form(data, counts_towards_attendance=['0', '1']))
    assert db.session.get(Subject, subject.id).counts_towards_attendance is True


def test_a_form_without_the_switch_keeps_subjects_counted(data, client_for):
    post(client_for(data.hod), '/subjects', subject_form(data))
    assert Subject.query.filter_by(paper_code='MCA190').one().counts_towards_attendance is True


def test_sessions_of_a_subject_that_does_not_count_count_nowhere(app, data, client_for):
    intel = Subject(name='Intel AI', paper_code='MCA190', department_id=data.mca_id, semester_id=data.sem1_id,
                    teacher_id=data.teacher_id, counts_towards_attendance=False)
    db.session.add(intel)
    db.session.commit()
    add_session(data, days_ago=1, present=[data.student_id])               # a counted class Rahul attended
    for n in range(4):
        add_session(data, subject_id=intel.id, days_ago=n + 2, present=[])   # Intel AI, missed every time

    rahul = db.session.get(Student, data.student_id)
    summary = build_student_attendance_summary(rahul)
    assert (summary['overall_attended'], summary['overall_total']) == (1, 1)
    assert 'MCA190' not in [row['subject'].paper_code for row in summary['rows']]

    with app.test_request_context():
        sessions, rows = build_attendance_matrix(class_filters(data))
        shortage = build_shortage_report(class_filters(data), 80)
        trends = build_class_trends(data.mca_id, data.sem1_id, weeks=4)
        eligibility = {r['student'].roll_no: r for r in build_eligibility(data.mca_id, data.sem1_id).rows}
    assert len(sessions) == 5  # listed in the matrix
    assert next(r for r in rows if r['student'].id == data.student_id)['counted'] == 1
    assert 'MCA001' not in {r['student'].roll_no for r in shortage['rows']}
    assert 'MCA190' not in [s.paper_code for s in shortage['subjects']]
    assert trends.sessions == 1
    assert (eligibility['MCA001']['held'], eligibility['MCA001']['status']) == (1, 'eligible')

    response = client_for(data.teacher).get(f'/export/{data.mca_id}/{data.sem1_id}/excel')
    notes = [row[0] for row in openpyxl.load_workbook(io.BytesIO(response.data)).active.iter_rows(
        min_col=1, max_col=1, values_only=True) if row[0]]
    assert any('do not count towards attendance' in note for note in notes)


# ── Co-teachers ──────────────────────────────────────────────

def co_teacher(data):
    """A second MCA teacher with a login and no subjects of their own."""
    teacher = Teacher(name='Mitali Ghosh', email='mitali@rcciit.org.in', department_id=data.mca_id)
    db.session.add(teacher)
    db.session.flush()
    make_account('mitali@rcciit.org.in', 'teacher', department_id=data.mca_id, teacher_id=teacher.id)
    db.session.commit()
    return teacher


def test_hod_adds_a_co_teacher_and_it_is_logged(data, client_for):
    mitali = co_teacher(data)
    post(client_for(data.hod), f'/subjects/edit/{data.subject2_id}', subject_form(
        data, paper_code='MCA102', name='Networks', co_teacher_ids=[mitali.id, data.teacher_id]))
    subject = db.session.get(Subject, data.subject2_id)
    assert [t.name for t in subject.co_teachers] == ['Mitali Ghosh']  # the main teacher is not repeated
    assert 'co-teachers: Mitali Ghosh' in ActivityLog.query.filter_by(action='subject_updated').one().description
    assert client_for(data.hod).get(f'/api/subjects/{data.sem1_id}').get_json()[1]['co_teachers'] == [
        {'id': mitali.id, 'name': 'Mitali Ghosh'}]


def test_a_co_teacher_from_another_department_is_refused(data, client_for):
    other = Teacher.query.filter_by(email='ravi@rcciit.org.in').one()
    response = post(client_for(data.principal), f'/subjects/edit/{data.subject2_id}', subject_form(
        data, paper_code='MCA102', name='Networks', co_teacher_ids=[other.id]), follow_redirects=True)
    assert "Co-teachers must be teachers of the subject's department." in page(response)
    assert db.session.get(Subject, data.subject2_id).co_teachers == []


def test_a_co_teacher_runs_and_corrects_the_shared_class(data, client_for):
    mitali = co_teacher(data)
    subject = db.session.get(Subject, data.subject2_id)
    subject.co_teachers = [mitali]
    db.session.commit()
    main_teachers_session = add_session(data, subject_id=subject.id)  # started by Asha, the main teacher
    client = client_for('mitali@rcciit.org.in')

    form = {'class_mode': 'regular', 'department_id': data.mca_id, 'semester_id': data.sem1_id,
            'subject_id': subject.id, 'duration': 30}
    assert post(client, '/generate_qr', form).status_code == 302
    assert ClassSession.query.order_by(ClassSession.id.desc()).first().teacher_id == mitali.id

    assert client.get(f'/generate_qr?session_id={main_teachers_session.id}').status_code == 200
    post(client, f'/sessions/{main_teachers_session.id}/attendance', {'student_id': data.student_id, 'note': 'Dead phone'})
    assert Attendance.query.filter_by(session_id=main_teachers_session.id).count() == 1
    reports = page(client.get(f'/reports?department_id={data.mca_id}&semester_id={data.sem1_id}'))
    assert 'MCA102 - Networks' in reports

    # But not DBMS, which she does not teach.
    dbms = add_session(data)
    assert client.get(f'/generate_qr?session_id={dbms.id}').status_code == 403
    form['subject_id'] = data.subject_id
    assert post(client, '/generate_qr', form).status_code == 403


def test_co_teaching_counts_as_teaching_the_class(data, client_for):
    mitali = co_teacher(data)
    client = client_for('mitali@rcciit.org.in')
    assert client.get(f'/semesters/{data.sem1_id}/groups').status_code == 403
    db.session.get(Subject, data.subject2_id).co_teachers = [mitali]
    db.session.commit()
    assert client.get(f'/semesters/{data.sem1_id}/groups').status_code == 200
    assert post(client, f'/students/reset-device/{data.student_id}').status_code == 302  # may reset in a taught class


# ── Default group ────────────────────────────────────────────

def test_a_subject_can_be_only_for_one_group(data, client_for):
    group = ClassGroup(department_id=data.mca_id, semester_id=data.sem1_id, name='Elective A')
    other_semester_group = ClassGroup(department_id=data.mca_id, semester_id=data.sem2_id, name='Gr-1')
    db.session.add_all([group, other_semester_group])
    db.session.commit()
    hod = client_for(data.hod)

    post(hod, f'/subjects/edit/{data.subject2_id}', subject_form(data, paper_code='MCA102', name='Networks',
                                                                group_id=group.id))
    assert db.session.get(Subject, data.subject2_id).group_id == group.id
    assert hod.get(f'/api/subjects/{data.sem1_id}').get_json()[1]['group_id'] == group.id

    response = post(hod, f'/subjects/edit/{data.subject2_id}', subject_form(
        data, paper_code='MCA102', name='Networks', group_id=other_semester_group.id), follow_redirects=True)
    assert "The group must belong to the subject's semester." in page(response)


# ── Teacher short names ──────────────────────────────────────

def test_teacher_short_names_default_to_initials():
    assert teacher_short_name(Teacher(name='Dr. Sutapa Chatterjee')) == 'SC'
    assert teacher_short_name(Teacher(name='Prof. Mitali Ghosh Roy')) == 'MGR'
    assert teacher_short_name(Teacher(name='Asha', short_name='AK')) == 'AK'
    assert teacher_short_name(None) == ''


def test_hod_sets_a_teacher_short_name(data, client_for):
    form = {'name': 'Asha Teacher', 'email': 'asha@rcciit.org.in', 'department_id': data.mca_id, 'short_name': 'AT'}
    post(client_for(data.hod), f'/teachers/edit/{data.teacher_id}', form)
    assert db.session.get(Teacher, data.teacher_id).short_name == 'AT'
    response = post(client_for(data.hod), f'/teachers/edit/{data.teacher_id}', dict(form, short_name='TOO-LONG-NAME'),
                    follow_redirects=True)
    assert 'Keep the short name to 10 characters' in page(response)


def test_the_short_name_is_edited_with_the_teachers_account(data, client_for):
    from attendqr.models import User

    account = User.query.filter_by(username=data.teacher).one()
    form = {'username': data.teacher, 'teacher_name': 'Asha Teacher', 'department_id': data.mca_id,
            'teacher_short_name': 'AKT'}
    post(client_for(data.hod), f'/edit_admin/{account.id}', form)
    assert db.session.get(Teacher, data.teacher_id).short_name == 'AKT'
    assert 'AKT' in page(client_for(data.hod).get('/teachers'))
