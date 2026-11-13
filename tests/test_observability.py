"""Request ids, the access log, and Prometheus metrics."""
import json
import logging

import pytest

from attendqr.observability import JsonFormatter, Metrics, RequestIdFilter, metrics
from attendqr.services.qr import make_qr_token
from conftest import add_session, login

TOKEN = 'metrics-token-for-tests-1234567890'


@pytest.fixture(autouse=True)
def fresh_metrics():
    metrics.reset()
    yield
    metrics.reset()


def scrape(app, token=TOKEN):
    headers = {'Authorization': f'Bearer {token}'} if token else {}
    return app.test_client().get('/metrics', headers=headers)


# ── Request ids ──────────────────────────────────────────────

def test_every_response_has_a_request_id(app):
    first = app.test_client().get('/healthz').headers['X-Request-ID']
    second = app.test_client().get('/healthz').headers['X-Request-ID']
    assert len(first) == 32 and first != second


def test_a_valid_incoming_request_id_is_kept_and_a_bad_one_replaced(app):
    client = app.test_client()
    assert client.get('/healthz', headers={'X-Request-ID': 'proxy-abc-12345'}).headers['X-Request-ID'] == 'proxy-abc-12345'
    replaced = client.get('/healthz', headers={'X-Request-ID': 'bad id <script>'}).headers['X-Request-ID']
    assert replaced != 'bad id <script>' and len(replaced) == 32


def test_json_log_lines_carry_the_request_id(app):
    with app.test_request_context('/'):
        from flask import g
        g.request_id = 'req-12345678'
        record = logging.LogRecord('attendqr', logging.INFO, __file__, 1, 'hello %s', ('world',), None)
        RequestIdFilter().filter(record)
        line = json.loads(JsonFormatter().format(record))
    assert line['request_id'] == 'req-12345678' and line['message'] == 'hello world'


def test_access_log_has_the_route_pattern_not_the_token(app, data, caplog):
    token = make_qr_token(add_session(data).id)
    app.config['ACCESS_LOG'] = True
    with caplog.at_level(logging.INFO, logger=app.logger.name):
        app.test_client().get(f'/scan/{token}')
    access = [r.getMessage() for r in caplog.records if 'GET /scan/<token>' in r.getMessage()]
    assert access, [r.getMessage() for r in caplog.records]
    assert all(token not in r.getMessage() for r in caplog.records)
    assert all('127.0.0.1' not in r.getMessage() for r in caplog.records)


# ── /metrics ─────────────────────────────────────────────────

def test_metrics_are_off_without_a_token(app):
    app.config['METRICS_TOKEN'] = ''
    assert scrape(app).status_code == 404


def test_metrics_need_the_token(app):
    app.config['METRICS_TOKEN'] = TOKEN
    assert scrape(app, token=None).status_code == 403
    assert scrape(app, token='wrong').status_code == 403
    assert scrape(app).status_code == 200


def test_metrics_report_requests_logins_scans_and_live_sessions(app, data):
    app.config['METRICS_TOKEN'] = TOKEN
    add_session(data)  # live
    login(app.test_client(), data.teacher)
    login(app.test_client(), data.teacher, password='wrong-password', expect_success=False)
    app.test_client().get('/scan/not-a-real-token')

    body = scrape(app).get_data(as_text=True)

    assert 'attendqr_logins_total{outcome="success"} 1' in body
    assert 'attendqr_logins_total{outcome="failed"} 1' in body
    assert 'attendqr_scans_total{outcome="token_invalid"} 1' in body
    assert 'attendqr_live_sessions 1' in body
    assert 'attendqr_http_requests_total{method="POST",route="/login",status="302"} 1' in body
    assert '# TYPE attendqr_http_request_duration_seconds histogram' in body
    assert 'attendqr_http_request_duration_seconds_bucket{route="/login",le="+Inf"} 2' in body


def test_marked_attendance_is_counted(app, data):
    from conftest import post_json

    class_session = add_session(data)
    client = app.test_client()
    client.get(f'/scan/{make_qr_token(class_session.id)}')
    login(client, data.student)
    assert post_json(client, '/mark_attendance').get_json()['success']
    assert metrics.value('attendqr_scans_total', outcome='granted') == 1
    assert metrics.value('attendqr_scans_total', outcome='marked') == 1


def test_histogram_buckets_are_cumulative():
    m = Metrics()
    for seconds in (0.004, 0.03, 0.03, 3.0):
        m.observe('attendqr_http_request_duration_seconds', seconds, route='/x')
    body = m.render()
    assert 'attendqr_http_request_duration_seconds_bucket{route="/x",le="0.01"} 1' in body
    assert 'attendqr_http_request_duration_seconds_bucket{route="/x",le="0.05"} 3' in body
    assert 'attendqr_http_request_duration_seconds_bucket{route="/x",le="+Inf"} 4' in body
    assert 'attendqr_http_request_duration_seconds_count{route="/x"} 4' in body


def test_label_values_are_escaped():
    m = Metrics()
    m.inc('attendqr_scans_total', outcome='a"b\\c')
    assert 'attendqr_scans_total{outcome="a\\"b\\\\c"} 1' in m.render()
