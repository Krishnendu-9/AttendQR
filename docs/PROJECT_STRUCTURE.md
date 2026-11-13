# Project Structure

AttendQR is a Flask application built with an application factory and blueprints.

```text
AttendQR/
  attendqr/
    __init__.py         create_app(): config checks, extensions, blueprints, request guards, error pages, template helpers, CLI
    config.py           Config class read from environment variables / .env
    extensions.py       db, login_manager, migrate; SQLite foreign keys, WAL mode and busy timeout
    models.py           SQLAlchemy models
    access.py           Roles, scope checks, visible_*_query() helpers, display helpers, activity log
    security.py         Secret key, CSRF, safe redirects, password policy, temporary passwords, CSP/security headers,
                        idle sign-out, login throttling
    schema.py           prepare_database(): migrations, upgrade of pre-migration databases, first Principal
    observability.py    Request ids, JSON logs, the access log, Prometheus metrics (/metrics)
    utils.py            Time (stored UTC, shown in TIMEZONE), normalisation, natural sorting, date parsing
    services/
      network.py        SCAN_ALLOWED_NETWORKS parsing and the scan address check
      qr.py             Rotating QR tokens, scan URLs, scan grants, device binding
      reports.py        Attendance summaries, report filters, Excel/PDF export
      analytics.py      Weekly trend, per-subject rates and students at risk, from the report matrix
      leave.py          Approved-leave windows used by every percentage, and leave request validation
      demo.py           Sample college for demonstrations (`flask seed-demo`)
      spot_checks.py    Random in-class checks of students marked present
      integrity.py      The integrity report: failed checks, buddy pairs, new phones, manual entries
      eligibility.py    Exam eligibility and condonation
      audit.py          The hash-chained activity log: sealing and `flask verify-audit`
      locks.py          Locked semesters
      two_factor.py     Two-step sign-in (TOTP) and recovery codes
      roster.py         Student login accounts and bulk import
      enrollment.py     Class membership over time, so moved students stay in their old class's reports
      retention.py      Clearing scan IP addresses after SCAN_IP_RETENTION_DAYS
      backup.py         Consistent SQLite backups (`flask backup-db`)
    blueprints/
      auth.py           /login, /logout, /account
      main.py           Dashboards; /healthz for container health checks
      academics.py      Departments, semesters, subjects, teachers
      students.py       Student records, import, password and device resets
      attendance.py     QR sessions (/generate_qr) and the student scan flow (/scan, /attend, /mark_attendance)
      reports.py        Reports, detailed view, exports, analytics, integrity, exam eligibility
      leave.py          Leave requests: students apply, the HOD or Principal decides (/leave)
      accounts.py       Staff account management (/accounts)
      api.py            JSON endpoints used by the pages (/api/...)
  migrations/           Alembic environment and versions (Flask-Migrate)
  templates/            Jinja2 templates (see "Frontend" below)
    layouts/            app.html (sidebar, header, content) and auth.html (sign in, errors)
    components/         Shared macros (ui.html, forms.html), navigation (nav.html), shell parts, Lucide icons
    students/           Student view, add/edit form and not-found pages
    dev/components.html Component gallery at /dev/components (debug mode only)
  assets/tailwind.css   Design tokens (Campus Blue, light and dark) and component styles; source of static/css/app.css
  static/
    css/app.css         Built stylesheet (committed; rebuild with scripts/tailwind.py)
    js/theme.js         Applies the Light / Dark / System theme before the page paints
    js/ui.js            Alpine components and page behaviour (menus, dialogs, toasts, palette, list filters)
    js/app.js           Shared page helpers (dropdown loading, password meter)
    js/charts.js        Hover and keyboard tooltips for the analytics charts
    vendor/             Alpine.js (CSP build), htmx and the Inter font, served locally
  tests/                pytest suite (conftest.py builds a fresh migrated database per test; TEST_DATABASE_URL
                        runs it on PostgreSQL)
  run.py                Local/LAN server (waitress; debug server on localhost with FLASK_DEBUG=1)
  wsgi.py               WSGI entry point; also used by the `flask` CLI
  Dockerfile            Production image: lock-file dependencies, non-root, waitress via run.py
  compose.yaml          Runs the image with a data volume; requires APP_BASE_URL
  scripts/docker_smoke.py  End-to-end QR attendance check that CI runs against the image
  scripts/load_test.py  A whole class through the attendance flow against the real server, with latency percentiles
  docs/                 DEPLOYMENT.md, GUIDE.md, THREAT_MODEL.md, RBAC.md, screenshots
  scripts/tailwind.py   Builds static/css/app.css with the pinned Tailwind standalone CLI (no Node.js)
  scripts/lucide_icons.py  Copies the Lucide icons the templates use into templates/components/icons/
```

