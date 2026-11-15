"""Institution settings the Principal changes in the app (the Settings page, /settings).

Each option has a server default, read from app.config when it is asked for: an
environment variable (TIMEZONE, ELIGIBILITY_PERCENT, CONDONATION_PERCENT) or a value in
config.py. A row in institution_setting overrides it, and "Reset to default" deletes the
row. So a college that changes nothing behaves exactly as before, and the environment
still gives the starting value.

Security options (scan networks, QR timings, two-step sign-in, idle sign-out, cookies)
are never settings here. They stay with whoever runs the server.

Reads must be cheap: to_local() asks for the time zone for every timestamp a page shows.
All rows are loaded with one query, once per app context (every request has its own),
and kept in flask.g; saving clears that copy.
"""
import json
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import Any
from zoneinfo import available_timezones

from flask import current_app, g

from ..access import log_activity
from ..extensions import db
from ..models import InstitutionSetting
from ..utils import normalize_text, utc_now

WEEKDAYS = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday')
MAX_INSTITUTION_NAME_LENGTH = 80
_CACHE_KEY = 'institution_settings'


class SettingError(ValueError):
    """A value that cannot be saved. `key` names the field the message belongs to."""

    def __init__(self, message, key=None):
        super().__init__(message)
        self.key = key


# ── Parsing: form text, stored JSON or config values to a clean value ──

def _text(value):
    text = normalize_text(value)
    if len(text) > MAX_INSTITUTION_NAME_LENGTH:
        raise SettingError(f"Keep it under {MAX_INSTITUTION_NAME_LENGTH} characters.")
    return text


def _whole_number(value):
    if isinstance(value, bool):
        raise SettingError("Enter a whole number.")
    if isinstance(value, int):
        return value
    text = str(value if value is not None else '').strip().rstrip('%').strip()
    if not text.isdigit():
        raise SettingError("Enter a whole number.")
    return int(text)


def _percent(low, high):
    def parse(value):
        number = _whole_number(value)
        if not low <= number <= high:
            raise SettingError(f"Enter a percentage from {low} to {high}.")
        return number
    return parse


@lru_cache(maxsize=1)
def timezone_names():
    return tuple(sorted(available_timezones()))


def _timezone(value):
    name = str(value or '').strip()
    if name not in timezone_names():
        raise SettingError("Choose a time zone from the list, such as Asia/Kolkata.")
    return name


def _flag(value):
    """On or off: a bool, or the form's list of values (a hidden "0" before the checkbox's "1")."""
    if isinstance(value, bool):
        return value
    values = value if isinstance(value, (list, tuple)) else [value]
    return any(str(item).strip().lower() in ('1', 'true', 'on', 'yes') for item in values)


def _word(value):
    """A term or role name: one to 30 characters."""
    text = normalize_text(value)
    if not text:
        raise SettingError("Enter a word; Reset to default brings back the usual one.")
    if len(text) > 30:
        raise SettingError("Keep it under 30 characters.")
    return text


def _weekday(value):
    day = _whole_number(value)
    if not 0 <= day <= 6:
        raise SettingError("Choose a day of the week.")
    return day


def _weekdays(value):
    items = value if isinstance(value, (list, tuple, set)) else [value]
    days = sorted({_weekday(item) for item in items if str(item).strip()})
    if not days:
        raise SettingError("Choose at least one working day.")
    return days


# ── The registry ─────────────────────────────────────────────

@dataclass(frozen=True)
class SettingSpec:
    key: str
    label: str
    section: str
    help: str
    kind: str          # how the page shows it: text, percent, timezone, weekdays, weekday, bool, word
    config_key: str    # where the server default comes from
    parse: Callable[[Any], Any]
    config_path: tuple = ()  # inside a config dict: ('semester', 0) is TERMS['semester'][0]

    def default(self) -> Any:
        value = current_app.config[self.config_key]
        for part in self.config_path:
            value = value[part]
        return sorted(value) if self.kind == 'weekdays' else value


