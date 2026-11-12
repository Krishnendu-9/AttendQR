"""The full-page student view, add and edit screens (GET pages over the existing POST routes)."""
from attendqr.extensions import db
from attendqr.models import Student
from conftest import add_session, post


def test_view_page_shows_profile_and_attendance(data, client_for):
    add_session(data, present=[data.student_id])
    html = client_for(data.principal).get(f'/students/{data.student_id}').get_data(as_text=True)
    assert 'Rahul Das' in html and 'MCA001' in html and 'rahul@example.com' in html
    assert 'Subject attendance' in html and 'DBMS' in html
    assert 'Present' in html  # the recent attendance entry


def test_view_page_is_scoped_like_the_list(data, client_for):
    outsider = Student(name='Outsider', roll_no='X1', email='x1@example.com',
                       department_id=data.other_id, semester_id=data.other_sem_id)
    db.session.add(outsider)
    db.session.commit()

    response = client_for(data.hod).get(f'/students/{outsider.id}')
    assert response.status_code == 404
    html = response.get_data(as_text=True)
    assert 'Student not found' in html and 'Outsider' not in html
    assert client_for(data.hod).get('/students/99999').status_code == 404
    assert client_for(data.teacher).get(f'/students/{data.student_id}').status_code == 200
    assert client_for(data.student).get(f'/students/{data.student_id}').status_code == 403


def test_teachers_can_add_but_not_edit(data, client_for):
    teacher = client_for(data.teacher)
    assert teacher.get('/students/new').status_code == 200
    assert teacher.get(f'/students/{data.student_id}/edit').status_code == 403
    html = teacher.get(f'/students/{data.student_id}').get_data(as_text=True)
    assert f'/students/{data.student_id}/edit' not in html and 'Reset password' in html


def test_edit_page_is_prefilled_and_posts_to_the_existing_route(data, client_for):
    html = client_for(data.principal).get(f'/students/{data.student_id}/edit').get_data(as_text=True)
    assert f'action="/students/edit/{data.student_id}"' in html
    assert 'value="Rahul Das"' in html and 'value="MCA001"' in html
    assert f'<option value="{data.sem1_id}" selected>' in html


def test_failed_save_from_the_form_page_keeps_the_input(data, client_for):
    client = client_for(data.principal)
    response = post(client, '/students', {
        'name': 'New Person', 'roll_no': 'MCA001', 'email': 'new@example.com',
        'department_id': data.mca_id, 'semester_id': data.sem1_id, 'form_page': '1',
    })
    assert response.status_code == 400
    html = response.get_data(as_text=True)
    assert 'value="New Person"' in html and 'value="new@example.com"' in html
    assert 'id="roll_no-error"' in html and 'Roll number MCA001 already exists' in html
    assert Student.query.filter_by(email='new@example.com').first() is None


def test_failed_save_without_the_form_page_still_redirects(data, client_for):
    response = post(client_for(data.principal), '/students', {
        'name': 'New Person', 'roll_no': 'MCA001', 'email': 'new@example.com',
        'department_id': data.mca_id, 'semester_id': data.sem1_id,
    })
    assert response.status_code == 302 and response.headers['Location'] == '/students'


def test_actions_return_to_the_student_page_or_the_list(data, client_for):
    client = client_for(data.principal)
    page = f'/students/{data.student_id}'
    response = post(client, f'/students/reset-device/{data.student_id}', {'return_to': page})
    assert response.headers['Location'] == page
    response = post(client, f'/students/reset-device/{data.student_id}', {'return_to': '/students?q=rahul&page=2'})
    assert response.headers['Location'] == '/students?q=rahul&page=2'
    for unsafe in ('/add_admin', '//evil.example/students', f'/students/{data.student_id}/edit', 'https://evil.example/'):
        response = post(client, f'/students/reset-device/{data.student_id}', {'return_to': unsafe})
        assert response.headers['Location'] == '/students', unsafe


def test_edit_and_delete_confirm_with_the_name(data, client_for):
    client = client_for(data.principal)
    response = post(client, f'/students/edit/{data.student_id}', {
        'name': 'Rahul Kumar Das', 'roll_no': 'MCA001', 'email': 'rahul@example.com',
        'department_id': data.mca_id, 'semester_id': data.sem1_id, 'return_to': f'/students/{data.student_id}',
    }, follow_redirects=True)
    assert 'Updated Rahul Kumar Das.' in response.get_data(as_text=True)
    response = post(client, f'/students/delete/{data.student2_id}', follow_redirects=True)
    assert 'Deleted Priya Sen.' in response.get_data(as_text=True)


def test_list_rows_link_to_the_view_page(data, client_for):
    html = client_for(data.principal).get('/students').get_data(as_text=True)
    assert f'data-href="/students/{data.student_id}"' in html
    assert 'id="students-list"' in html and 'hx-get="/students"' in html


def test_component_gallery_only_in_debug_mode(app, data, client_for):
    client = client_for(data.principal)
    assert client.get('/dev/components').status_code == 404
    app.debug = True
    try:
        response = client.get('/dev/components')
    finally:
        app.debug = False
    assert response.status_code == 200
    assert 'Present (marked by staff)' in response.get_data(as_text=True)
