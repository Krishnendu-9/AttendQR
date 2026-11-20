"""Importing a routine from a spreadsheet into a draft."""
import html
import io
import re

import openpyxl

from attendqr.extensions import db
from attendqr.models import ClassGroup, Teacher, Timetable, TimetableEntry
from conftest import post


def page(response):
    return html.unescape(response.get_data(as_text=True))


def text(response):
    """The page's words without markup."""
    return ' '.join(re.sub(r'<[^>]+>', ' ', page(response)).split())


def workbook(rows, headers=('day', 'start', 'end', 'period', 'length', 'paper_code', 'title', 'group', 'teachers',
                            'room', 'repeats')):
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(list(headers))
    for row in rows:
        sheet.append(list(row))
    data = io.BytesIO()
    book.save(data)
    data.seek(0)
    return data


def upload(client, draft, data, filename='routine.xlsx'):
    return post(client, f'/routine/drafts/{draft.id}/import', {'file': (data, filename)},
                content_type='multipart/form-data')


def open_draft(client, data):
    post(client, f'/routine/class/{data.sem1_id}/draft')
    return Timetable.query.filter_by(semester_id=data.sem1_id, status='draft').one()


def bell_schedule(client):
    post(client, '/calendar/schedules', {'name': 'Weekday', 'scope': 'institution'})
    from attendqr.models import BellSchedule
    schedule = BellSchedule.query.one()
    post(client, f'/calendar/schedules/{schedule.id}', {
        'name': 'Weekday', 'label': ['1', '2', '3'], 'start': ['10:00', '10:50', '11:40'],
        'end': ['10:50', '11:40', '12:30'], 'weekday': ['0', '1', '2', '3', '4']})


def test_the_template_lists_the_columns(data, client_for):
    response = client_for(data.hod).get(f'/routine/class/{data.sem1_id}/import-template')
    sheet = openpyxl.load_workbook(io.BytesIO(response.data)).active
    assert [cell.value for cell in sheet[1]][:6] == ['day', 'start', 'end', 'period', 'length', 'paper_code']
    assert sheet.cell(row=2, column=6).value == 'MCA101'  # the class's own subject


def test_rows_fill_the_draft_and_bad_rows_are_listed(data, client_for):
    hod = client_for(data.hod)
    bell_schedule(client_for(data.principal))
    db.session.get(Teacher, data.teacher_id).short_name = 'AT'
    db.session.add(ClassGroup(department_id=data.mca_id, semester_id=data.sem1_id, name='Gr-1'))
    db.session.commit()
    draft = open_draft(hod, data)
    response = upload(hod, draft, workbook([
        ('Monday', '10:00', '10:50', '', '', 'mca101', '', '', 'AT', '416', 'every'),
        ('Tue', '', '', '2', '2', 'MCA102', '', 'Gr-1', 'asha@college.example', 'Lab 2', 'odd'),
        ('3', '14:00', '15:00', '', '', '', 'Special Tutorial Class', '', '', '', ''),
        ('Funday', '10:00', '10:50', '', '', 'MCA101', '', '', '', '', ''),
        ('Thursday', '10:00', '10:50', '', '', 'MCA999', '', '', '', '', ''),
        ('Monday', '10:20', '11:00', '', '', 'MCA102', '', '', '', '', ''),  # Asha is busy then
    ]))
    shown = text(response)
    assert '3 of 6 rows imported' in shown
    assert "Row 5: Day 'funday' is not a day of the week." in shown
    assert 'Row 6: No subject of this class has the paper code MCA999.' in shown
    assert 'Row 7: Asha Teacher also teaches' in shown

    entries = sorted(draft.entries, key=lambda e: e.weekday)
    assert [(e.weekday, e.subject.paper_code if e.subject else e.title) for e in entries] == [
        (0, 'MCA101'), (1, 'MCA102'), (2, 'Special Tutorial Class')]
    lab = entries[1]
    assert (lab.start_time.strftime('%H:%M'), lab.end_time.strftime('%H:%M')) == ('10:50', '12:30')  # periods 2-3
    assert (lab.group.name, lab.week_pattern, [t.name for t in lab.teachers]) == ('Gr-1', 'odd', ['Asha Teacher'])
    assert db.session.get(Timetable, draft.id).status == 'draft'  # never published by an import


def test_a_csv_works_too(data, client_for):
    hod = client_for(data.hod)
    draft = open_draft(hod, data)
    csv = io.BytesIO(b'day,start,end,paper_code\nFriday,09:00,09:50,MCA101\n')
    assert '1 of 1 rows imported' in page(upload(hod, draft, csv, 'routine.csv'))
    assert TimetableEntry.query.one().weekday == 4


def test_a_broken_spreadsheet_is_explained_not_an_error_page(data, client_for):
    """A .xlsx that is not one (here, not even a zip) used to end in a server error."""
    hod = client_for(data.hod)
    draft = open_draft(hod, data)
    response = upload(hod, draft, io.BytesIO(b'not a zip'))
    assert response.status_code == 302
    body = page(hod.get(response.headers['Location']))
    assert 'The file could not be read' in body and 'BadZipFile' not in body
    assert TimetableEntry.query.count() == 0


def test_only_managers_import_and_only_spreadsheets(data, client_for):
    hod = client_for(data.hod)
    draft = open_draft(hod, data)
    response = upload(hod, draft, io.BytesIO(b'hello'), 'routine.pdf')
    assert 'Upload a .xlsx or .csv file' in page(hod.get(response.headers['Location']))
    assert upload(client_for(data.teacher), draft, workbook([])).status_code == 403
    assert client_for(data.teacher).get(f'/routine/class/{data.sem1_id}/import-template').status_code == 403
