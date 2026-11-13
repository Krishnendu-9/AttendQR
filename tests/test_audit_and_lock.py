"""The tamper-evident activity log, and locking a semester's attendance."""
import html

from attendqr.extensions import db
from attendqr.models import ActivityLog, AuditChain, LeaveRequest, Semester, SpotCheck, User
from attendqr.services.audit import verify_chain
from attendqr.services.qr import make_qr_token
from attendqr.utils import local_now
from conftest import add_session, post


def page(response):
    return html.unescape(response.get_data(as_text=True))


def logged_actions(client, data, count=3):
    """Make `count` logged changes through real requests (each seals its own entry)."""
    for n in range(count):
        post(client, '/semesters', {'name': f'Extra {n}', 'department_id': data.mca_id})


# ── Audit chain ──────────────────────────────────────────────

def test_entries_are_sealed_by_the_request_that_wrote_them(data, client_for):
    logged_actions(client_for(data.principal), data)

    entries = ActivityLog.query.order_by(ActivityLog.chain_seq).all()
    assert entries and all(e.chain_seq and len(e.entry_hash) == 64 for e in entries)
    check = verify_chain()
    assert check.ok and check.unsealed == 0 and check.entries == len(entries)
    head = db.session.get(AuditChain, 1)
    assert (head.last_seq, head.last_hash) == (entries[-1].chain_seq, entries[-1].entry_hash)


def test_changing_a_sealed_entry_is_detected(data, client_for):
    logged_actions(client_for(data.principal), data)
    entry = ActivityLog.query.filter(ActivityLog.action == 'semester_created').first()
    entry.description = 'Nothing happened here.'
    db.session.commit()

    check = verify_chain()
    assert not check.ok and f"Entry {entry.chain_seq}" in check.problems[0] and 'changed' in check.problems[0]


def test_deleting_a_sealed_entry_is_detected(data, client_for):
    logged_actions(client_for(data.principal), data)
    middle = ActivityLog.query.order_by(ActivityLog.chain_seq).all()[1]
    db.session.delete(middle)
    db.session.commit()

    assert any('missing' in problem for problem in verify_chain().problems)


def test_removing_the_newest_entries_is_detected_against_the_head(data, client_for):
    logged_actions(client_for(data.principal), data)
    db.session.delete(ActivityLog.query.order_by(ActivityLog.chain_seq.desc()).first())
    db.session.commit()

    assert any('newest entries were removed' in problem for problem in verify_chain().problems)


def test_deleting_an_account_does_not_look_like_tampering(data, client_for):
    hod = User.query.filter_by(username=data.hod).one()
    hod_id = hod.id
    logged_actions(client_for(data.hod), data, count=2)  # sign-in and changes, all with the HOD as actor
    written = ActivityLog.query.filter_by(actor_user_id=hod_id).count()
    assert written >= 3

    # Deleting the account sets actor_user_id to NULL on its entries (a foreign key cascade).
    post(client_for(data.principal), f'/delete_admin/{hod_id}')
    db.session.expire_all()
    assert db.session.get(User, hod_id) is None
    assert ActivityLog.query.filter_by(actor_username=data.hod, actor_user_id=None).count() == written
    assert verify_chain().ok


def test_entries_written_outside_requests_are_counted_until_sealed(app, data):
    db.session.add(ActivityLog(actor_username='script', action='x', description='d', created_at=local_now()))
    db.session.commit()
    assert verify_chain().unsealed >= 1

    result = app.test_cli_runner().invoke(args=['verify-audit', '--seal'])
    assert result.exit_code == 0 and 'intact' in result.output and 'Head: entry' in result.output
    assert verify_chain().unsealed == 0


def test_verify_audit_fails_on_tampering(app, data, client_for):
    logged_actions(client_for(data.principal), data)
    ActivityLog.query.filter(ActivityLog.chain_seq == 1).update({'action': 'nothing'})
    db.session.commit()

    result = app.test_cli_runner().invoke(args=['verify-audit'])
    assert result.exit_code == 1 and 'was changed after it was sealed' in result.output


# ── Semester lock ────────────────────────────────────────────

def lock(client, data, semester_id=None):
    return post(client, f'/semesters/{semester_id or data.sem1_id}/lock', follow_redirects=True)


def test_hod_locks_and_only_the_principal_unlocks_with_a_reason(data, client_for):
    hod, principal = client_for(data.hod), client_for(data.principal)
    assert post(client_for(data.teacher), f'/semesters/{data.sem1_id}/lock').status_code == 403

    assert 'is locked' in page(lock(hod, data))
    semester = db.session.get(Semester, data.sem1_id)
    assert semester.attendance_locked and semester.locked_by.username == data.hod

    assert post(hod, f'/semesters/{data.sem1_id}/unlock', {'note': 'x'}).status_code == 403
    assert 'short reason' in page(post(principal, f'/semesters/{data.sem1_id}/unlock', follow_redirects=True))
    post(principal, f'/semesters/{data.sem1_id}/unlock', {'note': 'Revaluation board correction'})
    db.session.expire_all()
    assert not semester.attendance_locked
    assert 'Revaluation board correction' in ActivityLog.query.filter_by(action='attendance_unlocked').one().description


def test_hod_cannot_lock_another_departments_semester(data, client_for):
    assert post(client_for(data.hod), f'/semesters/{data.other_sem_id}/lock').status_code == 403


def test_nothing_in_a_locked_semester_can_change(data, client_for):
    class_session = add_session(data, present=[data.student_id])
    entry_id = class_session.attendances[0].id
    leave = LeaveRequest(student_id=data.student2_id, start_date=local_now().date(), end_date=local_now().date(),
                         kind='medical', reason='Fever')
    db.session.add(leave)
    db.session.commit()
    hod = client_for(data.hod)
    lock(hod, data)

    refused = 'is locked'
    assert refused in page(post(hod, '/generate_qr', {'department_id': data.mca_id, 'semester_id': data.sem1_id,
                                                      'subject_id': data.subject_id, 'teacher_id': data.teacher_id,
                                                      'duration': 10}, follow_redirects=True))
    assert refused in page(post(hod, f'/sessions/{class_session.id}/attendance',
                                {'student_id': data.student2_id, 'note': 'phone died'}, follow_redirects=True))
    assert refused in page(post(hod, f'/sessions/{class_session.id}/attendance/{entry_id}/remove',
                                {'note': 'mistake'}, follow_redirects=True))
    assert refused in page(post(hod, f'/delete_session/{class_session.id}', {'note': 'x'}, follow_redirects=True))
    assert refused in page(post(hod, f'/sessions/{class_session.id}/spot-checks', follow_redirects=True))
    assert refused in page(post(client_for(data.principal), f'/leave/{leave.id}/decide', {'decision': 'approve'},
                                follow_redirects=True))
    scan = client_for(data.student).get(f'/scan/{make_qr_token(class_session.id)}')
    assert scan.status_code == 403 and 'Attendance Locked' in page(scan)

    db.session.expire_all()
    assert len(class_session.attendances) == 1 and SpotCheck.query.count() == 0 and leave.status == 'pending'
    assert db.session.get(Semester, data.sem2_id).attendance_locked is False  # other semesters are untouched


def test_the_session_view_shows_the_lock_instead_of_corrections(data, client_for):
    class_session = add_session(data, present=[data.student_id])
    hod = client_for(data.hod)
    lock(hod, data)
    text = page(hod.get(f'/generate_qr?session_id={class_session.id}'))
    assert 'Attendance locked' in text and 'Mark a student present' not in text
