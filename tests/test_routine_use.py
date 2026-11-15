"""Using the routine: dated classes, starting sessions, one-date changes, planned against held."""
import html
import io
from datetime import date, datetime, time, timedelta

import openpyxl
import pytest

from attendqr.extensions import db
from attendqr.models import (
    ActivityLog,
    Attendance,
    ClassGroup,
    ClassGroupMember,
    ClassSession,
    Holiday,
    RoutineChange,
    Student,
    Teacher,
    Timetable,
    TimetableEntry,
)
from attendqr.services.occurrences import CANCELLED, HELD, NOT_RECORDED, UPCOMING, occurrences
from attendqr.services.reports import (
    ReportFilters,
    build_attendance_matrix,
    build_shortage_report,
    build_student_attendance_summary,
)
from attendqr.utils import local_now, local_to_utc
from conftest import make_account, post

ALL_DAYS = (0, 1, 2, 3, 4, 5, 6)


def page(response):
    return html.unescape(response.get_data(as_text=True))


def at(clock_time):
    return clock_time.replace(second=0, microsecond=0)


@pytest.fixture
def every_day(app):
    app.config['WORKING_DAYS'] = ALL_DAYS
    return app


@pytest.fixture
def now():
    current = local_now()
    if current.hour == 0 or current.hour >= 22:
        pytest.skip("Needs an hour either side of now on the same day.")
    return current


def published(data, entries, start=None, semester_id=None):
    """A published routine for Semester 1 (or another) from `start` (default: four weeks ago)."""
    version = Timetable(department_id=data.mca_id, semester_id=semester_id or data.sem1_id, status='published',
                        effective_from=start or local_now().date() - timedelta(days=28))
    db.session.add(version)
    default_teachers = [db.session.get(Teacher, data.teacher_id)]
    for values in entries:
        teachers = values.pop('teachers', None)
        entry = TimetableEntry(timetable=version, **values)
        db.session.add(entry)
        entry.teachers = teachers if teachers is not None else default_teachers
    db.session.commit()
    return version


@pytest.fixture
def live(data, every_day, now):
    """DBMS today from 10 minutes ago to 40 minutes from now, every day of the week."""
    start = at((now - timedelta(minutes=10)).time())
    end = at((now + timedelta(minutes=40)).time())
    version = published(data, [{'weekday': d, 'start_time': start, 'end_time': end, 'subject_id': data.subject_id,
                                'room': '416'} for d in ALL_DAYS])
    return version


def today_occurrence(data, **kwargs):
    today = local_now().date()
    return next(o for o in occurrences(today, today, semester_ids=[data.sem1_id], **kwargs))


# ── Expanding the routine into dates ─────────────────────────

def test_a_weekly_class_happens_on_its_weekday(app, data):
    monday = date(2026, 11, 2)
    published(data, [{'weekday': 0, 'start_time': time(10), 'end_time': time(10, 50), 'subject_id': data.subject_id}],
              start=monday)
    found = occurrences(monday, monday + timedelta(days=20), semester_ids=[data.sem1_id], now=datetime(2026, 11, 1))
    assert [o.day for o in found] == [monday, monday + timedelta(days=7), monday + timedelta(days=14)]
    assert all(o.status == UPCOMING for o in found)


