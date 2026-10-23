"""The first Principal account never starts with a published password."""
import logging
import os
import stat

import pytest

from attendqr import create_app
from attendqr.extensions import db
from attendqr.models import Admin
from conftest import login, post

EMAIL = 'principal@example.com'
NEW_PASSWORD = 'Principal-2026-own'


def make_app(tmp_path, **overrides):
    config = {
        'TESTING': True,
        'SECRET_KEY': 'x' * 32,
        'SQLALCHEMY_DATABASE_URI': f"sqlite:///{(tmp_path / 'first.db').as_posix()}",
        'DEFAULT_PRINCIPAL_EMAIL': EMAIL,
    }
    config.update(overrides)
    return create_app(config)


@pytest.fixture
def run_app(tmp_path):
    """Create an app (running migrations and seeding), use it inside its context, then dispose of it."""
    apps = []

    def run(**overrides):
        app = make_app(tmp_path, **overrides)
        apps.append(app)
        return app
    yield run
    for app in apps:
        with app.app_context():
            db.session.remove()
            db.engine.dispose()


def can_log_in(app, password):
    with app.app_context():
        client = app.test_client()
        return login(client, EMAIL, password, expect_success=False).status_code == 302


@pytest.mark.parametrize('configured', [None, '', '   ', 'ChangeMe123!'])
def test_unset_or_placeholder_password_is_replaced_by_a_generated_one(run_app, initial_password_file, caplog, configured):
    overrides = {} if configured is None else {'DEFAULT_PRINCIPAL_PASSWORD': configured}
    with caplog.at_level(logging.WARNING):
        app = run_app(**overrides)

    password = initial_password_file.read_text(encoding='utf-8').strip()
    assert len(password) >= 20
    assert can_log_in(app, password)
    assert not can_log_in(app, 'ChangeMe123!')
    with app.app_context():
        assert Admin.query.filter_by(username=EMAIL).one().must_change_password

    # The log says where the password is, never what it is.
    assert str(initial_password_file) in caplog.text
    assert password not in caplog.text


@pytest.mark.skipif(os.name == 'nt', reason='Windows has no POSIX permission bits; the file inherits the folder ACL')
def test_password_file_is_readable_by_the_owner_only(run_app, initial_password_file):
    run_app()
    assert stat.S_IMODE(initial_password_file.stat().st_mode) == 0o600


def test_a_configured_password_is_used_and_no_file_is_written(run_app, initial_password_file):
    app = run_app(DEFAULT_PRINCIPAL_PASSWORD='Operator-Chosen-1')
    assert not initial_password_file.exists()
    assert can_log_in(app, 'Operator-Chosen-1')


def test_file_is_deleted_once_the_principal_changes_the_password(run_app, initial_password_file):
    app = run_app()
    password = initial_password_file.read_text(encoding='utf-8').strip()
    with app.app_context():
        client = app.test_client()
        login(client, EMAIL, password)
        response = post(client, '/account', {
            'current_password': password, 'new_password': NEW_PASSWORD, 'confirm_password': NEW_PASSWORD,
        })
        assert response.status_code == 302
    assert not initial_password_file.exists()
    assert can_log_in(app, NEW_PASSWORD)


def test_unused_file_survives_a_restart_and_is_not_regenerated(run_app, initial_password_file):
    run_app()
    password = initial_password_file.read_text(encoding='utf-8')
    run_app()
    assert initial_password_file.read_text(encoding='utf-8') == password


def test_stale_file_is_deleted_at_startup(run_app, initial_password_file):
    """If the password was replaced some other way, the next start removes the file."""
    from werkzeug.security import generate_password_hash

    app = run_app()
    with app.app_context():
        principal = Admin.query.filter_by(username=EMAIL).one()
        principal.password = generate_password_hash(NEW_PASSWORD)
        principal.must_change_password = False
        db.session.commit()
    assert initial_password_file.exists()

    run_app()
    assert not initial_password_file.exists()
