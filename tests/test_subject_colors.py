"""Subject colours in the routine: automatic by default, chosen by the HOD, shown in every routine view."""
from datetime import time, timedelta

from attendqr.extensions import db
from attendqr.models import Subject, Teacher, Timetable, TimetableEntry
from attendqr.services.subject_colors import NO_SUBJECT, SUBJECT_COLORS, parse_color, subject_color
from attendqr.utils import local_now
from conftest import post


def subject_form(data, subject, **values):
    form = {'paper_code': subject.paper_code, 'name': subject.name, 'department_id': data.mca_id,
            'semester_id': data.sem1_id, 'teacher_id': data.teacher_id}
    form.update(values)
    return form


def test_a_class_gets_different_automatic_colours(app, data):
    first, second = db.session.get(Subject, data.subject_id), db.session.get(Subject, data.subject2_id)
    assert first.color is None and second.color is None
    colors = subject_color(first), subject_color(second)
    assert colors[0] != colors[1] and set(colors) <= set(SUBJECT_COLORS) and NO_SUBJECT not in colors
    assert subject_color(None) == NO_SUBJECT  # a class that is not a subject ("Special Tutorial Class")


def test_the_hod_chooses_a_colour_and_the_list_shows_it(data, client_for):
    client = client_for(data.hod)
    subject = db.session.get(Subject, data.subject_id)
    post(client, f'/subjects/edit/{subject.id}', subject_form(data, subject, color='magenta'))
    db.session.refresh(subject)
    assert subject.color == 'magenta' and subject_color(subject) == 'magenta'
    html = client.get('/subjects').get_data(as_text=True)
    assert 'subject-chip subject-magenta' in html and 'Colour in the routine: Magenta' in html

    post(client, f'/subjects/edit/{subject.id}', subject_form(data, subject))  # no colour field: kept
    db.session.refresh(subject)
    assert subject.color == 'magenta'
    post(client, f'/subjects/edit/{subject.id}', subject_form(data, subject, color=''))  # back to automatic
    db.session.refresh(subject)
    assert subject.color is None


def test_an_unknown_colour_is_refused(data, client_for):
    subject = db.session.get(Subject, data.subject_id)
    response = post(client_for(data.hod), f'/subjects/edit/{subject.id}',
                    subject_form(data, subject, color='#ff0000'), follow_redirects=True)
    assert 'Choose a colour from the list.' in response.get_data(as_text=True)
    db.session.refresh(subject)
    assert subject.color is None
    assert parse_color(' Teal ') == 'teal' and parse_color('') is None


def test_the_routine_shows_each_subject_in_its_colour(data, client_for):
    subject = db.session.get(Subject, data.subject_id)
    subject.color = 'orange'
    version = Timetable(department_id=data.mca_id, semester_id=data.sem1_id, status='published',
                        effective_from=local_now().date() - timedelta(days=7))
    db.session.add(version)
    entry = TimetableEntry(timetable=version, weekday=0, start_time=time(10), end_time=time(11), subject=subject)
    title_only = TimetableEntry(timetable=version, weekday=1, start_time=time(10), end_time=time(11),
                                title='Special Tutorial Class')
    db.session.add_all([entry, title_only])
    entry.teachers = [db.session.get(Teacher, data.teacher_id)]
    db.session.commit()

    client = client_for(data.hod)
    grid = client.get(f'/routine/class/{data.sem1_id}').get_data(as_text=True)
    assert 'subject-tint subject-orange' in grid and 'subject-tint subject-grey' in grid
    printed = client.get(f'/routine/class/{data.sem1_id}/print').get_data(as_text=True)
    assert 'subject-tint subject-orange' in printed