def test_holidays_odd_even_weeks_and_date_limits(app, data):
    monday = date(2026, 11, 2)
    published(data, [
        {'weekday': 0, 'start_time': time(10), 'end_time': time(11), 'title': 'Lab odd', 'week_pattern': 'odd'},
        {'weekday': 0, 'start_time': time(10), 'end_time': time(11), 'title': 'Lab even', 'week_pattern': 'even'},
        {'weekday': 1, 'start_time': time(10), 'end_time': time(11), 'title': 'Short course',
         'valid_from': monday + timedelta(days=8), 'valid_to': monday + timedelta(days=8)},
    ], start=monday)
    db.session.add(Holiday(start_date=monday + timedelta(days=14), end_date=monday + timedelta(days=14), title='Puja'))
    db.session.commit()
    found = occurrences(monday, monday + timedelta(days=20), semester_ids=[data.sem1_id], now=datetime(2026, 11, 1))
    seen = [(o.day - monday).days for o in found if o.status != CANCELLED]
    titles = [(o.day - monday).days and o.title for o in found]
    assert ('Lab odd' in titles) and seen == [0, 7, 8]
    assert [o.title for o in found if (o.day - monday).days == 0] == ['Lab odd']
    assert [o.title for o in found if (o.day - monday).days == 7] == ['Lab even']
    holiday = [o for o in found if o.status == CANCELLED]
    assert len(holiday) == 1 and holiday[0].status_label == 'Holiday: Puja'
    assert occurrences(monday, monday + timedelta(days=20), semester_ids=[data.sem1_id], include_cancelled=False,
                       now=datetime(2026, 11, 1)) != found


def test_sunday_counts_only_when_it_is_a_working_day(app, data):
    sunday = date(2026, 11, 8)
    published(data, [{'weekday': 6, 'start_time': time(9), 'end_time': time(10), 'title': 'Sunday batch'}],
              start=date(2026, 11, 2))
    assert occurrences(sunday, sunday, semester_ids=[data.sem1_id]) == []
    app.config['WORKING_DAYS'] = ALL_DAYS
    assert [o.title for o in occurrences(sunday, sunday, semester_ids=[data.sem1_id])] == ['Sunday batch']


def test_a_new_version_takes_over_mid_week(app, data):
    old = published(data, [{'weekday': d, 'start_time': time(10), 'end_time': time(11), 'title': 'Old'}
                           for d in range(7)], start=date(2026, 11, 2))
    old.effective_to = date(2026, 11, 4)
    published(data, [{'weekday': d, 'start_time': time(10), 'end_time': time(11), 'title': 'New'} for d in range(7)],
              start=date(2026, 11, 5))
    app.config['WORKING_DAYS'] = ALL_DAYS
    found = occurrences(date(2026, 11, 3), date(2026, 11, 6), semester_ids=[data.sem1_id])
    assert [(o.day.day, o.title) for o in found] == [(3, 'Old'), (4, 'Old'), (5, 'New'), (6, 'New')]


def test_a_student_sees_only_their_groups(app, data, every_day):
    gr1 = ClassGroup(department_id=data.mca_id, semester_id=data.sem1_id, name='Gr-1')
    gr2 = ClassGroup(department_id=data.mca_id, semester_id=data.sem1_id, name='Gr-2')
    db.session.add_all([gr1, gr2])
    db.session.flush()
    db.session.add(ClassGroupMember(group_id=gr1.id, student_id=data.student_id,
                                    joined_at=datetime(2026, 1, 1)))
    db.session.commit()
    day = date(2026, 11, 3)
    published(data, [
        {'weekday': day.weekday(), 'start_time': time(10), 'end_time': time(12), 'title': 'Lab', 'group_id': gr1.id},
        {'weekday': day.weekday(), 'start_time': time(10), 'end_time': time(12), 'title': 'Lab', 'group_id': gr2.id},
        {'weekday': day.weekday(), 'start_time': time(14), 'end_time': time(15), 'title': 'Lecture'},
    ], start=date(2026, 11, 2))
    rahul = db.session.get(Student, data.student_id)
    assert [(o.title, o.group.name if o.group else None) for o in occurrences(day, day, student=rahul)] == [
        ('Lab', 'Gr-1'), ('Lecture', None)]


# ── Starting sessions from the routine ───────────────────────

