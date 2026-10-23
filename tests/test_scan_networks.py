"""SCAN_ALLOWED_NETWORKS: which client addresses may open a QR scan link."""
import ipaddress
import re

import pytest

from attendqr import create_app
from attendqr.extensions import db
from attendqr.models import Attendance
from attendqr.services.network import ScanNetworkConfigError, parse_scan_networks
from attendqr.services.qr import make_qr_token
from conftest import add_session, login, post_json

CAMPUS = '203.0.113.7'
HOME = '198.51.100.20'


def scan(client, token, remote_addr, headers=None):
    return client.get(f'/scan/{token}', environ_base={'REMOTE_ADDR': remote_addr}, headers=headers or {})


def refused(response):
    return response.status_code == 403 and 'Campus Network Required' in response.get_data(as_text=True)


# ── Parsing ──────────────────────────────────────────────────

def test_empty_setting_means_private_ranges_without_loopback():
    networks = parse_scan_networks('')
    assert ipaddress.ip_address('192.168.0.170') in networks[2]
    assert not any(ipaddress.ip_address('127.0.0.1') in net for net in networks if net.version == 4)
    assert parse_scan_networks(' , ') == networks


def test_any_disables_the_check():
    assert parse_scan_networks('any') is None
    assert parse_scan_networks(' ANY ') is None


def test_ranges_are_parsed():
    assert [str(n) for n in parse_scan_networks('203.0.113.0/24, 2001:db8::/32, 198.51.100.4')] == [
        '203.0.113.0/24', '2001:db8::/32', '198.51.100.4/32',
    ]
    assert len(parse_scan_networks(['10.0.0.0/8', ' 192.0.2.0/24 '])) == 2


@pytest.mark.parametrize('raw, message', [
    ('203.0.113.0/24, campus-wifi', "invalid entry 'campus-wifi'"),
    ('203.0.113.300/24', "invalid entry"),
    ('203.0.113.7/24', 'Did you mean 203.0.113.0/24?'),
    ('0.0.0.0/0', "Write 'any'"),
    ('::/0', "Write 'any'"),
    ('any, 203.0.113.0/24', "mixes 'any'"),
])
def test_malformed_settings_are_rejected(raw, message):
    with pytest.raises(ScanNetworkConfigError, match=re.escape(message)):
        parse_scan_networks(raw)


def test_app_refuses_to_start_with_a_malformed_setting(tmp_path):
    with pytest.raises(ScanNetworkConfigError):
        create_app({
            'TESTING': True,
            'SECRET_KEY': 'x' * 32,
            'SQLALCHEMY_DATABASE_URI': f"sqlite:///{(tmp_path / 'bad.db').as_posix()}",
            'SCAN_ALLOWED_NETWORKS': '203.0.113.0/24,10.0.0.0/33',
            'AUTO_MIGRATE': False,
        })


# ── Scan route ───────────────────────────────────────────────

def test_scan_from_outside_the_allowed_ranges_is_refused_and_grants_nothing(app, data):
    app.config['SCAN_ALLOWED_NETWORKS'] = '203.0.113.0/24'
    class_session = add_session(data)
    phone = app.test_client()
    login(phone, data.student)
    token = make_qr_token(class_session.id)

    assert refused(scan(phone, token, HOME))
    result = post_json(phone, '/mark_attendance')
    assert result.status_code == 410
    assert Attendance.query.filter_by(session_id=class_session.id).count() == 0

    allowed = scan(phone, token, CAMPUS)
    assert allowed.status_code == 302 and allowed.headers['Location'].endswith('/attend')
    assert post_json(phone, '/mark_attendance').get_json()['success'] is True


def test_refusal_happens_before_the_token_is_checked(app):
    app.config['SCAN_ALLOWED_NETWORKS'] = '203.0.113.0/24'
    assert refused(scan(app.test_client(), 'not-a-token', HOME))


def test_default_accepts_lan_addresses_only(app):
    app.config['SCAN_ALLOWED_NETWORKS'] = ''
    client = app.test_client()
    # A bad token returns "Invalid QR" (404) once the address has passed.
    assert scan(client, 'not-a-token', '192.168.0.42').status_code == 404
    assert scan(client, 'not-a-token', '::ffff:192.168.0.42').status_code == 404
    assert refused(scan(client, 'not-a-token', CAMPUS))
    assert refused(scan(client, 'not-a-token', '127.0.0.1'))


def test_any_accepts_every_address(app):
    app.config['SCAN_ALLOWED_NETWORKS'] = 'any'
    assert scan(app.test_client(), 'not-a-token', HOME).status_code == 404


def test_proxy_headers_without_a_trusted_proxy_are_refused(app):
    """Behind an unconfigured proxy, remote_addr is the proxy's address; refuse rather than trust it."""
    app.config['SCAN_ALLOWED_NETWORKS'] = '127.0.0.0/8'
    client = app.test_client()
    assert scan(client, 'not-a-token', '127.0.0.1').status_code == 404
    for header in ('X-Forwarded-For', 'X-Real-IP', 'Forwarded'):
        assert refused(scan(client, 'not-a-token', '127.0.0.1', {header: HOME}))


def test_trusted_proxy_uses_the_forwarded_client_address(tmp_path):
    app = create_app({
        'TESTING': True,
        'SECRET_KEY': 'x' * 32,
        'SQLALCHEMY_DATABASE_URI': f"sqlite:///{(tmp_path / 'proxy.db').as_posix()}",
        'TRUSTED_PROXY_COUNT': 1,
        'SCAN_ALLOWED_NETWORKS': '203.0.113.0/24',
    })
    with app.app_context():
        client = app.test_client()
        assert scan(client, 'not-a-token', '127.0.0.1', {'X-Forwarded-For': CAMPUS}).status_code == 404
        assert refused(scan(client, 'not-a-token', '127.0.0.1', {'X-Forwarded-For': HOME}))
        db.session.remove()
        db.engine.dispose()
