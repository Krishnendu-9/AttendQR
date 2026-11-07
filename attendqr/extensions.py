import sqlite3

from flask_login import LoginManager
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event
from sqlalchemy.engine import Engine

db = SQLAlchemy()
login_manager = LoginManager()
migrate = Migrate()

# How long a write waits for another one to finish before failing with "database is locked".
SQLITE_BUSY_TIMEOUT_MS = 15000


@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        # Write-ahead logging lets reports read while students' attendance is being
        # written, instead of a long report query holding up every scan in the class.
        # The setting is stored in the database file (in-memory databases ignore it).
        # WAL needs a local disk; do not put the database on a network share.
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
        cursor.close()
