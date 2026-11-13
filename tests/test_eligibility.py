"""Exam eligibility: eligible, needs condonation, condoned, detained."""
import html
import io

import openpyxl
import pytest

from attendqr.extensions import db
from attendqr.models import ActivityLog, Condonation, Department, Semester
from attendqr.services.eligibility import build_eligibility, status_for
from conftest import add_session, make_student, post


def page(response):
    return html.unescape(response.get_data(as_text=True))


@pytest.fixture
def held(data):
    """Ten classes: Rahul attended 3 (30%), Priya all 10, Third 7 (70%)."""
    department, semester = db.session.get(Department, data.mca_id), db.session.get(Semester, data.sem1_id)
    third = make_student('Third Student', 'MCA003', 'third@example.com', department, semester)
    db.session.commit()
    for n in range(10):
        present = [data.student2_id] + ([data.student_id] if n < 3 else []) + ([third.id] if n < 7 else [])
        add_session(data, days_ago=n, present=present)
    return third


def statuses(app, data):
    with app.test_request_context():
        return {row['student'].roll_no: (row['percentage'], row['status'])
                for row in build_eligibility(data.mca_id, data.sem1_id).rows}


def condone_url(data, student_id):
    return f'/reports/eligibility/{data.sem1_id}/students/{student_id}/condone'


def test_students_are_placed_by_their_percentage(app, data, held):
    assert statuses(app, data) == {
        'MCA001': (30.0, 'detained'), 'MCA002': (100.0, 'eligible'), 'MCA003': (70.0, 'needs_condonation'),
    }


def test_the_limits_are_inclusive():
    assert status_for(75, 4, False, 75, 65) == 'eligible'
    assert status_for(65, 4, False, 75, 65) == 'needs_condonation'
    assert status_for(64.99, 4, True, 75, 65) == 'detained'
    assert status_for(0, 0, False, 75, 65) == 'no_classes'


def test_hod_condones_a_student_in_the_band_with_a_reason(app, data, held, client_for):
    hod = client_for(data.hod)
    assert 'short reason' in page(post(hod, condone_url(data, held.id), follow_redirects=True))

    post(hod, condone_url(data, held.id), {'note': 'Hospitalised in October'})

    condonation = Condonation.query.one()
    assert (condonation.student_id, condonation.granted_by.username) == (held.id, data.hod)
    assert statuses(app, data)['MCA003'] == (70.0, 'condoned')
    assert 'Hospitalised in October' in ActivityLog.query.filter_by(action='attendance_condoned').one().description


def test_detained_and_eligible_students_cannot_be_condoned(data, held, client_for):
    hod = client_for(data.hod)
    assert 'detained' in page(post(hod, condone_url(data, data.student_id), {'note': 'x'}, follow_redirects=True))
    assert 'eligible' in page(post(hod, condone_url(data, data.student2_id), {'note': 'x'}, follow_redirects=True))
    assert Condonation.query.count() == 0


def test_a_condonation_can_be_revoked(app, data, held, client_for):
    hod = client_for(data.hod)
    post(hod, condone_url(data, held.id), {'note': 'Medical'})
    post(hod, f'/reports/eligibility/condonations/{Condonation.query.one().id}/revoke')
    assert Condonation.query.count() == 0 and statuses(app, data)['MCA003'][1] == 'needs_condonation'


def test_export_lists_status_and_reason(data, held, client_for):
    hod = client_for(data.hod)
    post(hod, condone_url(data, held.id), {'note': 'Medical certificate'})
    response = hod.get(f'/reports/eligibility/export?department_id={data.mca_id}&semester_id={data.sem1_id}')
    rows = list(openpyxl.load_workbook(io.BytesIO(response.data)).active.iter_rows(values_only=True))
    third = next(row for row in rows if row[0] == 'MCA003')
    assert third[6:9] == ('Eligible (condoned)', data.hod, 'Medical certificate')
    assert any(isinstance(row[0], str) and row[0].startswith('Eligible at 75%') for row in rows)


def test_page_and_actions_are_for_the_hod_and_principal_of_that_class(data, held, client_for):
    url = f'/reports/eligibility?department_id={data.mca_id}&semester_id={data.sem1_id}'
    text = page(client_for(data.principal).get(url))
    assert 'Needs condonation' in text and 'Detained' in text
    assert client_for(data.teacher).get(url).status_code == 403
    other_hod = client_for(data.hod).get(f'/reports/eligibility?department_id={data.other_id}&semester_id={data.other_sem_id}')
    assert other_hod.status_code == 403
    assert post(client_for(data.teacher), condone_url(data, held.id), {'note': 'x'}).status_code == 403
