"""Filling a draft routine from a spreadsheet: one row per class.

Optional: the routine is normally built on the website. The import only ever fills a draft,
runs the same checks as the editor's dialog on every row, and lists the rows it could not
take. Nothing is published until the HOD reviews the draft and publishes it.

The sheet is a simple list, not the merged-cell grid of a printed routine; that layout is
too irregular to read reliably.
"""
import csv
import io
import itertools
from datetime import date, datetime, time

import openpyxl
from flask import current_app
from werkzeug.datastructures import MultiDict

from ..access import SPECIAL_CLASS_SUBJECT_NAME, teacher_short_name
from ..extensions import db
from ..models import ClassGroup, Subject, Teacher, TimetableEntry
from ..utils import normalize_header
from .routine import RoutineError, apply_entry, clashes, clock, parse_entry_form, week_periods
from .settings import WEEKDAYS

HEADERS = ['day', 'start', 'end', 'period', 'length', 'paper_code', 'title', 'group', 'teachers', 'room', 'repeats',
           'valid_from', 'valid_to', 'note']
_DAY_NAMES = {name.lower(): index for index, name in enumerate(WEEKDAYS)}
_DAY_NAMES.update({name[:3].lower(): index for index, name in enumerate(WEEKDAYS)})
_REPEATS = {'': 'every', 'every': 'every', 'every week': 'every', 'weekly': 'every', 'odd': 'odd', 'odd weeks': 'odd',
            'even': 'even', 'even weeks': 'even'}


def template_rows(semester):
    """(headers, sample rows) for the download, using the class's own subjects and groups when it has them."""
    subjects = [s for s in Subject.query.filter_by(semester_id=semester.id) if s.name != SPECIAL_CLASS_SUBJECT_NAME]
    groups = ClassGroup.query.filter_by(semester_id=semester.id).order_by(ClassGroup.name).all()
    first = subjects[0] if subjects else None
    second = subjects[1] if len(subjects) > 1 else first
    teacher = teacher_short_name(first.teacher) if first and first.teacher else 'SC'
    rows = [
        ['Monday', '10:00', '10:50', '', '', first.paper_code if first else 'C202', '', '', teacher, '416', 'every', '', '', ''],
        ['Tuesday', '', '', '1', '3', second.paper_code if second else 'C293', '', groups[0].name if groups else 'Gr-1',
         'MG+SC', 'Lab 2', 'every', '', '', 'Lab'],
        ['Wednesday', '14:10', '15:00', '', '', '', 'Special Tutorial Class', '', '', '', 'odd', '', '', ''],
    ]
    return HEADERS, rows


def read_rows(file_storage):
    """The data rows of a .csv or .xlsx upload as dicts keyed by normalised header. Raises ValueError."""
    limit = current_app.config['MAX_IMPORT_ROWS']
    filename = (file_storage.filename or '').lower()
    file_storage.stream.seek(0)
    if filename.endswith('.csv'):
        raw = file_storage.stream.read()
        try:
            content = raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            content = raw.decode('latin-1', errors='replace')
        reader = csv.DictReader(io.StringIO(content))
        rows = ({normalize_header(k): v for k, v in row.items() if k is not None} for row in reader)
    elif filename.endswith('.xlsx'):
        workbook = openpyxl.load_workbook(file_storage.stream, read_only=True, data_only=True)
        try:
            sheet = workbook.active or workbook.worksheets[0]
            values = sheet.iter_rows(values_only=True)
            header = next(values, None)
            if header is None:
                return []
            names = [normalize_header(value) for value in header]
            rows = [{names[i]: value for i, value in enumerate(row) if i < len(names)} for row in
                    itertools.islice(values, limit + 1)]
        finally:
            workbook.close()
    else:
        raise ValueError("Upload a .xlsx or .csv file.")
    result = [row for row in itertools.islice(rows, limit + 1)
              if any(value not in (None, '') for value in row.values())]
    if len(result) > limit:
        raise ValueError(f"The file has more than {limit} rows.")
    return result


def _text(row, key):
    value = row.get(key)
    if value is None:
        return ''
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, datetime):
        return value.date().isoformat() if key.startswith('valid') else value.strftime('%H:%M')
    if isinstance(value, (time, date)):
        return value.isoformat()[:5] if isinstance(value, time) else value.isoformat()
    return str(value).strip()


