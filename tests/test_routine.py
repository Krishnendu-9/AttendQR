"""The class routine: bell schedules, holidays, drafts, clash checks, publishing and printing."""
import html
from datetime import date, time, timedelta

import pytest

from attendqr.extensions import db
from attendqr.models import (
    ActivityLog,
    BellSchedule,
    ClassGroup,
    ClassGroupMember,
    Holiday,
    Semester,
    Subject,
    Timetable,
    TimetableEntry,
)
from attendqr.services.routine import runs_in_week, schedule_for_day, week_number
from attendqr.utils import local_now
from conftest import make_account, post


def page(response):
    return html.unescape(response.get_data(as_text=True))


def weekday_schedule(client, name='Weekday', scope='institution', days=(0, 1, 2, 3, 4)):
    """A bell schedule like the photo's: 50-minute periods from 10:00, a break 1:20-2:10."""
    post(client, '/calendar/schedules', {'name': name, 'scope': scope})
    schedule = BellSchedule.query.order_by(BellSchedule.id.desc()).first()
    rows = [('1', '10:00', '10:50'), ('2', '10:50', '11:40'), ('3', '11:40', '12:30'), ('4', '12:30', '13:20'),
            ('Break', '13:20', '14:10'), ('5', '14:10', '15:00'), ('6', '15:00', '15:50')]
    post(client, f'/calendar/schedules/{schedule.id}', {
        'name': name, 'label': [r[0] for r in rows], 'start': [r[1] for r in rows], 'end': [r[2] for r in rows],
        'is_break': ['4'], 'weekday': [str(d) for d in days],
    })
    return schedule


def open_draft(client, data, semester_id=None):
    post(client, f'/routine/class/{semester_id or data.sem1_id}/draft')
    return Timetable.query.filter_by(semester_id=semester_id or data.sem1_id, status='draft').one()


def add(client, draft, **values):
    form = {'weekday': '0', 'start_time': '10:00', 'end_time': '10:50', 'week_pattern': 'every'}
    form.update(values)
    return post(client, f'/routine/drafts/{draft.id}/entries', form, follow_redirects=True)


def publish(client, draft, day):
    return post(client, f'/routine/drafts/{draft.id}/publish', {'effective_from': day.isoformat()},
                follow_redirects=True)


@pytest.fixture
def hod(data, client_for):
    return client_for(data.hod)


# ── Bell schedules and holidays ──────────────────────────────

def test_a_bell_schedule_gets_periods_and_days(data, client_for):
    principal = client_for(data.principal)
    schedule = weekday_schedule(principal)
    assert [p.label for p in schedule.periods] == ['1', '2', '3', '4', 'Break', '5', '6']
    assert [p.is_break for p in schedule.periods].count(True) == 1
    assert sorted(d.weekday for d in schedule.days) == [0, 1, 2, 3, 4]
    assert schedule_for_day(data.mca_id, 0).id == schedule.id
    assert schedule_for_day(data.mca_id, 5) is None
    shown = page(principal.get('/calendar'))
    assert 'Weekday' in shown and '13:20-14:10' in shown


def test_a_departments_own_schedule_comes_first(data, client_for):
    institution = weekday_schedule(client_for(data.principal))
    own = weekday_schedule(client_for(data.hod), 'MCA Monday', scope=str(data.mca_id), days=(0,))
    assert schedule_for_day(data.mca_id, 0).id == own.id
    assert schedule_for_day(data.mca_id, 1).id == institution.id
    assert schedule_for_day(data.other_id, 0).id == institution.id


def test_a_day_moves_between_schedules_of_one_scope(data, client_for):
    principal = client_for(data.principal)
    weekday = weekday_schedule(principal)
    saturday = weekday_schedule(principal, 'Saturday', days=(4, 5))
    assert sorted(d.weekday for d in db.session.get(BellSchedule, weekday.id).days) == [0, 1, 2, 3]
    assert sorted(d.weekday for d in saturday.days) == [4, 5]