def test_a_teacher_starts_the_session_of_a_routine_class(data, client_for, live):
    teacher = client_for(data.teacher)
    dashboard = page(teacher.get('/'))
    assert "Today's classes" in dashboard and 'Start session' in dashboard and 'MCA101 - DBMS' in dashboard

    occurrence = today_occurrence(data)
    response = post(teacher, '/routine/occurrences/start', {'key': occurrence.key})
    session = ClassSession.query.one()
    assert response.headers['Location'].endswith(f'/generate_qr?session_id={session.id}')
    assert (session.timetable_entry_id, session.occurrence_date, session.teacher_id) == (
        occurrence.entry.id, occurrence.original_day, data.teacher_id)
    assert 20 <= (session.expires_at - session.created_at).total_seconds() / 60 <= 41
    assert today_occurrence(data).status == HELD

    # Starting again opens the same session.
    again = post(teacher, '/routine/occurrences/start', {'key': occurrence.key})
    assert again.headers['Location'].endswith(f'session_id={session.id}') and ClassSession.query.count() == 1
    assert 'Open session' in page(teacher.get('/'))


def test_only_its_teachers_and_the_hod_may_start_it(data, client_for, live):
    occurrence = today_occurrence(data)
    other = Teacher(name='Mitali Ghosh', email='mitali@rcciit.org.in', department_id=data.mca_id)
    db.session.add(other)
    db.session.flush()
    make_account('mitali@rcciit.org.in', 'teacher', department_id=data.mca_id, teacher_id=other.id)
    db.session.commit()
    assert post(client_for('mitali@rcciit.org.in'), '/routine/occurrences/start', {'key': occurrence.key}).status_code == 403
    assert post(client_for(data.hod), '/routine/occurrences/start', {'key': occurrence.key}).status_code == 302
    assert post(client_for(data.hod), '/routine/occurrences/start', {'key': 'entry-999-2026-01-01'}).status_code == 404


def test_a_class_far_ahead_cannot_be_started_yet(app, data, client_for, every_day, now):
    later = at((now + timedelta(minutes=45)).time())
    end = at((now + timedelta(minutes=90)).time()) if now.hour < 22 else time(23, 59)
    published(data, [{'weekday': now.weekday(), 'start_time': later, 'end_time': end, 'subject_id': data.subject_id}])
    occurrence = today_occurrence(data)
    response = post(client_for(data.teacher), '/routine/occurrences/start', {'key': occurrence.key},
                    follow_redirects=True)
    assert 'from 15 minutes before the class' in page(response) and ClassSession.query.count() == 0


def test_a_session_started_by_hand_holds_the_class_under_way(data, client_for, live):
    form = {'class_mode': 'regular', 'department_id': data.mca_id, 'semester_id': data.sem1_id,
            'subject_id': data.subject_id, 'duration': 30}
    post(client_for(data.teacher), '/generate_qr', form)
    session = ClassSession.query.one()
    assert session.timetable_entry_id == today_occurrence(data).entry.id
    assert '(the routine class)' in ActivityLog.query.filter_by(action='qr_session_created').one().description

    # Another subject is not in the routine now: unscheduled.
    form['subject_id'] = data.subject2_id
    post(client_for(data.teacher), '/generate_qr', form)
    networks = ClassSession.query.filter_by(subject_id=data.subject2_id).one()
    assert networks.timetable_entry_id is None
    assert 'Unscheduled' in page(client_for(data.teacher).get('/generate_qr'))


def test_unscheduled_is_only_said_where_a_routine_exists(data, client_for):
    form = {'class_mode': 'regular', 'department_id': data.mca_id, 'semester_id': data.sem1_id,
            'subject_id': data.subject_id, 'duration': 30}
    post(client_for(data.teacher), '/generate_qr', form)
    assert 'Unscheduled' not in page(client_for(data.teacher).get('/generate_qr'))


# ── My routine ───────────────────────────────────────────────

def test_my_routine_for_a_teacher_and_a_student(data, client_for, live):
    teacher_week = page(client_for(data.teacher).get('/routine/me'))
    assert 'My routine' in teacher_week and 'MCA101' in teacher_week
    student_week = page(client_for(data.student).get('/routine/me'))
    assert 'MCA101' in student_week and 'Start session' not in student_week
    assert 'Today' in page(client_for(data.student).get('/'))
    assert client_for(data.hod).get('/routine/me').status_code == 403