SECTIONS = (
    ('institution', 'Institution', 'How the institution is named in the app and in exported reports.'),
    ('attendance', 'Attendance rules',
     'The percentages students are measured against. Changing them changes every report at once.'),
    ('calendar', 'Calendar', 'The time zone dates and times follow, and the shape of the week.'),
    ('routine', 'Routine', 'Who may change the class routine for one date.'),
    ('terms', 'Terms', ('The words the app uses, for an institution that says "Course" or "Batch". Web addresses '
                       'and earlier activity log entries keep the usual words.')),
)

SETTINGS = {spec.key: spec for spec in (
    SettingSpec('institution_name', 'Institution name', 'institution',
                'Shown in the sidebar, page titles and exported reports. Leave it empty to show only AttendQR.',
                'text', 'INSTITUTION_NAME', _text),
    SettingSpec('attendance_target', 'Attendance target', 'attendance',
                'Students below it are warned on their dashboard, and the shortage list starts from it.',
                'percent', 'ATTENDANCE_TARGET', _percent(1, 99)),
    SettingSpec('eligibility_percent', 'Exam eligibility', 'attendance',
                'At or above this a student may sit the exams.',
                'percent', 'ELIGIBILITY_PERCENT', _percent(1, 100)),
    SettingSpec('condonation_percent', 'Condonation limit', 'attendance',
                'From here up to the eligibility percentage, the HOD or Principal may condone a shortage with a '
                'reason. Below it a student is detained.',
                'percent', 'CONDONATION_PERCENT', _percent(1, 100)),
    SettingSpec('timezone', 'Time zone', 'calendar',
                ('Times are stored in UTC and shown in this zone. "Today", report dates and leave days follow it. '
                 'Set it before classes start: changing it later moves past leave days and report dates too.'),
                'timezone', 'TIMEZONE', _timezone),
    SettingSpec('working_days', 'Working days', 'calendar',
                'The days classes can be held. The class routine will use them.',
                'weekdays', 'WORKING_DAYS', _weekdays),
    SettingSpec('week_starts_on', 'First day of the week', 'calendar',
                ('Weekly analytics, the integrity report and odd and even weeks in the routine count weeks from '
                 'this day. Changing it during a term can swap odd and even weeks in published routines, past '
                 'weeks included.'),
                'weekday', 'WEEK_STARTS_ON', _weekday),
    SettingSpec('teachers_change_own_classes', 'Teachers may change their own classes', 'routine',
                'A teacher can cancel or move a class they teach, for one date and with a reason, until the class '
                'ends. Substitutes, extra classes and changes to past classes are always for the HOD or Principal.',
                'bool', 'TEACHERS_CHANGE_OWN_CLASSES', _flag),
)}


TERM_NAMES = {'department': 'Department', 'semester': 'Semester', 'subject': 'Subject', 'group': 'Group',
              'paper_code': 'Paper code'}
ROLE_KEYS = {'principal': 'Principal', 'hod': 'HOD', 'teacher': 'Teacher', 'student': 'Student'}
for _name, _label in TERM_NAMES.items():
    for _index, _form in ((0, ''), (1, 's')):
        _key = f'term_{_name}{_form}'
        SETTINGS[_key] = SettingSpec(
            _key, f"{_label}{' (plural)' if _index else ''}", 'terms',
            f'The word for "{_label.lower()}{"s" if _index else ""}".', 'word', 'TERMS', _word, (_name, _index))
for _role, _label in ROLE_KEYS.items():
    SETTINGS[f'role_{_role}'] = SettingSpec(f'role_{_role}', f"Role: {_label}", 'terms',
                                            f'What the {_label} role is called.', 'word', 'ROLE_NAMES', _word, (_role,))


def term(name, plural=False):
    """The institution's word for a thing ("Semester", or "Batch"), singular or plural."""
    return get_setting(f"term_{name}{'s' if plural else ''}")


def terms(name):
    return term(name, plural=True)


def _cross_error(value_of):
    """(key, message) when values break a rule between settings, else None."""
    condonation, eligibility = value_of('condonation_percent'), value_of('eligibility_percent')
    if condonation > eligibility:
        return 'condonation_percent', (f"The condonation limit cannot be above the exam eligibility percentage "
                                       f"({eligibility}%).")
    return None