@pytest.mark.parametrize(('rows', 'message'), [
    ((('1', '10:00', '10:50'), ('2', '10:40', '11:30')), 'overlaps'),
    ((('1', '11:00', '10:50'),), 'must end after it starts'),
    ((('1', '10:00', ''),), 'enter a start and an end time'),
    ((), 'Add at least one period'),
])
def test_bad_periods_are_refused(data, client_for, rows, message):
    principal = client_for(data.principal)
    post(principal, '/calendar/schedules', {'name': 'Weekday', 'scope': 'institution'})
    schedule = BellSchedule.query.one()
    response = post(principal, f'/calendar/schedules/{schedule.id}', {
        'name': 'Weekday', 'label': [r[0] for r in rows], 'start': [r[1] for r in rows], 'end': [r[2] for r in rows]})
    assert response.status_code == 400 and message in page(response)


def test_who_may_change_schedules_and_holidays(data, client_for):
    institution = weekday_schedule(client_for(data.principal))
    hod = client_for(data.hod)
    assert hod.get(f'/calendar/schedules/{institution.id}').status_code == 403
    assert post(hod, '/calendar/schedules', {'name': 'Theirs', 'scope': str(data.other_id)}).status_code == 403
    assert post(hod, '/calendar/holidays', {'title': 'X', 'start_date': '2026-12-25', 'scope': 'institution'}
                ).status_code == 302  # an HOD's holiday is for their own department
    assert Holiday.query.one().department_id == data.mca_id
    assert client_for(data.teacher).get('/calendar').status_code == 403


def test_holidays_are_added_and_removed(data, client_for):
    principal = client_for(data.principal)
    post(principal, '/calendar/holidays', {'title': 'Durga Puja', 'start_date': '2026-10-17',
                                           'end_date': '2026-10-21', 'scope': 'institution'})
    holiday = Holiday.query.one()
    assert (holiday.start_date, holiday.end_date, holiday.department_id) == (date(2026, 10, 17), date(2026, 10, 21), None)
    assert "Added holiday 'Durga Puja'" in ActivityLog.query.filter_by(action='holiday_added').one().description
    response = post(principal, '/calendar/holidays', {'title': 'Bad', 'start_date': '2026-10-21',
                                                      'end_date': '2026-10-17'}, follow_redirects=True)
    assert 'cannot be before the first day' in page(response)
    post(principal, f'/calendar/holidays/{holiday.id}/delete')
    assert Holiday.query.count() == 0


# ── Drafts and clashes ───────────────────────────────────────

def test_hod_builds_a_draft_routine(data, hod):
    draft = open_draft(hod, data)
    add(hod, draft, subject_id=data.subject_id, room='416')
    add(hod, draft, weekday='6', start_time='09:00', end_time='10:00', title='Special Tutorial Class')  # Sunday
    entries = TimetableEntry.query.order_by(TimetableEntry.weekday).all()
    assert [(e.weekday, e.subject_id, e.title) for e in entries] == [(0, data.subject_id, None),
                                                                      (6, None, 'Special Tutorial Class')]
    assert [t.id for t in entries[0].teachers] == [data.teacher_id]  # the subject's teacher by default
    shown = page(hod.get(f'/routine/class/{data.sem1_id}/draft'))
    assert 'MCA101' in shown and 'Special Tutorial Class' in shown and 'Sunday' in shown
    assert "Started a draft routine" in ActivityLog.query.filter_by(action='routine_draft_created').one().description


def test_an_entry_needs_a_subject_or_a_title_and_sane_times(data, hod):
    draft = open_draft(hod, data)
    assert 'Choose a subject, or enter a title' in page(add(hod, draft))
    assert 'must end after it starts' in page(add(hod, draft, title='X', start_time='11:00', end_time='10:00'))
    other_subject = Subject.query.filter(Subject.semester_id != data.sem1_id).first() or Subject(
        name='Other', paper_code='MCA201', department_id=data.mca_id, semester_id=data.sem2_id)
    db.session.add(other_subject)
    db.session.commit()
    assert 'Choose a subject of this class' in page(add(hod, draft, subject_id=other_subject.id))
    assert TimetableEntry.query.count() == 0