def _weekday(text):
    text = text.strip().lower()
    if text in _DAY_NAMES:
        return _DAY_NAMES[text]
    if text.isdigit() and 1 <= int(text) <= 7:
        return int(text) - 1  # 1 = Monday ... 7 = Sunday
    raise RoutineError(f"Day '{text}' is not a day of the week.")


def _times(row, weekday, periods):
    start, end = _text(row, 'start'), _text(row, 'end')
    if start or end:
        return start, end
    label, length = _text(row, 'period'), _text(row, 'length') or '1'
    classes = [p for p in periods[weekday] if not p.is_break]
    index = next((i for i, p in enumerate(classes) if p.label.lower() == label.lower()), None)
    if not label or index is None:
        raise RoutineError("Give a start and an end time, or a period of the bell schedule for that day."
                           if not label else f"There is no period '{label}' on {WEEKDAYS[weekday]}.")
    if not length.isdigit() or int(length) < 1:
        raise RoutineError(f"Length '{length}' should be a number of periods.")
    last = classes[min(index + int(length), len(classes)) - 1]
    return clock(classes[index].start_time), clock(last.end_time)


def _teacher_ids(text, teachers):
    """Ids for "MG+SC" or "a@college.edu, b@college.edu", matched by email or short name."""
    ids = []
    for token in [t.strip() for t in text.replace('+', ',').replace(';', ',').split(',') if t.strip()]:
        match = next((t for t in teachers if t.email.lower() == token.lower()
                      or teacher_short_name(t).lower() == token.lower()), None)
        if match is None:
            raise RoutineError(f"No teacher of this department has the email or short name '{token}'.")
        ids.append(str(match.id))
    return ids


def import_rows(draft, rows):
    """Add each row to the draft. Returns (added, notes, errors): notes and errors are (row number, text)."""
    semester = draft.semester
    periods = week_periods(semester.department_id)
    subjects = {(s.paper_code or '').upper(): s for s in Subject.query.filter_by(semester_id=semester.id) if s.paper_code}
    groups = {g.name.lower(): g for g in ClassGroup.query.filter_by(semester_id=semester.id)}
    teachers = Teacher.query.filter_by(department_id=semester.department_id).all()
    added, notes, errors = 0, [], []
    for number, row in enumerate(rows, start=2):  # row 1 is the header
        try:
            weekday = _weekday(_text(row, 'day'))
            start, end = _times(row, weekday, periods)
            form = MultiDict([('weekday', str(weekday)), ('start_time', start), ('end_time', end),
                              ('title', _text(row, 'title')), ('room', _text(row, 'room')),
                              ('valid_from', _text(row, 'valid_from')), ('valid_to', _text(row, 'valid_to')),
                              ('note', _text(row, 'note'))])
            code = _text(row, 'paper_code').upper()
            if code:
                subject = subjects.get(code)
                if subject is None:
                    raise RoutineError(f"No subject of this class has the paper code {code}.")
                form.add('subject_id', str(subject.id))
            group_name = _text(row, 'group')
            if group_name:
                group = groups.get(group_name.lower())
                if group is None:
                    raise RoutineError(f"This class has no group called {group_name}.")
                form.add('group_id', str(group.id))
            for teacher_id in _teacher_ids(_text(row, 'teachers'), teachers):
                form.add('teacher_ids', teacher_id)
            repeats = _REPEATS.get(_text(row, 'repeats').lower())
            if repeats is None:
                raise RoutineError("Repeats should be every, odd or even.")
            form.add('week_pattern', repeats)
            values = parse_entry_form(form, draft)
            clash_errors, warnings = clashes(draft, values)
            if clash_errors:
                raise RoutineError(' '.join(clash_errors))
        except RoutineError as exc:
            errors.append((number, str(exc)))
            continue
        entry = TimetableEntry()
        entry.timetable = draft
        db.session.add(entry)
        apply_entry(entry, values)
        db.session.flush()
        added += 1
        notes += [(number, warning) for warning in warnings]
    return added, notes, errors
