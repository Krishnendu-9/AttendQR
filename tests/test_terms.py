"""Configurable terms: an institution can say "Batch" for semester, "Course" for department, "Director"..."""
import html

from attendqr.access import role_label
from attendqr.services.settings import term, terms
from conftest import post


def page(response):
    return html.unescape(response.get_data(as_text=True))


def rename(client, **words):
    return post(client, '/settings', words)


def test_the_usual_words_by_default(app):
    assert (term('semester'), terms('semester'), term('paper_code')) == ('Semester', 'Semesters', 'Paper code')
    assert role_label('principal') == 'Principal' and role_label('hod') == 'HOD'


def test_custom_words_reach_the_menu_pages_and_messages(data, client_for):
    principal = client_for(data.principal)
    rename(principal, term_semester='Batch', term_semesters='Batches', term_department='Course',
           term_departments='Courses', role_principal='Director', role_hod='Coordinator')
    assert (term('semester'), terms('department'), role_label('principal')) == ('Batch', 'Courses', 'Director')

    menu = page(principal.get('/'))
    assert '>Batches<' in menu and '>Courses<' in menu and '>Semesters<' not in menu
    batches = page(principal.get('/semesters'))
    assert '<h1' in batches and 'Batches' in batches.split('<h1')[1].split('</h1>')[0]
    response = post(principal, '/semesters', {'name': 'Semester 1', 'department_id': data.mca_id}, follow_redirects=True)
    assert "Batch 'Semester 1' already exists for this course." in page(response)
    assert 'Coordinator' in page(principal.get('/accounts'))
    assert 'Director' in page(principal.get('/account'))


def test_an_empty_word_is_refused(data, client_for):
    response = rename(client_for(data.principal), term_subject='')
    assert response.status_code == 400 and 'Enter a word' in page(response)
    assert term('subject') == 'Subject'


def test_urls_and_old_log_entries_keep_the_usual_words(data, client_for):
    from attendqr.models import ActivityLog

    principal = client_for(data.principal)
    post(principal, '/semesters', {'name': 'Semester 9', 'department_id': data.mca_id})
    before = ActivityLog.query.filter_by(action='semester_created').one().description
    rename(principal, term_semester='Batch')
    assert ActivityLog.query.filter_by(action='semester_created').one().description == before
    assert principal.get('/semesters').status_code == 200
