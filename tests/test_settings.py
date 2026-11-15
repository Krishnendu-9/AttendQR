"""Institution settings: the Principal changes options in the app; the server's values are the defaults."""
import html
import io
import json
import re
from datetime import date, datetime

import openpyxl
import pytest

from attendqr.extensions import db
from attendqr.models import ActivityLog, InstitutionSetting
from attendqr.services.analytics import build_class_trends, week_start
from attendqr.services.audit import verify_chain
from attendqr.services.settings import SETTINGS, forget_cached, get_setting
from attendqr.utils import format_12h
from conftest import add_session, post

ALL_DEFAULTS = {
    'institution_name': '',
    'attendance_target': 80,
    'eligibility_percent': 75,
    'condonation_percent': 65,
    'timezone': 'Asia/Kolkata',
    'working_days': [0, 1, 2, 3, 4, 5],
    'week_starts_on': 0,
    'teachers_change_own_classes': True,
}


def page(response):
    return html.unescape(response.get_data(as_text=True))


def text(response):
    """The page's words without markup, with runs of spaces joined."""
    return ' '.join(re.sub(r'<[^>]+>', ' ', page(response)).split())


def save(client, **values):
    return post(client, '/settings', values)


def test_defaults_are_the_server_values(app):
    assert {key: get_setting(key) for key in ALL_DEFAULTS} == ALL_DEFAULTS
    assert set(SETTINGS) - set(ALL_DEFAULTS) == {key for key in SETTINGS if key.startswith(('term_', 'role_'))}
    assert InstitutionSetting.query.count() == 0


def test_without_a_saved_value_the_server_value_is_read_each_time(app):
    app.config['ATTENDANCE_TARGET'] = 70
    assert get_setting('attendance_target') == 70


def test_only_the_principal_can_open_or_change_settings(data, client_for):
    for username in (data.hod, data.teacher, data.student):
        client = client_for(username)
        assert client.get('/settings').status_code == 403
        assert save(client, attendance_target='50').status_code == 403
        assert post(client, '/settings/attendance_target/reset').status_code == 403
    assert InstitutionSetting.query.count() == 0

    principal_page = text(client_for(data.principal).get('/settings'))
    assert 'Server default: 80%' in principal_page and 'Server default: Asia/Kolkata' in principal_page


def test_the_settings_link_is_only_in_the_principals_menu(data, client_for):
    assert 'href="/settings"' in page(client_for(data.principal).get('/'))
    assert 'href="/settings"' not in page(client_for(data.hod).get('/'))


def test_saving_stores_only_what_changed_and_logs_it(data, client_for):
    principal = client_for(data.principal)
    response = save(principal, institution_name='  RCC   Institute ', attendance_target='75',
                    eligibility_percent='75', timezone='Asia/Kolkata')
    assert response.status_code == 302

    rows = {row.key: row for row in InstitutionSetting.query.all()}
    assert set(rows) == {'institution_name', 'attendance_target'}
    assert json.loads(rows['institution_name'].value) == 'RCC Institute'
    assert rows['attendance_target'].updated_by.username == data.principal
    assert get_setting('attendance_target') == 75

    logged = sorted(entry.description for entry in ActivityLog.query.filter_by(action='setting_changed'))
    assert logged == ['Changed the setting Attendance target: 80% to 75%.',
                      'Changed the setting Institution name: (none) to RCC Institute.']
    check = verify_chain()
    assert check.ok and check.unsealed == 0

    shown = page(principal.get('/settings'))
    assert 'Changed here' in shown and 'Reset to default' in shown


def test_saving_the_same_values_changes_nothing(data, client_for):
    response = save(client_for(data.principal), attendance_target='80', week_starts_on='0')
    assert response.status_code == 302
    assert InstitutionSetting.query.count() == 0
    assert ActivityLog.query.filter_by(action='setting_changed').count() == 0


@pytest.mark.parametrize(('values', 'message'), [
    ({'condonation_percent': '80'}, 'cannot be above the exam eligibility percentage (75%)'),
    ({'eligibility_percent': '60'}, 'cannot be above the exam eligibility percentage (60%)'),
    ({'timezone': 'Mars/Olympus_Mons'}, 'Choose a time zone from the list'),
    ({'working_days': ''}, 'Choose at least one working day'),
    ({'attendance_target': '100'}, 'from 1 to 99'),
    ({'attendance_target': 'eighty'}, 'Enter a whole number'),
    ({'week_starts_on': '7'}, 'Choose a day of the week'),
    ({'institution_name': 'x' * 81}, 'under 80 characters'),
])
def test_invalid_values_are_refused_and_nothing_is_saved(data, client_for, values, message):
    # A valid change goes with each invalid one: a refused save stores neither.
    response = save(client_for(data.principal), **{'attendance_target': '70', **values})
    assert response.status_code == 400
    assert message in page(response)
    assert InstitutionSetting.query.count() == 0


def test_the_form_keeps_what_was_typed_after_an_error(data, client_for):
    response = save(client_for(data.principal), institution_name='My College', condonation_percent='90')
    assert 'value="My College"' in page(response) and 'value="90"' in page(response)


def test_working_days_can_include_sunday(data, client_for):
    principal = client_for(data.principal)
    save(principal, working_days=['6', '0', '2'])
    assert get_setting('working_days') == [0, 2, 6]


