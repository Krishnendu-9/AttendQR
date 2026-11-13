"""Two-step sign-in with an authenticator app: setup, sign-in, recovery codes, required roles, resets."""
import html
import re
import time

import pyotp
import pytest

from attendqr.extensions import db
from attendqr.models import ActivityLog, User
from conftest import PASSWORD, login, post


def page(response):
    return html.unescape(response.get_data(as_text=True))


def code(secret, steps_ahead=0):
    return pyotp.TOTP(secret).at(time.time() + 30 * steps_ahead)


def enable(client, username):
    """Turn two-step sign-in on through the setup page; returns (secret, recovery codes)."""
    client.get('/account/two-step')
    secret = User.query.filter_by(username=username).one().totp_pending_secret
    response = post(client, '/account/two-step', {'action': 'enable', 'code': code(secret)})
    codes = re.findall(r'<li>([A-Z0-9]{5}-[A-Z0-9]{5})</li>', response.get_data(as_text=True))
    return secret, codes


def password_step(app, username):
    client = app.test_client()
    response = login(client, username, expect_success=False)
    return client, response


# ── Setup ────────────────────────────────────────────────────

def test_setup_shows_a_qr_code_and_turns_on_with_a_valid_code(data, client_for):
    client = client_for(data.teacher)
    text = page(client.get('/account/two-step'))
    assert 'data:image/png;base64' in text and 'Zoho' in text

    secret, codes = enable(client, data.teacher)

    user = User.query.filter_by(username=data.teacher).one()
    assert user.totp_secret == secret and user.totp_pending_secret is None and user.totp_enabled_at
    assert len(codes) == 10 and len(set(codes)) == 10
    assert codes[0] not in (user.recovery_codes or '')  # only hashes are stored
    assert ActivityLog.query.filter_by(action='two_factor_enabled').count() == 1


def test_a_wrong_code_does_not_turn_it_on(data, client_for):
    client = client_for(data.teacher)
    client.get('/account/two-step')
    response = post(client, '/account/two-step', {'action': 'enable', 'code': '000000'})
    assert 'did not match' in page(response)
    assert User.query.filter_by(username=data.teacher).one().totp_secret is None


def test_students_have_no_two_step_page(data, client_for):
    assert client_for(data.student).get('/account/two-step').status_code == 404


# ── Signing in ───────────────────────────────────────────────

def test_sign_in_needs_the_code_after_the_password(app, data, client_for):
    secret, _ = enable(client_for(data.teacher), data.teacher)

    client, response = password_step(app, data.teacher)
    assert response.headers['Location'].endswith('/login/two-step')
    assert client.get('/').status_code == 302  # not signed in yet

    wrong = post(client, '/login/two-step', {'code': '000000'})
    assert 'not right' in page(wrong)
    ok = post(client, '/login/two-step', {'code': code(secret, 1)})
    assert ok.status_code == 302 and client.get('/').status_code == 200
    assert 'with two-step sign-in' in ActivityLog.query.filter_by(action='login_succeeded').order_by(
        ActivityLog.id.desc()).first().description


def test_a_code_works_only_once(app, data, client_for):
    secret, _ = enable(client_for(data.teacher), data.teacher)
    used = code(secret, 1)
    first, _ = password_step(app, data.teacher)
    post(first, '/login/two-step', {'code': used})

    second, _ = password_step(app, data.teacher)
    assert 'not right' in page(post(second, '/login/two-step', {'code': used}))


def test_a_recovery_code_signs_in_once(app, data, client_for):
    _, codes = enable(client_for(data.teacher), data.teacher)
    client, _ = password_step(app, data.teacher)
    response = post(client, '/login/two-step', {'code': codes[0].lower().replace('-', ' ')}, follow_redirects=True)
    assert '9 left' in page(response)

    again, _ = password_step(app, data.teacher)
    assert 'not right' in page(post(again, '/login/two-step', {'code': codes[0]}))