# ── One-date changes ─────────────────────────────────────────

def test_a_teacher_cancels_their_own_class_with_a_reason(data, client_for, live):
    occurrence = today_occurrence(data)
    teacher = client_for(data.teacher)
    response = post(teacher, '/routine/occurrences/change', {'key': occurrence.key, 'kind': 'cancel'},
                    follow_redirects=True)
    assert 'Give a reason' in page(response) and RoutineChange.query.count() == 0

    post(teacher, '/routine/occurrences/change', {'key': occurrence.key, 'kind': 'cancel', 'reason': 'Unwell'})
    change = RoutineChange.query.one()
    assert (change.kind, change.reason, change.date) == ('cancel', 'Unwell', occurrence.original_day)
    assert today_occurrence(data).status == CANCELLED
    assert 'Cancelled MCA101' in ActivityLog.query.filter_by(action='routine_cancel').one().description
    assert 'Cancelled: Unwell' in page(client_for(data.student).get('/'))
    assert post(teacher, '/routine/occurrences/start', {'key': occurrence.key}, follow_redirects=True).status_code == 200
    assert ClassSession.query.count() == 0

    # Undo puts it back.
    post(teacher, f'/routine/changes/{change.id}/undo')
    assert RoutineChange.query.count() == 0 and today_occurrence(data).status != CANCELLED


def test_the_setting_can_stop_teachers_changing_classes(data, client_for, live):
    post(client_for(data.principal), '/settings', {'teachers_change_own_classes': ['0']})
    occurrence = today_occurrence(data)
    response = post(client_for(data.teacher), '/routine/occurrences/change',
                    {'key': occurrence.key, 'kind': 'cancel', 'reason': 'Unwell'}, follow_redirects=True)
    assert 'cannot be changed that way' in page(response) and RoutineChange.query.count() == 0
    post(client_for(data.hod), '/routine/occurrences/change', {'key': occurrence.key, 'kind': 'cancel', 'reason': 'Exam'})
    assert RoutineChange.query.count() == 1


def test_a_class_is_moved_to_another_time(data, client_for, every_day):
    day = local_now().date() + timedelta(days=2)
    published(data, [{'weekday': day.weekday(), 'start_time': time(10), 'end_time': time(11), 'subject_id': data.subject_id},
                     {'weekday': day.weekday(), 'start_time': time(14), 'end_time': time(15), 'title': 'Seminar'}])
    target = next(o for o in occurrences(day, day, semester_ids=[data.sem1_id]) if o.subject)
    hod = client_for(data.hod)
    clash = post(hod, '/routine/occurrences/change', {'key': target.key, 'kind': 'move', 'reason': 'Hall booked',
                                                      'new_date': day.isoformat(), 'new_start_time': '14:30',
                                                      'new_end_time': '15:30'}, follow_redirects=True)
    assert 'The class already has 14:00-15:00 Seminar' in page(clash)
    post(hod, '/routine/occurrences/change', {'key': target.key, 'kind': 'move', 'reason': 'Hall booked',
                                              'new_date': (day + timedelta(days=1)).isoformat(),
                                              'new_start_time': '12:00', 'new_end_time': '13:00'})
    assert [o.title for o in occurrences(day, day, semester_ids=[data.sem1_id])] == ['Seminar']
    moved = occurrences(day + timedelta(days=1), day + timedelta(days=1), semester_ids=[data.sem1_id])
    assert [(o.subject.id, o.start_time) for o in moved if o.subject] == [(data.subject_id, time(12))]
    assert moved[0].status_label.startswith('Moved from')


