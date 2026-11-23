"""Leave requests: students apply, the HOD or Principal decides, and approved leave is not counted."""
import html
import io
from datetime import timedelta

import openpyxl

from attendqr.extensions import db
from attendqr.models import ActivityLog, LeaveRequest, Student
from attendqr.services.reports import build_student_attendance_summary
from attendqr.utils import local_now, to_local
from conftest import add_session, post


def page(response):
    return html.unescape(response.get_data(as_text=True))


def today():
    return local_now().date()


def request_leave(client, start, end=None, kind='medical', reason='Fever'):
    return post(client, '/leave', {'kind': kind, 'start_date': start.isoformat(),
                                   'end_date': (end or start).isoformat(), 'reason': reason}, follow_redirects=True)


def approved_leave(student_id, start, end=None, status=LeaveRequest.APPROVED):
    leave = LeaveRequest(student_id=student_id, start_date=start, end_date=end or start, kind='medical',
                         reason='Fever', status=status)
    db.session.add(leave)
    db.session.commit()
    return leave


def session_day(class_session):
    """The college calendar day a session was held on."""
    return to_local(class_session.created_at).date()


# ── Students ─────────────────────────────────────────────────

def test_student_requests_leave_and_it_is_logged(data, client_for):
    response = request_leave(client_for(data.student), today(), today() + timedelta(days=1))

    assert 'Leave request sent' in page(response)
    leave = LeaveRequest.query.one()
    assert (leave.student_id, leave.status, leave.days) == (data.student_id, 'pending', 2)
    assert ActivityLog.query.filter_by(action='leave_requested', target_id=leave.id).count() == 1


def test_leave_request_is_validated(data, client_for):
    client = client_for(data.student)
    cases = [
        ({'start': today(), 'end': today() - timedelta(days=1)}, 'cannot be before the first day'),
        ({'start': today(), 'end': today() + timedelta(days=30)}, 'at most 30 days'),
        ({'start': today() - timedelta(days=45)}, 'up to 30 days ago'),
        ({'start': today(), 'reason': ' '}, 'short reason'),
        ({'start': today(), 'kind': 'holiday'}, 'type of leave'),
    ]
    for kwargs, message in cases:
        response = request_leave(client, **kwargs)
        assert response.status_code == 400, message
        assert message in page(response)
    assert LeaveRequest.query.count() == 0


def test_overlapping_requests_are_refused(data, client_for):
    client = client_for(data.student)
    request_leave(client, today(), today() + timedelta(days=2))
    response = request_leave(client, today() + timedelta(days=1))
    assert 'already have a pending request' in page(response)
    assert LeaveRequest.query.count() == 1


def test_student_can_withdraw_only_their_own_pending_request(data, client_for):
    mine = approved_leave(data.student_id, today(), status=LeaveRequest.PENDING)
    decided = approved_leave(data.student_id, today() + timedelta(days=5))
    theirs = approved_leave(data.student2_id, today(), status=LeaveRequest.PENDING)
    client = client_for(data.student)

    assert post(client, f'/leave/{theirs.id}/withdraw').status_code == 404
    assert 'Only a request that has not been decided' in page(post(client, f'/leave/{decided.id}/withdraw', follow_redirects=True))
    post(client, f'/leave/{mine.id}/withdraw')

    db.session.expire_all()
    assert {leave.id for leave in LeaveRequest.query} == {decided.id, theirs.id}


# ── Decisions ────────────────────────────────────────────────

def test_hod_approves_a_request_in_their_department(data, client_for):
    leave = approved_leave(data.student_id, today(), status=LeaveRequest.PENDING)
    hod = client_for(data.hod)

    assert 'Rahul Das' in page(hod.get('/leave'))
    post(hod, f'/leave/{leave.id}/decide', {'decision': 'approve', 'note': 'Get well soon'})

    db.session.expire_all()
    assert leave.status == 'approved' and leave.decided_by.username == data.hod and leave.decision_note == 'Get well soon'
    assert ActivityLog.query.filter_by(action='leave_approved').count() == 1