## Frontend

The interface is server-rendered Jinja templates. There is no separate frontend project and no Node.js.

- **Styling:** Tailwind CSS v4 utilities over the design tokens in `assets/tailwind.css`. Templates use
  tokens only (`bg-card`, `text-muted-foreground`, `text-label-lg`, `shadow-card`); Tailwind's default
  colours, font sizes and shadows are switched off. Green, amber and red are for attendance status and
  errors only, never for buttons or links.
- **Rebuilding the CSS:** after changing classes in templates or `static/js`, or `assets/tailwind.css`, run
  `python scripts/tailwind.py` (or `--watch` while working). The first run downloads the pinned Tailwind
  binary into `tools/` (ignored by git) and checks its SHA-256. Commit the rebuilt `static/css/app.css`.
- **Behaviour:** Alpine.js, the CSP build, because the Content Security Policy forbids `eval`. Components are
  registered in `static/js/ui.js` with `Alpine.data()` and templates name them (`x-data="dropdown"`).
  Inside component methods use `this.$root`, not `this.$el` (that is the element that called the method).
  htmx swaps the list on list pages (search, filters, paging) and keeps the state in the URL.
- **No inline styles or scripts:** the policy blocks `style="..."` and un-nonced `<script>`;
  `tests/test_auth.py` checks every template. Page scripts go in `{% block scripts %}` with `nonce="{{ csp_nonce() }}"`.
- **Pages:** extend `layouts/app.html`, set `page_title` and `breadcrumbs` at the top, and build the page from
  the macros in `components/ui.html` and `components/forms.html`. Navigation (sidebar and Ctrl K palette) is
  defined once in `components/nav.html`. Attendance status badges come only from `attendance_badge()`.
- **Lists:** Students pages on the server (htmx swaps the list). Smaller lists (departments, semesters,
  subjects, teachers, accounts) load in full and filter instantly with `clientFilter` (ui.js); add and edit
  open a dialog via `record_opener()` / `record_dialog()` in `components/ui.html`. Students use full pages
  for view, add and edit.
- **Every page** uses `layouts/app.html`, or `layouts/auth.html` for sign in, errors and the student scan page.

## Runtime Data

`instance/` holds runtime data and is ignored by git:

- `attendance.db`: the SQLite database (default `DATABASE_URL`)
- `attendance.db-wal`, `attendance.db-shm`: SQLite's write-ahead log, present while the app runs. Back up with `flask backup-db`, not by copying files.
- `secret_key`: generated on first start when `SECRET_KEY` is not set. Keep it private; deleting it signs everyone out.
- `initial_principal_password`: the first Principal's one-time password; deleted once it has been replaced.
- `backups/`: written by `flask backup-db`.

QR images are generated in memory and are never written to disk.

## Environment Files

- `.env.example`: committed template documenting every setting
- `.env`: local values; ignored by git

## Dependencies

- `requirements.txt`: direct dependencies with compatible version ranges
- `requirements.lock`: every package pinned, for reproducible installs and CI
- `requirements-dev.txt`: test and lint tools
- `pyproject.toml`: project metadata and pytest settings; reads its dependencies from `requirements.txt`
