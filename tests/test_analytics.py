"""Analytics: weekly trend, per-subject rate and students at risk, consistent with the reports."""
import html

from attendqr.access import SPECIAL_CLASS_SUBJECT_NAME
from attendqr.extensions import db
from attendqr.models import LeaveRequest, Subject
from attendqr.services.analytics import DEFAULT_WEEKS, build_class_trends, parse_weeks
from attendqr.utils import to_local
from conftest import add_session


def history(data):
    """Five DBMS classes on the last five days: Rahul attended the two latest, Priya all of them."""
    return [add_session(data, days_ago=days, present=[data.student2_id] + ([data.student_id] if days < 2 else []))
            for days in range(5)]


def trends_for(app, data, weeks=DEFAULT_WEEKS):
    with app.test_request_context():
        return build_class_trends(data.mca_id, data.sem1_id, weeks)


def test_totals_subjects_and_students_at_risk(app, data):
    history(data)
    special = Subject(name=SPECIAL_CLASS_SUBJECT_NAME, department_id=data.mca_id, semester_id=data.sem1_id)
    db.session.add(special)
    db.session.commit()
    add_session(data, subject_id=special.id, present=[data.student_id], special_title='Workshop')  # never counted

    trends = trends_for(app, data)

    assert (trends.overall.present, trends.overall.expected, trends.overall.rate) == (7, 10, 70.0)
    assert trends.sessions == 5
    assert [(item['subject'].name, item['rate']) for item in trends.subjects] == [('DBMS', 70.0)]
    assert [row['student'].roll_no for row in trends.at_risk] == ['MCA001']
    assert trends.at_risk[0]['percentage'] == 40.0
    assert trends.below_target == 1 and trends.class_size == 2


def test_weekly_buckets_add_up_and_the_chart_has_one_slot_per_week(app, data):
    history(data)

    trends = trends_for(app, data, weeks=4)

    assert len(trends.weeks) == 4 and len(trends.chart['points']) == 4
    assert sum(week['present'] for week in trends.weeks) == 7
    assert sum(week['expected'] for week in trends.weeks) == 10
    assert sum(week['sessions'] for week in trends.weeks) == 5
    # Weeks without classes have no rate, and the line breaks there instead of dropping to 0%.
    empty = [week for week in trends.weeks if not week['expected']]
    assert all(week['rate'] is None for week in empty)
    assert trends.chart['last']['rate'] == trends.weeks[-1]['rate'] or not trends.weeks[-1]['expected']


def test_approved_leave_is_left_out_as_in_the_reports(app, data):
    sessions = history(data)
    days = sorted(to_local(s.created_at).date() for s in sessions[2:])
    db.session.add(LeaveRequest(student_id=data.student_id, start_date=days[0], end_date=days[-1], kind='medical',
                                reason='Fever', status=LeaveRequest.APPROVED))
    db.session.commit()

    trends = trends_for(app, data)

    assert (trends.overall.present, trends.overall.expected) == (7, 7)
    assert trends.at_risk == []


def test_page_renders_charts_with_a_table_view(data, client_for):
    history(data)
    page = html.unescape(client_for(data.hod).get(
        f'/reports/analytics?department_id={data.mca_id}&semester_id={data.sem1_id}&weeks=4').get_data(as_text=True))

    assert 'Weekly attendance' in page and 'Show as table' in page
    assert 'Target 80%' in page
    assert 'Rahul Das' in page  # at risk
    assert 'data-tip-value="70.0%"' in page  # the DBMS bar's tooltip


def test_page_without_a_class_asks_for_one(data, client_for):
    page = client_for(data.teacher).get('/reports/analytics').get_data(as_text=True)
    assert 'Choose a class' in page


def test_scope_is_enforced(data, client_for):
    url = f'/reports/analytics?department_id={data.other_id}&semester_id={data.other_sem_id}'
    assert client_for(data.hod).get(url).status_code == 403
    assert client_for(data.student).get('/reports/analytics').status_code == 403


def test_unknown_periods_fall_back_to_the_default():
    assert parse_weeks('12') == 12
    assert parse_weeks('5') == DEFAULT_WEEKS
    assert parse_weeks('x') == DEFAULT_WEEKS


def test_y_axis_fits_the_data_and_the_target():
    from attendqr.services.analytics import y_domain

    assert y_domain([86.0, 91.5], 80) == (70, [70, 75, 80, 85, 90, 95, 100])
    assert y_domain([40.0, 95.0], 80) == (30, [30, 40, 50, 60, 70, 80, 90, 100])
    assert y_domain([3.0], 80)[0] == 0