def test_the_same_teacher_cannot_be_in_two_places(data, hod):
    draft = open_draft(hod, data)
    add(hod, draft, subject_id=data.subject_id)
    group = ClassGroup(department_id=data.mca_id, semester_id=data.sem1_id, name='Gr-1')
    db.session.add(group)
    db.session.commit()
    response = add(hod, draft, subject_id=data.subject2_id, start_time='10:30', end_time='11:20', group_id=group.id)
    assert 'Asha Teacher also teaches Mon 10:00-10:50 MCA101' in page(response)
    assert TimetableEntry.query.count() == 1


def test_a_whole_class_entry_cannot_share_its_time(data, hod):
    draft = open_draft(hod, data)
    add(hod, draft, title='Seminar')
    response = add(hod, draft, title='Library', start_time='10:20', end_time='11:00')
    assert 'a whole-class class cannot share its time' in page(response)


def test_two_groups_run_side_by_side_but_a_group_not_twice(data, hod):
    gr1 = ClassGroup(department_id=data.mca_id, semester_id=data.sem1_id, name='Gr-1')
    gr2 = ClassGroup(department_id=data.mca_id, semester_id=data.sem1_id, name='Gr-2')
    db.session.add_all([gr1, gr2])
    db.session.commit()
    draft = open_draft(hod, data)
    add(hod, draft, title='Lab A', group_id=gr1.id, end_time='12:30')
    add(hod, draft, title='Lab B', group_id=gr2.id, end_time='12:30')
    assert TimetableEntry.query.count() == 2
    response = add(hod, draft, title='Lab C', group_id=gr1.id, start_time='11:00', end_time='11:30')
    assert 'Gr-1 already has Mon 10:00-12:30 Lab A (Gr-1)' in page(response)
    assert TimetableEntry.query.count() == 2

    # Editing an entry checks it against the others, but not against itself.
    lab_b = next(e for e in draft.entries if e.title == 'Lab B')
    post(hod, f'/routine/entries/{lab_b.id}', {'weekday': '0', 'start_time': '10:00', 'end_time': '13:00',
                                              'title': 'Lab B', 'group_id': gr2.id, 'week_pattern': 'every'})
    assert db.session.get(TimetableEntry, lab_b.id).end_time == time(13, 0)


def test_shared_members_warn_and_save_anyway_saves(data, hod):
    gr1 = ClassGroup(department_id=data.mca_id, semester_id=data.sem1_id, name='Gr-1')
    elective = ClassGroup(department_id=data.mca_id, semester_id=data.sem1_id, name='Elective')
    db.session.add_all([gr1, elective])
    db.session.flush()
    for group in (gr1, elective):
        db.session.add(ClassGroupMember(group_id=group.id, student_id=data.student_id))
    db.session.commit()
    draft = open_draft(hod, data)
    add(hod, draft, title='Lab', group_id=gr1.id)
    response = add(hod, draft, title='Cloud', group_id=elective.id)
    assert '1 student(s) are in both Elective and Gr-1' in page(response) and TimetableEntry.query.count() == 1
    add(hod, draft, title='Cloud', group_id=elective.id, confirm_overlap='1')
    assert TimetableEntry.query.count() == 2


def test_odd_and_even_weeks_do_not_clash(data, hod):
    draft = open_draft(hod, data)
    add(hod, draft, title='Lab odd', week_pattern='odd')
    add(hod, draft, title='Lab even', week_pattern='even')
    assert TimetableEntry.query.count() == 2
    assert 'cannot share its time' in page(add(hod, draft, title='Lab every'))


def test_a_room_cannot_hold_two_classes(data, hod):
    gr1 = ClassGroup(department_id=data.mca_id, semester_id=data.sem1_id, name='Gr-1')
    gr2 = ClassGroup(department_id=data.mca_id, semester_id=data.sem1_id, name='Gr-2')
    db.session.add_all([gr1, gr2])
    db.session.commit()
    draft = open_draft(hod, data)
    add(hod, draft, title='Lab A', group_id=gr1.id, room='Lab 3')
    assert 'Room lab 3 is taken by' in page(add(hod, draft, title='Lab B', group_id=gr2.id, room='lab 3'))