def test_a_substitute_takes_and_runs_the_class(data, client_for, live):
    substitute = Teacher(name='Mitali Ghosh', email='mitali@rcciit.org.in', department_id=data.mca_id)
    db.session.add(substitute)
    db.session.flush()
    make_account('mitali@rcciit.org.in', 'teacher', department_id=data.mca_id, teacher_id=substitute.id)
    db.session.commit()
    occurrence = today_occurrence(data)
    assert post(client_for(data.teacher), '/routine/occurrences/change',
                {'key': occurrence.key, 'kind': 'substitute', 'substitute_teacher_id': substitute.id,
                 'reason': 'On leave'}, follow_redirects=True).status_code == 200
    assert RoutineChange.query.count() == 0  # teachers cannot assign substitutes

    post(client_for(data.hod), '/routine/occurrences/change',
         {'key': occurrence.key, 'kind': 'substitute', 'substitute_teacher_id': substitute.id, 'reason': 'On leave'})
    assert [t.name for t in today_occurrence(data).teachers] == ['Mitali Ghosh']
    mitali = client_for('mitali@rcciit.org.in')
    assert 'MCA101' in page(mitali.get('/routine/me'))
    post(mitali, '/routine/occurrences/start', {'key': occurrence.key})
    assert ClassSession.query.one().teacher_id == substitute.id
    # Asha's own week no longer has today's class: it is Mitali's today.
    asha_today = occurrences(occurrence.day, occurrence.day, teacher_id=data.teacher_id)
    assert occurrence.key not in [o.key for o in asha_today]


def test_an_extra_class_is_added_and_can_be_held(data, client_for, every_day, now):
    hod = client_for(data.hod)
    start = at((now - timedelta(minutes=5)).time())
    end = at((now + timedelta(minutes=30)).time())
    post(hod, f'/routine/class/{data.sem1_id}/extra', {
        'date': now.date().isoformat(), 'start_time': start.strftime('%H:%M'), 'end_time': end.strftime('%H:%M'),
        'subject_id': data.subject2_id, 'reason': 'Make-up class'})
    change = RoutineChange.query.one()
    assert change.kind == 'extra'
    extra = today_occurrence(data)
    assert extra.status_label.endswith('extra class') and [t.id for t in extra.teachers] == [data.teacher_id]
    post(client_for(data.teacher), '/routine/occurrences/start', {'key': extra.key})
    db.session.refresh(change)
    assert change.session_id == ClassSession.query.one().id
    assert today_occurrence(data).status == HELD


def test_changes_are_refused_in_a_locked_semester(data, client_for, live):
    occurrence = today_occurrence(data)
    post(client_for(data.hod), f'/semesters/{data.sem1_id}/lock')
    post(client_for(data.hod), '/routine/occurrences/change', {'key': occurrence.key, 'kind': 'cancel', 'reason': 'x'})
    assert RoutineChange.query.count() == 0


def test_the_class_week_lists_each_class_with_actions(data, client_for, live):
    shown = page(client_for(data.hod).get(f'/routine/class/{data.sem1_id}/week'))
    assert 'MCA101 - DBMS' in shown and 'Cancel' in shown and 'Substitute' in shown and 'Extra class' in shown
    assert client_for(data.other_teacher).get(f'/routine/class/{data.sem1_id}/week').status_code == 403


# ── Planned and held ─────────────────────────────────────────

def past_routine(data, every_day_app):
    """Three days ago, two days ago and yesterday at 10:00: one held, one cancelled, one not recorded."""
    today = local_now().date()
    published(data, [{'weekday': d, 'start_time': time(10), 'end_time': time(11), 'subject_id': data.subject_id}
                     for d in ALL_DAYS], start=today - timedelta(days=3))
    held_day, cancelled_day = today - timedelta(days=3), today - timedelta(days=2)
    entry = TimetableEntry.query.filter_by(weekday=held_day.weekday()).one()
    started = local_to_utc(datetime.combine(held_day, time(10, 2)))
    session = ClassSession(subject_id=data.subject_id, teacher_id=data.teacher_id, created_at=started,
                           expires_at=started + timedelta(minutes=50), timetable_entry_id=entry.id,
                           occurrence_date=held_day)
    db.session.add(session)
    db.session.flush()
    db.session.add(Attendance(student_id=data.student_id, session_id=session.id, timestamp=started))
    cancelled_entry = TimetableEntry.query.filter_by(weekday=cancelled_day.weekday()).one()
    db.session.add(RoutineChange(kind='cancel', timetable_entry_id=cancelled_entry.id, date=cancelled_day,
                                 department_id=data.mca_id, semester_id=data.sem1_id, subject_id=data.subject_id,
                                 reason='Exam duty'))
    db.session.commit()
    return today


