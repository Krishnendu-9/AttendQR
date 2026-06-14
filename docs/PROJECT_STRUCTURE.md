# Project Structure

The project uses a simple Flask layout suitable for a final-year academic project.

```text
minor_project/
  app.py
  models.py
  config.py
  run.py
  wsgi.py
  requirements.txt
  README.md
  .env.example
  templates/
  static/
  docs/
  tests/
```

## Important Files

- `app.py`: Flask app, routes, RBAC checks, QR generation, report export, and startup migration.
- `models.py`: SQLAlchemy database models.
- `config.py`: Flask configuration and database URL.
- `run.py`: local development entrypoint.
- `wsgi.py`: deployment entrypoint.
- `templates/`: Jinja2 templates.
- `static/app.css`: shared UI styling.
- `static/qrcodes/`: runtime-generated QR images.
- `tests/smoke_test.py`: basic verification script.

## Runtime Data

The SQLite database lives inside `instance/`. This folder is ignored by git because it is runtime data.

Generated QR images are runtime artifacts and should not be committed.
