"""WSGI entry point, e.g. `waitress-serve --port=5000 wsgi:app` or `gunicorn wsgi:app`.

Also lets `flask db ...` and `flask init-db` find the app without FLASK_APP.
"""
from attendqr import create_app

app = application = create_app()