def test_planned_and_held_counts_each_class(data, client_for, every_day):
    today = past_routine(data, every_day)
    args = f'semester_id={data.sem1_id}&date_from={(today - timedelta(days=3)).isoformat()}&date_to={(today - timedelta(days=1)).isoformat()}'
    shown = page(client_for(data.hod).get(f'/reports/routine?{args}'))
    assert 'Not recorded' in shown and 'Exam duty' in shown and 'MCA101 - DBMS' in shown
    found = occurrences(today - timedelta(days=3), today - timedelta(days=1), semester_ids=[data.sem1_id])
    assert sorted(o.status for o in found) == sorted([HELD, CANCELLED, NOT_RECORDED])

    from attendqr.services.planned import summarise
    overall, subjects, teachers = summarise(found)
    assert [s.label for s in subjects] == ['MCA101 - DBMS']
    assert (overall.planned, overall.held, overall.cancelled, overall.not_recorded, overall.held_rate) == (3, 1, 1, 1, 50)
    assert teachers[0].label == 'Asha Teacher'

    response = client_for(data.hod).get(f'/reports/routine/export?{args}')
    sheet = openpyxl.load_workbook(io.BytesIO(response.data)).active
    assert [cell.value for cell in sheet[1]][:3] == ['Date', 'Day', 'Starts']
    statuses = {row[9] for row in sheet.iter_rows(min_row=2, values_only=True) if row[0] and row[9]}
    assert {'Held', 'Not recorded', 'Cancelled'} <= statuses

    teacher_view = page(client_for(data.teacher).get(f'/reports/routine?date_from={(today - timedelta(days=3)).isoformat()}'))
    assert 'MCA101 - DBMS' in teacher_view
    assert client_for(data.student).get('/reports/routine').status_code == 403
    from attendqr.services.routine import week_start
    if today - timedelta(days=1) >= week_start(today):  # yesterday's missed class is in this week
        assert 'not recorded this week' in page(client_for(data.hod).get('/'))


def test_classes_never_recorded_count_for_nobody(app, data, every_day):
    """The rule that never changes: a planned class without a session is in no student's numbers."""
    rahul = db.session.get(Student, data.student_id)
    filters = ReportFilters(department_id=data.mca_id, semester_id=data.sem1_id, subject_id=None, parsed_from=None,
                            parsed_to=None, date_from='', date_to='', error=None)

    def numbers():
        summary = build_student_attendance_summary(rahul)
        with app.test_request_context():
            _, rows = build_attendance_matrix(filters)
            shortage = build_shortage_report(filters, 80)
        row = next(r for r in rows if r['student'].id == rahul.id)
        return (summary['overall_attended'], summary['overall_total'], row['total'], row['counted'],
                len(shortage['rows']), shortage['classes_held'])

    past_routine(data, every_day)
    before = numbers()
    # Ten more weeks of routine classes, none recorded: nothing moves.
    published(data, [{'weekday': d, 'start_time': time(12), 'end_time': time(13), 'subject_id': data.subject2_id}
                     for d in ALL_DAYS], start=local_now().date() - timedelta(days=70), semester_id=data.sem2_id)
    assert numbers() == before
    assert before[:4] == (1, 1, 1, 1)  # Rahul: the one held class


# ── Limits on changes (review fixes) ─────────────────────────