def display(key, value):
    """A value as people read it, e.g. "80%" or "Mon, Tue, Wed"."""
    kind = SETTINGS[key].kind
    if kind == 'percent':
        return f"{value}%"
    if kind == 'weekdays':
        return ', '.join(WEEKDAYS[day][:3] for day in value)
    if kind == 'weekday':
        return WEEKDAYS[value]
    if kind == 'bool':
        return 'On' if value else 'Off'
    return value or '(none)'


# ── Reading ──────────────────────────────────────────────────

def _stored():
    """{key: value} of the valid rows, loaded once per app context."""
    stored = g.get(_CACHE_KEY)
    if stored is not None:
        return stored
    stored = {}
    # Never flush a view's half-done work just to read the settings.
    with db.session.no_autoflush:
        rows = db.session.query(InstitutionSetting.key, InstitutionSetting.value).all()
    for key, raw in rows:
        spec = SETTINGS.get(key)
        if spec is None:
            continue  # saved by a newer version of the app
        try:
            stored[key] = spec.parse(json.loads(raw))
        except (ValueError, TypeError):
            current_app.logger.warning("Ignoring the saved setting %s: %r is not a valid value.", key, raw)
    # A changed server default can break a rule with a saved value; then use both defaults.
    if _cross_error(lambda key: stored[key] if key in stored else SETTINGS[key].default()):
        current_app.logger.warning("The saved condonation and eligibility percentages conflict with the server "
                                   "defaults; using the defaults for both until they are saved again.")
        stored.pop('condonation_percent', None)
        stored.pop('eligibility_percent', None)
    g.setdefault(_CACHE_KEY, stored)
    return stored


def forget_cached():
    """Drop this context's copy, so the next read sees the database."""
    g.pop(_CACHE_KEY, None)


def get_setting(key) -> Any:
    """The value in use: the one saved on the Settings page, else the server default."""
    stored = _stored()
    return stored[key] if key in stored else SETTINGS[key].default()


def institution_name():
    return get_setting('institution_name')


# ── Changing ─────────────────────────────────────────────────

def _log_change(key, old, new, how='Changed'):
    log_activity('setting_changed', f"{how} the setting {SETTINGS[key].label}: {display(key, old)} to "
                 f"{display(key, new)}.", 'setting')


def save_settings(submitted, user):
    """Validate `submitted` ({key: raw value}) as a whole and store what changed.

    Returns the keys that changed. Raises SettingError (with the field's key) and stores
    nothing if any value is invalid. The caller commits.
    """
    current = {key: get_setting(key) for key in SETTINGS}
    new = dict(current)
    for key, raw in submitted.items():
        try:
            new[key] = SETTINGS[key].parse(raw)
        except SettingError as exc:
            raise SettingError(str(exc), key) from None
    problem = _cross_error(new.__getitem__)
    if problem:
        raise SettingError(problem[1], problem[0])

    changed = [key for key in SETTINGS if key in submitted and new[key] != current[key]]
    for key in changed:
        row = db.session.get(InstitutionSetting, key)
        if row is None:
            row = InstitutionSetting(key=key)
            db.session.add(row)
        row.value = json.dumps(new[key])
        row.updated_at = utc_now()
        row.updated_by_user_id = user.id
        _log_change(key, current[key], new[key])
    forget_cached()
    return changed


def reset_setting(key):
    """Go back to the server default by deleting the saved value. Returns False if none was saved.

    Raises SettingError if the default would break a rule with another saved value. The caller commits.
    """
    row = db.session.get(InstitutionSetting, key)
    if row is None:
        return False
    old = get_setting(key)
    default = SETTINGS[key].default()
    problem = _cross_error(lambda other: default if other == key else get_setting(other))
    if problem:
        raise SettingError(f"The server default ({display(key, default)}) cannot be used: {problem[1]}", key)
    db.session.delete(row)
    _log_change(key, old, default, how='Reset')
    forget_cached()
    return True


def saved_rows():
    """{key: InstitutionSetting} for the options changed in the app, for the Settings page."""
    return {row.key: row for row in InstitutionSetting.query.all()}
