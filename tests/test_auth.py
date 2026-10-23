"""Login, sessions, passwords, redirects, secret key, and response headers."""
import pytest
from flask import Flask

from attendqr.extensions import db
from attendqr.models import Admin
from attendqr.security import configure_secret_key, is_safe_redirect_target, password_policy_error
from conftest import PASSWORD, login, post


@pytest.mark.parametrize('target, safe', [
    ('/students', True),
    ('/students?page=2', True),
    ('/\\evil.com', False),
    ('/\\/evil.com', False),
    ('//evil.com', False),
    ('https://evil.com/', False),
    ('javascript:alert(1)', False),
    ('/\t/evil.com', False),
    ('students', False),
    ('', False),
    (None, False),
])
def test_safe_redirect_targets(target, safe):
    assert is_safe_redirect_target(target) is safe


def test_login_ignores_backslash_open_redirect(app, data):
    response = post(app.test_client(), '/login', {
        'username': data.principal, 'password': PASSWORD, 'next': '/\\evil.com',
    })
    assert response.status_code == 302
    assert response.headers['Location'] == '/'


def test_login_errors_do_not_reveal_whether_account_exists(app, data):
    client = app.test_client()
    unknown = login(client, 'nobody@example.com', 'whatever1', expect_success=False).get_data(as_text=True)
    wrong = login(client, data.teacher, 'wrong-pass1', expect_success=False).get_data(as_text=True)
    assert 'Invalid email or password.' in unknown
    assert 'Invalid email or password.' in wrong


def test_inactive_account_is_only_revealed_with_correct_password(app, data):
    account = Admin.query.filter_by(username=data.teacher).one()
    account.is_active = False
    db.session.commit()
    client = app.test_client()
    assert 'inactive' not in login(client, data.teacher, 'wrong-pass1', expect_success=False).get_data(as_text=True)
    assert 'inactive' in login(client, data.teacher, PASSWORD, expect_success=False).get_data(as_text=True)


def test_lockout_is_per_device_so_others_cannot_lock_a_user_out(app, data):
    attacker = app.test_client()
    for _ in range(app.config['LOGIN_IP_FAILURE_LIMIT']):
        login(attacker, data.student, 'wrong-pass1', expect_success=False,
              environ_base={'REMOTE_ADDR': '10.0.0.66'})
    blocked = login(attacker, data.student, PASSWORD, expect_success=False,
                    environ_base={'REMOTE_ADDR': '10.0.0.66'})
    assert 'Too many failed attempts' in blocked.get_data(as_text=True)

    # The real student on their own phone is unaffected.
    login(app.test_client(), data.student, PASSWORD, environ_base={'REMOTE_ADDR': '10.0.0.7'})


def test_password_reset_by_admin_unlocks_account(app, data, client_for):
    for _ in range(app.config['LOGIN_IP_FAILURE_LIMIT']):
        login(app.test_client(), data.teacher, 'wrong-pass1', expect_success=False)
    hod = client_for(data.hod)
    assert 'Locked' in hod.get('/add_admin').get_data(as_text=True)
    target = Admin.query.filter_by(username=data.teacher).one()
    post(hod, f'/reset_admin_password/{target.id}')
    assert 'Locked' not in hod.get('/add_admin').get_data(as_text=True)


def test_logout_requires_post(data, client_for):
    client = client_for(data.teacher)
    assert client.get('/logout').status_code == 405
    assert post(client, '/logout').status_code == 302
    assert client.get('/').status_code == 302


def test_post_without_csrf_token_is_rejected(data, client_for):
    client = client_for(data.principal)
    assert client.post('/departments', data={'branch': 'X', 'name': 'Y'}).status_code == 400


def test_password_change_signs_out_other_sessions(app, data, client_for):
    laptop = client_for(data.teacher)
    phone = client_for(data.teacher)

    response = post(laptop, '/account', {
        'current_password': PASSWORD, 'new_password': 'NewPass99', 'confirm_password': 'NewPass99',
    }, follow_redirects=True)
    assert 'Password updated successfully.' in response.get_data(as_text=True)

    assert laptop.get('/account').status_code == 200        # the session that changed it stays in
    assert phone.get('/account').status_code == 302         # every other session is signed out
    login(app.test_client(), data.teacher, PASSWORD, expect_success=False)
    login(app.test_client(), data.teacher, 'NewPass99')


def test_new_password_must_follow_policy(data, client_for):
    client = client_for(data.student)
    for weak, message in [('short1', 'at least 8'), ('lettersonly', 'letter and one number')]:
        response = post(client, '/account', {
            'current_password': PASSWORD, 'new_password': weak, 'confirm_password': weak,
        })
        assert message in response.get_data(as_text=True), weak


