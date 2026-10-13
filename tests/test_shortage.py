"""The shortage list: students below the attendance requirement."""
import html
import io

import openpyxl
import pytest

from attendqr.blueprints.attendance import get_or_create_special_subject
from attendqr.services.reports import parse_threshold
from conftest import add_session


@pytest.fixture
def term(data):
    """DBMS: 4 classes, Networks: 2 classes.

    Rahul attends everything (100%). Priya attends 2 DBMS and both Networks:
    DBMS 50%, Networks 100%, overall 4/6 = 66.67%.
    """
    for i in range(4):
        add_session(data, subject_id=data.subject_id, present=[data.student_id] + ([data.student2_id] if i < 2 else []))
    for _ in range(2):
        add_session(data, subject_id=data.subject2_id, present=[data.student_id, data.student2_id])
    return data


def shortage(client, data, **params):
    query = {'department_id': data.mca_id, 'semester_id': data.sem1_id, **params}
    return client.get('/reports/shortage', query_string=query)


def test_lists_only_students_below_target(term, client_for):
    page = html.unescape(shortage(client_for(term.teacher), term).get_data(as_text=True))
    assert 'Priya Sen' in page
    assert 'Rahul Das' not in page
    assert '66.67%' in page and '4/6' in page
    # 80% of 6 overall needs 4 more consecutive classes; DBMS (2/4) needs 6.
    assert '2/4 · needs 6' in page
    assert 'Late Joiner' not in page  # different semester


def test_subject_shortage_is_listed_even_when_overall_is_fine(term, client_for):
    page = shortage(client_for(term.teacher), term, threshold=60).get_data(as_text=True)
    assert 'Priya Sen' in page  # 66.67% overall passes 60, but DBMS at 50% does not


def test_threshold_changes_who_is_short(term, client_for):
    page = shortage(client_for(term.teacher), term, threshold=50).get_data(as_text=True)
    assert 'Priya Sen' not in page
    assert 'Every student' in page


def test_subject_filter(term, client_for):
    page = shortage(client_for(term.teacher), term, subject_id=term.subject2_id).get_data(as_text=True)
    assert 'Every student' in page  # everyone attended both Networks classes


def test_void_sessions_and_special_classes_are_ignored(term, client_for, app):
    add_session(term, active=False)  # void
    special = get_or_create_special_subject(term.mca_id, term.sem1_id)
    add_session(term, subject_id=special.id, special_title='Aptitude', present=[])
    page = html.unescape(shortage(client_for(term.teacher), term).get_data(as_text=True))
    assert '4/6' in page and 'Aptitude' not in page


def test_no_classes_held_yet(data, client_for):
    page = shortage(client_for(data.teacher), data).get_data(as_text=True)
    assert 'No classes have been held' in page


def test_scope_is_enforced(term, client_for):
    assert shortage(client_for(term.other_teacher), term).status_code == 403
    assert shortage(client_for(term.student), term).status_code == 403
    assert shortage(client_for(term.hod), term).status_code == 200


def test_excel_export(term, client_for):
    response = client_for(term.hod).get('/reports/shortage/export', query_string={
        'department_id': term.mca_id, 'semester_id': term.sem1_id,
    })
    assert response.status_code == 200
    sheet = openpyxl.load_workbook(io.BytesIO(response.data)).active
    rows = list(sheet.iter_rows(values_only=True))
    assert rows[0][:7] == ('Roll No', 'Name', 'Email', 'Overall Attended', 'Classes Held', 'Overall %', 'Classes Needed')
    assert rows[1][:7] == ('MCA002', 'Priya Sen', 'priya@example.com', 4, 6, 66.67, 4)
    assert rows[1][-1] == 'MCA101 (needs 6)'


def test_page_without_selection_shows_only_the_form(data, client_for):
    page = client_for(data.teacher).get('/reports/shortage')
    assert page.status_code == 200
    assert 'Find Shortages' in page.get_data(as_text=True)


@pytest.mark.parametrize('raw, expected', [('75', 75), ('0', 80), ('100', 80), ('abc', 80), (None, 80), ('99', 99)])
def test_parse_threshold(raw, expected):
    assert parse_threshold(raw, 80) == expected