def test_wrong_codes_count_towards_the_sign_in_pause(app, data, client_for):
    enable(client_for(data.teacher), data.teacher)
    client, _ = password_step(app, data.teacher)
    for _ in range(5):
        post(client, '/login/two-step', {'code': '000000'})
    assert 'Too many failed attempts' in page(post(client, '/login/two-step', {'code': '000000'}))


def test_the_code_step_expires_and_ends_with_a_password_reset(app, data, client_for):
    secret, _ = enable(client_for(data.hod), data.hod)
    client, _ = password_step(app, data.hod)
    with client.session_transaction() as sess:
        sess['two_step'] = {**sess['two_step'], 'expires': int(time.time()) - 1}
    assert client.get('/login/two-step').headers['Location'].endswith('/login')

    client, _ = password_step(app, data.hod)
    post(client_for(data.principal), f"/reset_admin_password/{User.query.filter_by(username=data.hod).one().id}")
    assert post(client, '/login/two-step', {'code': code(secret, 1)}).headers['Location'].endswith('/login')


# ── Required roles ───────────────────────────────────────────

@pytest.fixture
def required(app):
    app.config['TOTP_REQUIRED_ROLES'] = ('principal', 'hod')


def test_required_roles_must_set_it_up_before_anything_else(required, data, client_for):
    hod = client_for(data.hod)
    response = hod.get('/students')
    assert response.headers['Location'].endswith('/account/two-step')
    assert hod.get('/account/two-step').status_code == 200
    assert hod.get('/api/teachers/1', headers={'Accept': 'application/json'}).status_code == 403
    # Teachers are not required to.
    assert client_for(data.teacher).get('/students').status_code == 200

    enable(hod, data.hod)
    assert hod.get('/students').status_code == 200


def test_required_roles_cannot_turn_it_off(required, data, client_for):
    hod = client_for(data.hod)
    secret, _ = enable(hod, data.hod)
    response = post(hod, '/account/two-step', {'action': 'disable', 'password': PASSWORD, 'code': code(secret, 1)})
    assert 'must keep two-step sign-in on' in page(response)


def test_optional_users_can_turn_it_off_with_password_and_code(data, client_for):
    teacher = client_for(data.teacher)
    secret, _ = enable(teacher, data.teacher)
    assert 'not right' in page(post(teacher, '/account/two-step', {'action': 'disable', 'password': 'wrong', 'code': code(secret, 1)}))
    post(teacher, '/account/two-step', {'action': 'disable', 'password': PASSWORD, 'code': code(secret, 1)})
    db.session.expire_all()
    assert User.query.filter_by(username=data.teacher).one().totp_secret is None


def test_new_recovery_codes_replace_the_old_ones(app, data, client_for):
    teacher = client_for(data.teacher)
    secret, old_codes = enable(teacher, data.teacher)
    response = post(teacher, '/account/two-step', {'action': 'recovery', 'code': code(secret, 1)})
    new_codes = re.findall(r'<li>([A-Z0-9]{5}-[A-Z0-9]{5})</li>', response.get_data(as_text=True))
    assert len(new_codes) == 10 and not set(new_codes) & set(old_codes)

    client, _ = password_step(app, data.teacher)
    assert 'not right' in page(post(client, '/login/two-step', {'code': old_codes[0]}))


def test_principal_resets_a_lost_phone(data, client_for):
    _secret, _ = enable(client_for(data.teacher), data.teacher)
    teacher = User.query.filter_by(username=data.teacher).one()
    old_token = teacher.session_token

    post(client_for(data.principal), f'/accounts/{teacher.id}/reset-two-step')

    db.session.expire_all()
    assert teacher.totp_secret is None and teacher.recovery_codes is None and teacher.session_token != old_token
    assert ActivityLog.query.filter_by(action='two_factor_reset').count() == 1
    assert post(client_for(data.other_teacher), f'/accounts/{teacher.id}/reset-two-step').status_code == 403
