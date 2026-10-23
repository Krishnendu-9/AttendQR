# Project Structure

AttendQR is a Flask application built with an application factory and blueprints.

```text
AttendQR/
  attendqr/
    __init__.py         create_app(): config, extensions, blueprints, request guards, template helpers
    config.py           Config class read from environment variables / .env
    extensions.py       db, login_manager, migrate; SQLite foreign-key pragma
    models.py           SQLAlchemy models
    access.py           Roles, scope checks, visible_*_query() helpers, display helpers, activity log
    security.py         Secret key, CSRF, safe redirects, password policy, CSP/security headers, login throttling
    schema.py           prepare_database(): migrations, upgrade of pre-migration databases, first Principal
    utils.py            Time, normalisation, natural sorting, date-range parsing
    services/
      network.py        SCAN_ALLOWED_NETWORKS parsing and the scan address check
      qr.py             Rotating QR tokens, scan URLs, scan grants, device binding
      reports.py        Attendance summaries, report filters, Excel/PDF export
      roster.py         Student login accounts and bulk import
      retention.py      Clearing scan IP addresses after SCAN_IP_RETENTION_DAYS
    blueprints/
      auth.py           /login, /logout, /account
      main.py           Dashboards; /healthz for container health checks
      academics.py      Departments, semesters, subjects, teachers
      students.py       Student records, import, password and device resets
      attendance.py     QR sessions (/generate_qr) and the student scan flow (/scan, /attend, /mark_attendance)
      reports.py        Reports, detailed view, exports
      accounts.py       Staff account management (/add_admin)
      api.py            JSON endpoints used by the pages (/api/...)
  migrations/           Alembic environment and versions (Flask-Migrate)
  templates/            Jinja2 templates; _head.html and _pagination.html are shared partials
  static/
    app.css             Application styles
    js/app.js           Shared page helpers (dropdown loading, password meter, confirmations)
    vendor/             Bootstrap, Bootstrap Icons, and Inter, served locally
  tests/                pytest suite (conftest.py builds a fresh migrated database per test)
  run.py                Local/LAN server (waitress; debug server on localhost with FLASK_DEBUG=1)
  wsgi.py               WSGI entry point; also used by the `flask` CLI
  Dockerfile            Production image: lock-file dependencies, non-root, waitress via run.py
  compose.yaml          Runs the image with a data volume; requires APP_BASE_URL
  scripts/docker_smoke.py  End-to-end QR attendance check that CI runs against the image
```

## Runtime Data

`instance/` holds runtime data and is ignored by git:

- `attendance.db`: the SQLite database (default `DATABASE_URL`)
- `secret_key`: generated on first start when `SECRET_KEY` is not set. Keep it private; deleting it signs everyone out.

QR images are generated in memory and are never written to disk.

## Environment Files

- `.env.example`: committed template documenting every setting
- `.env`: local values; ignored by git

## Dependencies

- `requirements.txt`: direct dependencies with compatible version ranges
- `requirements.lock`: every package pinned, for reproducible installs and CI
- `requirements-dev.txt`: test and lint tools
- `pyproject.toml`: project metadata and pytest settings; reads its dependencies from `requirements.txt`