def test_a_teacher_busy_in_another_classes_routine_is_refused(data, hod):
    draft = open_draft(hod, data, data.sem2_id)
    sem2_subject = Subject(name='Compilers', paper_code='MCA201', department_id=data.mca_id, semester_id=data.sem2_id,
                           teacher_id=data.teacher_id)
    db.session.add(sem2_subject)
    db.session.commit()
    add(hod, draft, subject_id=sem2_subject.id)
    publish(hod, draft, local_now().date())

    sem1 = open_draft(hod, data)
    response = add(hod, sem1, subject_id=data.subject_id)
    assert 'Asha Teacher also teaches Mon 10:00-10:50 MCA201 in ' in page(response)
    assert 'Semester 2' in page(response).split('also teaches')[1]


# ── Publishing and versions ──────────────────────────────────

def test_publishing_replaces_the_routine_from_a_date(data, hod):
    today = local_now().date()
    draft = open_draft(hod, data)
    add(hod, draft, subject_id=data.subject_id)
    publish(hod, draft, today)
    first = db.session.get(Timetable, draft.id)
    assert (first.status, first.effective_from, first.effective_to) == ('published', today, None)

    # Published versions are never edited; "Edit routine" makes a draft copy.
    entry = first.entries[0]
    assert post(hod, f'/routine/entries/{entry.id}', {'weekday': '1'}).status_code == 404
    second = open_draft(hod, data)
    assert len(second.entries) == 1 and second.entries[0].id != entry.id
    add(hod, second, subject_id=data.subject2_id, weekday='2')

    assert 'must start after the current one' in page(publish(hod, second, today))
    publish(hod, second, today + timedelta(days=7))
    db.session.refresh(first)
    assert first.effective_to == today + timedelta(days=6)
    assert ActivityLog.query.filter_by(action='routine_superseded').count() == 1
    shown = page(hod.get(f'/routine/class/{data.sem1_id}'))
    assert 'MCA101' in shown and 'Versions' in shown


def test_a_draft_with_unassigned_classes_cannot_be_published(data, hod):
    other = Semester(name='Semester 3', department_id=data.mca_id)
    db.session.add(other)
    db.session.flush()
    source_subject = Subject(name='Networks', paper_code='MCA102', department_id=data.mca_id, semester_id=other.id)
    unmatched = Subject(name='Elective X', paper_code='MCA999', department_id=data.mca_id, semester_id=other.id)
    db.session.add_all([source_subject, unmatched])
    db.session.commit()
    source = open_draft(hod, data, other.id)
    add(hod, source, subject_id=unmatched.id)
    add(hod, source, subject_id=source_subject.id, weekday='1')
    publish(hod, source, local_now().date())

    # MCA102 exists in Semester 1 too (data fixture: Networks), MCA999 does not.
    draft = open_draft(hod, data)
    response = post(hod, f'/routine/drafts/{draft.id}/copy-from', {'source_id': source.id}, follow_redirects=True)
    assert '1 class(es) had no subject with the same paper code here' in page(response)
    db.session.refresh(draft)
    assert sorted((e.weekday, e.subject_id) for e in draft.entries) == [(0, None), (1, data.subject2_id)]
    assert 'no subject or title yet' in page(publish(hod, draft, local_now().date()))
    assert db.session.get(Timetable, draft.id).status == 'draft'


def test_copying_a_day_skips_clashes(data, hod):
    draft = open_draft(hod, data)
    add(hod, draft, subject_id=data.subject_id)
    add(hod, draft, title='Seminar', weekday='1', start_time='14:00', end_time='15:00')
    response = post(hod, f'/routine/drafts/{draft.id}/copy-day', {'from_day': '0', 'to_day': '1'}, follow_redirects=True)
    assert 'Copied 1 class(es) from Monday to Tuesday' in page(response)
    response = post(hod, f'/routine/drafts/{draft.id}/copy-day', {'from_day': '1', 'to_day': '0'}, follow_redirects=True)
    assert 'skipped because they would clash' in page(response)