def test_rejecting_needs_a_note(data, client_for):
    leave = approved_leave(data.student_id, today(), status=LeaveRequest.PENDING)
    hod = client_for(data.hod)

    assert 'short reason for rejecting' in page(post(hod, f'/leave/{leave.id}/decide', {'decision': 'reject'}, follow_redirects=True))
    post(hod, f'/leave/{leave.id}/decide', {'decision': 'reject', 'note': 'No certificate'})
    db.session.expire_all()
    assert leave.status == 'rejected'


def test_decided_requests_cannot_be_decided_again(data, client_for):
    leave = approved_leave(data.student_id, today(), status=LeaveRequest.REJECTED)
    response = post(client_for(data.principal), f'/leave/{leave.id}/decide', {'decision': 'approve'}, follow_redirects=True)
    assert 'already been decided' in page(response)
    db.session.expire_all()
    assert leave.status == 'rejected'


def test_only_the_students_hod_or_the_principal_can_decide(data, client_for, app):
    from conftest import make_account

    other_hod = make_account('hod2@college.example', 'hod', department_id=data.other_id)
    db.session.commit()
    leave = approved_leave(data.student_id, today(), status=LeaveRequest.PENDING)

    assert post(client_for(other_hod.username), f'/leave/{leave.id}/decide', {'decision': 'approve'}).status_code == 403
    assert post(client_for(data.teacher), f'/leave/{leave.id}/decide', {'decision': 'approve'}).status_code == 403
    assert client_for(data.teacher).get('/leave').status_code == 403
    # Another department's HOD does not even see it.
    assert 'Rahul Das' not in page(client_for(other_hod.username).get('/leave'))
    post(client_for(data.principal), f'/leave/{leave.id}/decide', {'decision': 'approve'})
    db.session.expire_all()
    assert leave.status == 'approved'


# ── Reopening a decision ─────────────────────────────────────

def reopen(client, leave, note='Approved by mistake'):
    return post(client, f'/leave/{leave.id}/reopen', {'note': note}, follow_redirects=True)


def test_a_decision_can_be_reopened_with_a_reason_and_decided_again(data, client_for):
    leave = approved_leave(data.student_id, today())
    leave.decision_note = 'Get well soon'
    db.session.commit()
    hod = client_for(data.hod)
    assert f'/leave/{leave.id}/reopen' in page(hod.get('/leave'))

    assert 'waiting for a decision again' in page(reopen(hod, leave, 'Certificate not received'))
    db.session.expire_all()
    assert (leave.status, leave.decided_by_user_id, leave.decided_at, leave.decision_note) == ('pending', None, None, None)
    entry = ActivityLog.query.filter_by(action='leave_reopened', target_id=leave.id).one()
    assert 'which was approved' in entry.description and 'Certificate not received' in entry.description

    post(hod, f'/leave/{leave.id}/decide', {'decision': 'reject', 'note': 'No certificate'})
    db.session.expire_all()
    assert leave.status == 'rejected'


def test_reopening_an_approval_counts_the_classes_again(data, client_for):
    class_session = add_session(data, present=())
    leave = approved_leave(data.student_id, session_day(class_session))
    student = db.session.get(Student, data.student_id)
    assert build_student_attendance_summary(student)['overall_total'] == 0

    reopen(client_for(data.hod), leave)
    db.session.expire_all()
    assert build_student_attendance_summary(student)['overall_total'] == 1


def test_reopening_needs_a_reason_and_a_decided_request(data, client_for):
    hod = client_for(data.hod)
    decided = approved_leave(data.student_id, today())
    waiting = approved_leave(data.student_id, today() + timedelta(days=5), status=LeaveRequest.PENDING)

    assert 'short reason for reopening' in page(reopen(hod, decided, note=' '))
    assert 'still waiting for a decision' in page(reopen(hod, waiting))
    db.session.expire_all()
    assert decided.status == 'approved'
    assert ActivityLog.query.filter_by(action='leave_reopened').count() == 0


