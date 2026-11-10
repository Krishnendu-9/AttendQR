"""Consistent copies of the SQLite database, safe to take while the app is running.

Copying attendance.db with the file manager while the app runs can produce a
broken copy (and misses changes still in the -wal file). SQLite's backup API
copies a consistent snapshot instead. `flask backup-db` runs it.
"""
import os
import re
import sqlite3

from flask import current_app

from ..extensions import db
from ..utils import ist_now

BACKUP_DIRNAME = 'backups'
_BACKUP_NAME = re.compile(r'^attendance-\d{8}-\d{6}\.db$')


class BackupError(RuntimeError):
    pass


def default_backup_dir():
    return os.path.join(current_app.instance_path, BACKUP_DIRNAME)


def backup_database(destination=None):
    """Write a snapshot of the database to `destination` (default: instance/backups/attendance-<time>.db)."""
    engine = db.engine
    if engine.dialect.name != 'sqlite':
        raise BackupError(
            f"backup-db only handles SQLite; this database is {engine.dialect.name}. "
            "Use its own tool, for example pg_dump for PostgreSQL."
        )
    if not engine.url.database or engine.url.database == ':memory:':
        raise BackupError("The database is in memory; there is nothing on disk to back up.")

    if destination is None:
        destination = os.path.join(default_backup_dir(), f"attendance-{ist_now():%Y%m%d-%H%M%S}.db")
    destination = os.path.abspath(destination)
    # Backups hold password hashes and personal data: only the server's account may read
    # them, like the secret key. (On Windows the file inherits the folder's permissions.)
    os.makedirs(os.path.dirname(destination), mode=0o700, exist_ok=True)
    try:
        os.close(os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    except FileExistsError:
        raise BackupError(f"{destination} already exists; choose another file name.") from None

    source = engine.raw_connection()
    try:
        # SQLite accepts the empty file just created and keeps its permissions.
        target = sqlite3.connect(destination)
        try:
            source.driver_connection.backup(target)
        finally:
            target.close()
    except Exception:
        if os.path.exists(destination):
            os.remove(destination)
        raise
    finally:
        source.close()
    return destination


def prune_backups(keep):
    """Delete all but the newest `keep` automatic backups in the default folder. Returns the deleted paths."""
    folder = default_backup_dir()
    if keep < 1 or not os.path.isdir(folder):
        return []
    names = sorted(name for name in os.listdir(folder) if _BACKUP_NAME.fullmatch(name))
    removed = []
    for name in names[:-keep]:
        path = os.path.join(folder, name)
        os.remove(path)
        removed.append(path)
    return removed