def test_discarding_a_draft(data, hod):
    draft = open_draft(hod, data)
    add(hod, draft, title='Seminar')
    post(hod, f'/routine/drafts/{draft.id}/discard')
    assert Timetable.query.count() == 0 and TimetableEntry.query.count() == 0


def test_a_subject_or_group_in_a_routine_cannot_be_deleted(data, hod):
    group = ClassGroup(department_id=data.mca_id, semester_id=data.sem1_id, name='Gr-1')
    db.session.add(group)
    db.session.commit()
    draft = open_draft(hod, data)
    add(hod, draft, subject_id=data.subject2_id, group_id=group.id)
    assert 'routine classes: 1' in page(post(hod, f'/subjects/delete/{data.subject2_id}', follow_redirects=True))
    assert 'class(es) in a routine' in page(post(hod, f'/groups/{group.id}/delete', follow_redirects=True))


# ── Scope, locks, printing ───────────────────────────────────

def test_who_can_see_and_edit_a_routine(data, client_for, hod):
    draft = open_draft(hod, data)
    add(hod, draft, subject_id=data.subject_id)
    publish(hod, draft, local_now().date())

    teacher = client_for(data.teacher)
    assert teacher.get(f'/routine/class/{data.sem1_id}').status_code == 200
    assert teacher.get(f'/routine/class/{data.sem2_id}').status_code == 403
    assert post(teacher, f'/routine/class/{data.sem1_id}/draft').status_code == 403
    assert 'MCA - Semester 1' in page(teacher.get('/routine/classes'))
    assert client_for(data.student).get(f'/routine/class/{data.sem1_id}').status_code == 403

    make_account('hod2@rcciit.org.in', 'hod', department_id=data.other_id)
    db.session.commit()
    other_hod = client_for('hod2@rcciit.org.in')
    assert other_hod.get(f'/routine/class/{data.sem1_id}').status_code == 403
    assert post(other_hod, f'/routine/class/{data.sem1_id}/draft').status_code == 403


def test_a_locked_semesters_routine_cannot_change(data, hod):
    draft = open_draft(hod, data)
    post(hod, f'/semesters/{data.sem1_id}/lock')
    assert 'is locked' in page(add(hod, draft, title='Seminar'))
    assert TimetableEntry.query.count() == 0
    assert 'can be viewed but not changed' in page(hod.get(f'/routine/class/{data.sem1_id}/draft'))


def test_the_print_view_lays_days_against_periods(data, client_for, hod):
    weekday_schedule(client_for(data.principal), days=(0, 1, 2, 3, 4, 5))
    draft = open_draft(hod, data)
    add(hod, draft, subject_id=data.subject_id, start_time='10:00', end_time='11:40', room='416')  # two periods
    publish(hod, draft, local_now().date())
    printed = page(hod.get(f'/routine/class/{data.sem1_id}/print'))
    assert 'Class routine' in printed and 'colspan="2"' in printed and '416' in printed
    assert 'MCA101 - DBMS: Asha Teacher (AT)' in printed


def test_week_numbers_follow_the_first_day_of_the_routine(app):
    monday = date(2026, 11, 9)
    assert week_number(monday, monday + timedelta(days=6)) == 1
    assert week_number(monday, monday + timedelta(days=7)) == 2
    assert runs_in_week('odd', 1) and not runs_in_week('odd', 2) and runs_in_week('even', 2)
    assert runs_in_week('every', 7)


def test_grid_places_parallel_classes_side_by_side(app, data):
    from attendqr.services.routine import week_grid

    entries = [TimetableEntry(weekday=0, start_time=time(10), end_time=time(12)),
               TimetableEntry(weekday=0, start_time=time(10), end_time=time(11)),
               TimetableEntry(weekday=0, start_time=time(14), end_time=time(15))]
    grid = week_grid(entries, data.mca_id, days=[0])
    row = grid['rows'][0]
    blocks = row['blocks']
    assert [(b['lane_start'], b['lane_size']) for b in blocks] == [(0.0, 50.0), (50.0, 50.0), (0.0, 100.0)]
    assert blocks[0]['start'] == 0.0 and grid['span'] == 300
    assert row['lanes'] == 2  # the row is drawn taller for the two parallel classes