def test_reset_to_default_deletes_the_saved_value(data, client_for):
    principal = client_for(data.principal)
    save(principal, attendance_target='70')
    assert post(principal, '/settings/attendance_target/reset').status_code == 302

    assert InstitutionSetting.query.count() == 0
    assert get_setting('attendance_target') == 80
    assert ActivityLog.query.filter_by(action='setting_changed').order_by(ActivityLog.id.desc()).first().description \
        == 'Reset the setting Attendance target: 70% to 80%.'
    assert post(principal, '/settings/nonsense/reset').status_code == 404


def test_a_reset_that_would_break_a_rule_is_refused(data, client_for):
    principal = client_for(data.principal)
    save(principal, eligibility_percent='90', condonation_percent='85')
    response = post(principal, '/settings/eligibility_percent/reset', follow_redirects=True)
    assert 'cannot be used' in page(response)
    assert get_setting('eligibility_percent') == 90


def test_a_broken_saved_value_falls_back_to_the_default(app):
    db.session.add_all([
        InstitutionSetting(key='timezone', value=json.dumps('Nowhere/Land')),
        InstitutionSetting(key='attendance_target', value='not json'),
        InstitutionSetting(key='from_a_newer_version', value='1'),
    ])
    db.session.commit()
    forget_cached()
    assert get_setting('timezone') == 'Asia/Kolkata'
    assert get_setting('attendance_target') == 80


def test_a_saved_percentage_that_conflicts_with_a_new_server_default_is_set_aside(app):
    db.session.add(InstitutionSetting(key='condonation_percent', value='70'))
    db.session.commit()
    forget_cached()
    assert get_setting('condonation_percent') == 70
    app.config['ELIGIBILITY_PERCENT'] = 68  # the server's eligibility now sits below the saved condonation
    forget_cached()
    assert (get_setting('condonation_percent'), get_setting('eligibility_percent')) == (65, 68)


# ── The settings reach every page that uses them ─────────────

def test_the_target_changes_the_dashboard_shortage_list_and_analytics(data, client_for):
    for n in range(4):
        add_session(data, days_ago=n, present=[data.student_id] if n < 3 else [])  # Rahul: 75%
    save(client_for(data.principal), attendance_target='70')

    assert 'Required: 70%' in page(client_for(data.student).get('/'))

    teacher = client_for(data.teacher)
    shortage = page(teacher.get(f'/reports/shortage?department_id={data.mca_id}&semester_id={data.sem1_id}'))
    assert 'id="threshold" name="threshold" min="1" max="99" value="70"' in shortage
    assert 'Rahul Das' not in shortage  # 75% is no longer short

    analytics = page(teacher.get(f'/reports/analytics?department_id={data.mca_id}&semester_id={data.sem1_id}'))
    assert 'Target 70%' in analytics


def test_the_eligibility_percentages_change_the_eligibility_report(app, data, client_for):
    for n in range(10):
        add_session(data, days_ago=n, present=[data.student_id] if n < 7 else [])  # Rahul: 70%
    save(client_for(data.principal), eligibility_percent='70', condonation_percent='60')
    report = page(client_for(data.hod).get(f'/reports/eligibility?department_id={data.mca_id}&semester_id={data.sem1_id}'))
    assert '60% to 70%' in report

    from attendqr.services.eligibility import build_eligibility
    with app.test_request_context():
        assert {row['student'].roll_no: row['status'] for row in build_eligibility(data.mca_id, data.sem1_id).rows}[
            'MCA001'] == 'eligible'


def test_a_time_zone_change_shifts_how_stored_times_are_shown(data, client_for):
    stored = datetime(2026, 11, 12, 4, 30)  # UTC
    assert format_12h(stored) == '12-11-2026 10:00 AM'
    save(client_for(data.principal), timezone='Europe/London')
    assert format_12h(stored) == '12-11-2026 04:30 AM'


def test_weeks_start_on_the_chosen_day(app, data, client_for):
    assert week_start(date(2026, 11, 15)) == date(2026, 11, 9)  # a Sunday belongs to the week from Monday
    save(client_for(data.principal), week_starts_on='6')
    assert week_start(date(2026, 11, 15)) == date(2026, 11, 15)
    assert week_start(date(2026, 11, 14)) == date(2026, 11, 8)

    with app.test_request_context():
        trends = build_class_trends(data.mca_id, data.sem1_id, weeks=4)
    assert {week['start'].weekday() for week in trends.weeks} == {6}


def test_the_institution_name_appears_in_titles_the_sidebar_and_exports(data, client_for):
    principal = client_for(data.principal)
    shown = page(principal.get('/'))
    assert '| AttendQR</title>' in shown and 'Attendance system' in shown

    save(principal, institution_name='RCC Institute & Co')
    shown = page(principal.get('/'))
    assert '| RCC Institute & Co</title>' in shown and 'Attendance system' not in shown

    add_session(data, present=[data.student_id])
    response = principal.get(f'/export/{data.mca_id}/{data.sem1_id}/excel')
    sheet = openpyxl.load_workbook(io.BytesIO(response.data)).active
    assert sheet.cell(row=1, column=1).value == 'Roll No'  # the headers stay in row 1
    notes = [row[0] for row in sheet.iter_rows(min_col=1, max_col=1, values_only=True) if row[0]]
    assert 'Institution: RCC Institute & Co' in notes
    assert sheet.oddHeader.center.text == 'RCC Institute && Co'
    assert principal.get(f'/export/{data.mca_id}/{data.sem1_id}/pdf').status_code == 200


def test_the_sign_in_page_shows_the_institution_name(app, data, client_for):
    save(client_for(data.principal), institution_name='RCC Institute')
    shown = page(app.test_client().get('/login'))
    assert 'Sign in | RCC Institute</title>' in shown
    assert 'QR-based attendance for RCC Institute' in shown
