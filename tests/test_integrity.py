"""Integrity report: failed spot checks, buddy pairs, new phones and manual entries."""
from datetime import timedelta

from attendqr.extensions import db
from attendqr.models import Attendance, Department, Semester, SpotCheck
from attendqr.services.integrity import build_integrity_report
from conftest import add_session, make_student


def report_for(app, data):
    with app.test_request_context():
        return build_integrity_report(data.mca_id, data.sem1_id)


def scan(student_id, class_session, seconds=0, ip='10.0.0.5', new_device=False, source='scan'):
    db.session.add(Attendance(student_id=student_id, session_id=class_session.id, source=source,
                              timestamp=class_session.created_at + timedelta(seconds=seconds), scan_ip=ip,
                              new_device=new_device, note='n' if source == 'manual' else None))


def flags_of(report, roll_no):
    row = next((r for r in report.students if r.student.roll_no == roll_no), None)
    return [flag.kind for flag in row.flags] if row else []


def test_a_failed_spot_check_is_the_strongest_flag(app, data):
    class_session = add_session(data)
    db.session.add_all([
        SpotCheck(session_id=class_session.id, student_id=data.student_id, result='absent'),
        SpotCheck(session_id=class_session.id, student_id=data.student2_id, result='present'),
    ])
    db.session.commit()

    report = report_for(app, data)

    assert (report.checks, report.failed_checks) == (2, 1)
    assert report.students[0].student.roll_no == 'MCA001' and report.students[0].score == 5
    assert 'Not in the room at 1 of 1 spot check' in report.students[0].flags[0].text
    assert flags_of(report, 'MCA002') == []


def test_students_scanning_together_from_one_address_are_a_buddy_pair(app, data):
    for days in range(3):
        class_session = add_session(data, days_ago=days)
        scan(data.student_id, class_session, 10, ip='10.0.0.9')
        scan(data.student2_id, class_session, 18, ip='10.0.0.9')
    db.session.commit()

    report = report_for(app, data)

    assert [(p['first'].roll_no, p['second'].roll_no, p['together'], p['share']) for p in report.pairs] == \
        [('MCA001', 'MCA002', 3, 100)]
    assert flags_of(report, 'MCA001') == ['buddy'] and flags_of(report, 'MCA002') == ['buddy']


def test_different_addresses_far_apart_or_too_few_sessions_are_not_a_pair(app, data):
    for days, (ip2, gap) in enumerate([('10.0.0.8', 5), ('10.0.0.9', 120), ('10.0.0.9', 5)]):
        class_session = add_session(data, days_ago=days)
        scan(data.student_id, class_session, 0, ip='10.0.0.9')
        scan(data.student2_id, class_session, gap, ip=ip2)
    db.session.commit()

    assert report_for(app, data).pairs == []  # only one session matches both rules


def test_repeated_new_phones_and_bursts_are_flagged(app, data):
    department, semester = db.session.get(Department, data.mca_id), db.session.get(Semester, data.sem1_id)
    third = make_student('Third', 'MCA003', 'third@example.com', department, semester)
    first = add_session(data, days_ago=2)
    for n, student_id in enumerate((data.student_id, data.student2_id, third.id)):
        scan(student_id, first, n * 60, ip=f'10.0.0.{n + 1}', new_device=True)
    scan(data.student_id, add_session(data, days_ago=1), ip='10.0.0.1', new_device=True)
    db.session.commit()

    report = report_for(app, data)

    assert flags_of(report, 'MCA001') == ['new_device']
    assert [(b['session'].id, b['count'], b['addresses']) for b in report.bursts] == [(first.id, 3, 3)]


def test_frequent_manual_entries_are_listed(app, data):
    for days in range(3):
        scan(data.student2_id, add_session(data, days_ago=days), ip=None, source='manual')
    db.session.commit()
    assert flags_of(report_for(app, data), 'MCA002') == ['manual']


def test_page_is_for_the_hod_and_principal_of_that_class(data, client_for):
    url = f'/reports/integrity?department_id={data.mca_id}&semester_id={data.sem1_id}'
    assert 'Nothing unusual' in client_for(data.hod).get(url).get_data(as_text=True)
    assert client_for(data.principal).get(url).status_code == 200
    assert client_for(data.teacher).get(url).status_code == 403
    assert client_for(data.hod).get(
        f'/reports/integrity?department_id={data.other_id}&semester_id={data.other_sem_id}').status_code == 403