def test_a_teacher_cannot_cancel_a_class_that_has_passed(data, client_for, every_day):
    """Cancelling a missed class afterwards would hide it from planned vs held; only the HOD may."""
    yesterday = local_now().date() - timedelta(days=1)
    published(data, [{'weekday': yesterday.weekday(), 'start_time': time(10), 'end_time': time(11),
                      'subject_id': data.subject_id}])
    missed = next(o for o in occurrences(yesterday, yesterday, semester_ids=[data.sem1_id]))
    assert missed.status == NOT_RECORDED

    response = post(client_for(data.teacher), '/routine/occurrences/change',
                    {'key': missed.key, 'kind': 'cancel', 'reason': 'Was unwell'}, follow_redirects=True)
    assert 'cannot be changed that way' in page(response) and RoutineChange.query.count() == 0

    post(client_for(data.hod), '/routine/occurrences/change', {'key': missed.key, 'kind': 'cancel', 'reason': 'Strike'})
    assert RoutineChange.query.one().kind == 'cancel'


def test_a_class_cannot_be_moved_into_the_past_or_onto_a_holiday(data, client_for, every_day):
    day = local_now().date() + timedelta(days=2)
    published(data, [{'weekday': day.weekday(), 'start_time': time(10), 'end_time': time(11), 'subject_id': data.subject_id}])
    target = next(o for o in occurrences(day, day, semester_ids=[data.sem1_id]))
    holiday_day = day + timedelta(days=1)
    db.session.add(Holiday(start_date=holiday_day, end_date=holiday_day, title='Foundation Day'))
    db.session.commit()
    hod = client_for(data.hod)

    def move_to(new_day):
        return page(post(hod, '/routine/occurrences/change', {
            'key': target.key, 'kind': 'move', 'reason': 'Hall booked', 'new_date': new_day.isoformat(),
            'new_start_time': '12:00', 'new_end_time': '13:00'}, follow_redirects=True))

    assert 'already passed' in move_to(local_now().date() - timedelta(days=1))
    assert 'is a holiday (Foundation Day)' in move_to(holiday_day)
    assert RoutineChange.query.count() == 0


def test_a_moved_class_takes_a_holiday_declared_later(data, client_for, every_day):
    day = local_now().date() + timedelta(days=2)
    published(data, [{'weekday': day.weekday(), 'start_time': time(10), 'end_time': time(11), 'subject_id': data.subject_id}])
    target = next(o for o in occurrences(day, day, semester_ids=[data.sem1_id]))
    new_day = day + timedelta(days=1)
    post(client_for(data.hod), '/routine/occurrences/change', {
        'key': target.key, 'kind': 'move', 'reason': 'Hall booked', 'new_date': new_day.isoformat(),
        'new_start_time': '12:00', 'new_end_time': '13:00'})
    assert RoutineChange.query.one().kind == 'move'

    db.session.add(Holiday(start_date=new_day, end_date=new_day, title='Rain'))
    db.session.commit()
    moved = next(o for o in occurrences(new_day, new_day, semester_ids=[data.sem1_id]) if o.change is not None)
    assert moved.status == CANCELLED and moved.status_label == 'Holiday: Rain'


def test_a_replacement_routine_cannot_start_in_the_past(app, data):
    from attendqr.models import User
    from attendqr.services.routine import RoutineError, publish, start_draft

    today = local_now().date()
    published(data, [{'weekday': 0, 'start_time': time(10), 'end_time': time(11), 'subject_id': data.subject_id}],
              start=today - timedelta(days=28))
    semester = Timetable.query.one().semester
    user = User.query.filter_by(username=data.hod).one()
    draft, _ = start_draft(semester, user)
    with pytest.raises(RoutineError, match='cannot start in the past'):
        publish(draft, today - timedelta(days=7), user)
    db.session.rollback()  # as the publish page does
    db.session.expunge_all()

    semester = Timetable.query.one().semester
    user = User.query.filter_by(username=data.hod).one()
    draft, _ = start_draft(semester, user)
    previous = publish(draft, today, user)
    assert previous.effective_to == today - timedelta(days=1)
