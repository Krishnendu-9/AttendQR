"""flask backup-db: a consistent copy of the SQLite database, taken while the app runs."""
import os
import sqlite3

from attendqr.services.backup import _BACKUP_NAME


def test_backup_contains_the_data(app, data, tmp_path):
    target = tmp_path / 'copy.db'
    result = app.test_cli_runner().invoke(args=['backup-db', '--output', str(target)])
    assert result.exit_code == 0, result.output
    assert 'Backed up the database' in result.output
    with sqlite3.connect(target) as copy:
        assert copy.execute("SELECT COUNT(*) FROM student").fetchone()[0] == 3
        assert copy.execute("SELECT version_num FROM alembic_version").fetchone()[0]


def test_backup_never_overwrites_a_file(app, tmp_path):
    target = tmp_path / 'copy.db'
    target.write_text('keep me')
    result = app.test_cli_runner().invoke(args=['backup-db', '--output', str(target)])
    assert result.exit_code != 0 and 'already exists' in result.output
    assert target.read_text() == 'keep me'


def test_default_backups_are_named_by_time_and_pruned(app, monkeypatch, tmp_path):
    monkeypatch.setattr('attendqr.services.backup.default_backup_dir', lambda: str(tmp_path / 'backups'))
    folder = tmp_path / 'backups'
    folder.mkdir()
    for stamp in ('20260101-080000', '20260102-080000', '20260103-080000'):
        (folder / f'attendance-{stamp}.db').write_bytes(b'')
    (folder / 'notes.txt').write_text('not a backup')

    result = app.test_cli_runner().invoke(args=['backup-db', '--keep', '2'])
    assert result.exit_code == 0, result.output
    names = sorted(os.listdir(folder))
    backups = [name for name in names if _BACKUP_NAME.fullmatch(name)]
    assert len(backups) == 2 and 'attendance-20260103-080000.db' in backups
    assert 'notes.txt' in names