def test_password_may_not_equal_roll_number_or_email(app):
    assert 'roll number' in password_policy_error('MCA2026001', ['student@example.com', 'mca2026001'])
    assert password_policy_error('Different2026', ['student@example.com', 'MCA2026001']) is None


def test_admin_reset_and_deactivation_end_existing_sessions(data, client_for):
    teacher = client_for(data.teacher)
    hod = client_for(data.hod)
    account = Admin.query.filter_by(username=data.teacher).one()

    post(hod, f'/reset_admin_password/{account.id}')
    assert teacher.get('/generate_qr').status_code == 302
    assert hod.get('/add_admin').status_code == 200  # the HOD's own session is unaffected

    teacher = client_for(data.other_teacher)
    other = Admin.query.filter_by(username=data.other_teacher).one()
    post(client_for(data.principal), f'/toggle_admin_status/{other.id}')
    assert teacher.get('/generate_qr').status_code == 302


def test_must_change_password_is_enforced(app, data):
    account = Admin.query.filter_by(username=data.teacher).one()
    account.must_change_password = True
    db.session.commit()

    client = app.test_client()
    login(client, data.teacher)
    response = client.get('/reports?department_id=1')
    assert response.status_code == 302
    assert '/account?next=' in response.headers['Location']
    assert client.get('/api/teachers/1').status_code == 403
    assert client.get('/account').status_code == 200

    response = post(client, '/account', {
        'current_password': PASSWORD, 'new_password': 'Changed123', 'confirm_password': 'Changed123',
        'next': '/reports',
    })
    assert response.headers['Location'].endswith('/reports')
    assert client.get('/reports').status_code == 200


def test_default_principal_must_change_password(app):
    principal = Admin.query.filter_by(username='principal@example.com').one()
    assert principal.must_change_password


def test_security_headers(data, client_for):
    response = client_for(data.principal).get('/')
    csp = response.headers['Content-Security-Policy']
    script_src = csp.split('script-src')[1].split(';')[0]
    assert "'nonce-" in script_src and 'unsafe-inline' not in script_src
    assert response.headers['X-Frame-Options'] == 'DENY'
    assert response.headers['Cache-Control'] == 'no-store'
    assert response.headers['Referrer-Policy'] == 'same-origin'


def test_inline_scripts_carry_the_request_nonce(data, client_for):
    response = client_for(data.principal).get('/generate_qr')
    nonce = response.headers['Content-Security-Policy'].split("'nonce-")[1].split("'")[0]
    html = response.get_data(as_text=True)
    assert html.count('<script nonce="') == html.count(f'<script nonce="{nonce}"') >= 1
    assert 'onclick=' not in html and 'onsubmit=' not in html


def test_pages_use_local_assets_only(data, client_for):
    html = client_for(data.principal).get('/').get_data(as_text=True)
    assert 'cdn.jsdelivr.net' not in html and 'fonts.googleapis.com' not in html
    assert '/static/vendor/bootstrap/bootstrap.min.css' in html


# ── Secret key ───────────────────────────────────────────────

def _bare_app(tmp_path, **config):
    app = Flask(__name__, instance_path=str(tmp_path))
    app.config.update(config)
    return app


def test_missing_secret_key_is_generated_once_and_persisted(tmp_path):
    first = _bare_app(tmp_path, SECRET_KEY=None)
    configure_secret_key(first)
    second = _bare_app(tmp_path, SECRET_KEY=None)
    configure_secret_key(second)
    assert len(first.config['SECRET_KEY']) == 64
    assert first.config['SECRET_KEY'] == second.config['SECRET_KEY']
    assert (tmp_path / 'secret_key').read_text().strip() == first.config['SECRET_KEY']


def test_published_placeholder_secret_key_is_refused_in_production(tmp_path):
    app = _bare_app(tmp_path, SECRET_KEY='attendance-system-secret-key')
    with pytest.raises(RuntimeError, match='placeholder'):
        configure_secret_key(app)


def test_explicit_secret_key_is_used(tmp_path):
    app = _bare_app(tmp_path, SECRET_KEY='a-long-random-value-from-the-environment')
    configure_secret_key(app)
    assert app.config['SECRET_KEY'] == 'a-long-random-value-from-the-environment'
    assert not (tmp_path / 'secret_key').exists()


def test_health_check_needs_no_login_and_checks_the_database(app):
    response = app.test_client().get('/healthz')
    assert response.status_code == 200 and response.get_json() == {'status': 'ok'}