def test_a_rejection_is_not_reopened_over_a_newer_request_for_the_same_days(data, client_for):
    rejected = approved_leave(data.student_id, today(), today() + timedelta(days=2), status=LeaveRequest.REJECTED)
    approved_leave(data.student_id, today() + timedelta(days=1), status=LeaveRequest.PENDING)

    assert 'cannot be reopened' in page(reopen(client_for(data.hod), rejected))
    db.session.expire_all()
    assert rejected.status == 'rejected'


def test_only_the_students_hod_or_the_principal_can_reopen(data, client_for):
    from conftest import make_account

    other_hod = make_account('hod2@college.example', 'hod', department_id=data.other_id)
    db.session.commit()
    leave = approved_leave(data.student_id, today())

    assert post(client_for(other_hod.username), f'/leave/{leave.id}/reopen', {'note': 'x'}).status_code == 403
    assert post(client_for(data.teacher), f'/leave/{leave.id}/reopen', {'note': 'x'}).status_code == 403
    assert post(client_for(data.student), f'/leave/{leave.id}/reopen', {'note': 'x'}).status_code == 403
    reopen(client_for(data.principal), leave)
    db.session.expire_all()
    assert leave.status == 'pending'


def test_a_decision_in_a_locked_semester_cannot_be_reopened(data, client_for):
    leave = approved_leave(data.student_id, today())
    hod = client_for(data.hod)
    post(hod, f'/semesters/{data.sem1_id}/lock')

    assert f'/leave/{leave.id}/reopen' not in page(hod.get('/leave'))
    assert 'is locked' in page(reopen(hod, leave))
    db.session.expire_all()
    assert leave.status == 'approved'


# ── Effect on attendance ─────────────────────────────────────

def make_history(data):
    """Five DBMS classes on five days; Rahul attended the two most recent ones (40%)."""
    sessions = [add_session(data, days_ago=days, present=[data.student2_id] + ([data.student_id] if days < 2 else []))
                for days in range(5)]
    return sessions


def test_approved_leave_is_left_out_of_the_student_summary(data):
    sessions = make_history(data)
    student = db.session.get(Student, data.student_id)
    before = build_student_attendance_summary(student)
    assert (before['overall_attended'], before['overall_total'], before['excused']) == (2, 5, 0)

    approved_leave(data.student_id, session_day(sessions[4]), session_day(sessions[2]))
    after = build_student_attendance_summary(student)
    assert (after['overall_attended'], after['overall_total'], after['excused']) == (2, 2, 3)
    assert after['overall_percentage'] == 100


def test_pending_or_rejected_leave_changes_nothing(data):
    sessions = make_history(data)
    approved_leave(data.student_id, session_day(sessions[4]), session_day(sessions[2]), status=LeaveRequest.PENDING)
    approved_leave(data.student_id, session_day(sessions[1]), status=LeaveRequest.REJECTED)

    summary = build_student_attendance_summary(db.session.get(Student, data.student_id))
    assert (summary['overall_attended'], summary['overall_total'], summary['excused']) == (2, 5, 0)


def test_a_class_attended_during_leave_counts_neither_way(data):
    sessions = make_history(data)
    approved_leave(data.student_id, session_day(sessions[0]))  # Rahul was present that day anyway

    summary = build_student_attendance_summary(db.session.get(Student, data.student_id))
    assert (summary['overall_attended'], summary['overall_total'], summary['excused']) == (1, 4, 1)


def test_shortage_list_and_detailed_report_respect_approved_leave(data, client_for):
    sessions = make_history(data)
    client = client_for(data.hod)
    shortage_url = f'/reports/shortage?department_id={data.mca_id}&semester_id={data.sem1_id}'
    assert 'MCA001' in page(client.get(shortage_url))

    approved_leave(data.student_id, session_day(sessions[4]), session_day(sessions[2]))
    assert 'MCA001' not in page(client.get(shortage_url))

    detailed = page(client.get(f'/reports/detailed?department_id={data.mca_id}&semester_id={data.sem1_id}'))
    assert 'On approved leave; not counted' in detailed


def test_excel_export_marks_leave_and_explains_it(data, client_for):
    sessions = make_history(data)
    approved_leave(data.student_id, session_day(sessions[4]))

    response = client_for(data.hod).get(f'/export/{data.mca_id}/{data.sem1_id}/excel')
    sheet = openpyxl.load_workbook(io.BytesIO(response.data)).active
    values = [cell for row in sheet.iter_rows(values_only=True) for cell in row]
    rahul = next(row for row in sheet.iter_rows(values_only=True) if row[0] == 'MCA001')
    assert 'L' in rahul
    assert rahul[-2:] == (2, '50.0%')  # 2 of the 4 classes that count
    assert any(isinstance(v, str) and v.startswith('L = on approved leave') for v in values)


def test_student_dashboard_mentions_excused_classes(data, client_for):
    sessions = make_history(data)
    approved_leave(data.student_id, session_day(sessions[4]))

    text = page(client_for(data.student).get('/student/attendance'))
    assert '1 class during your approved leave is not counted' in text


def test_a_student_can_send_only_a_few_requests_a_week_withdrawn_ones_included(data, client_for):
    from attendqr.services.leave import MAX_REQUESTS_PER_PERIOD
    student = client_for(data.student)
    for offset in range(MAX_REQUESTS_PER_PERIOD):
        request_leave(student, today() + timedelta(days=offset * 2))
        leave = LeaveRequest.query.order_by(LeaveRequest.id.desc()).first()
        post(student, f'/leave/{leave.id}/withdraw')  # withdrawing does not give the request back
    assert LeaveRequest.query.count() == 0
    response = request_leave(student, today() + timedelta(days=40))
    assert 'the most allowed' in page(response)
    assert LeaveRequest.query.count() == 0


def test_approved_leave_keeps_its_classes_when_the_time_zone_changes(data, client_for):
    """Approval fixes the leave's window, so a later time zone change cannot move it onto other classes."""
    from datetime import datetime

    from attendqr.models import InstitutionSetting
    from attendqr.services.leave import LeaveWindows
    from attendqr.services.settings import forget_cached

    day = today() - timedelta(days=2)
    leave = LeaveRequest(student_id=data.student_id, start_date=day, end_date=day, kind='medical', reason='Fever')
    db.session.add(leave)
    db.session.commit()
    post(client_for(data.hod), f'/leave/{leave.id}/decide', {'decision': 'approve'})
    db.session.expire_all()
    # Asia/Kolkata is UTC+5:30: the day runs from 18:30 UTC the evening before.
    start = datetime.combine(day - timedelta(days=1), datetime.min.time()) + timedelta(hours=18, minutes=30)
    assert (leave.starts_at, leave.ends_at) == (start, start + timedelta(days=1))

    late_evening = start + timedelta(days=1, minutes=30)  # 19:00 UTC: past midnight in Kolkata, the next day
    db.session.add(InstitutionSetting(key='timezone', value='"UTC"'))
    db.session.commit()
    forget_cached()
    assert not LeaveWindows([data.student_id]).excused(data.student_id, late_evening)
    # Leave approved before windows were stored follows the zone set now, as it always did.
    leave.starts_at = leave.ends_at = None
    db.session.commit()
    assert LeaveWindows([data.student_id]).excused(data.student_id, late_evening)


def test_reopening_clears_the_window_until_it_is_approved_again(data, client_for):
    leave = LeaveRequest(student_id=data.student_id, start_date=today(), end_date=today(), kind='medical',
                         reason='Fever')
    db.session.add(leave)
    db.session.commit()
    hod = client_for(data.hod)
    post(hod, f'/leave/{leave.id}/decide', {'decision': 'approve'})
    post(hod, f'/leave/{leave.id}/reopen', {'note': 'Approved by mistake'})
    db.session.expire_all()
    assert leave.status == LeaveRequest.PENDING and leave.starts_at is None and leave.ends_at is None
