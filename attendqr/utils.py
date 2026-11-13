"""Small helpers shared across the application.

Time: every timestamp is stored as a naive UTC datetime (SQLite keeps no time zone,
and naive values compare the same way on every database). Only display and
calendar dates use the college's time zone, the TIMEZONE setting.
"""
import re
from datetime import UTC, datetime, time
from functools import lru_cache
from typing import overload
from zoneinfo import ZoneInfo

from flask import current_app, has_app_context

DEFAULT_TIMEZONE = 'Asia/Kolkata'


def utc_now():
    """Current time as a naive UTC datetime, the form every timestamp is stored in."""
    return datetime.now(UTC).replace(tzinfo=None)


@lru_cache(maxsize=8)
def _zone(name):
    return ZoneInfo(name)


def local_zone():
    """The college's time zone (TIMEZONE setting)."""
    name = current_app.config.get('TIMEZONE') if has_app_context() else None
    return _zone(name or DEFAULT_TIMEZONE)


@overload
def to_local(dt: datetime) -> datetime: ...
@overload
def to_local(dt: None) -> None: ...
def to_local(dt):
    """A stored (naive UTC) datetime as naive local time, for display."""
    if dt is None:
        return None
    return dt.replace(tzinfo=UTC).astimezone(local_zone()).replace(tzinfo=None)


def local_to_utc(dt):
    """A naive local datetime as naive UTC, for storing or comparing with stored values."""
    return dt.replace(tzinfo=local_zone()).astimezone(UTC).replace(tzinfo=None)


def local_now():
    """Current local time (naive), for dates and times shown to people."""
    return to_local(utc_now())


def local_day_start_utc(day):
    """The UTC moment the local calendar `day` begins, for filtering stored timestamps by date."""
    return local_to_utc(datetime.combine(day, time.min))


def format_12h(dt, include_date=True):
    """A stored timestamp in local time, e.g. "12-11-2026 10:30 AM"."""
    if not dt:
        return ''
    fmt = '%d-%m-%Y %I:%M %p' if include_date else '%I:%M %p'
    return to_local(dt).strftime(fmt)


def normalize_text(value):
    return ' '.join(str(value or '').strip().split())


def normalize_email(value):
    return normalize_text(value).lower()


def normalize_roll_no(value):
    return normalize_text(value).upper()


def normalize_branch(value):
    return normalize_text(value).upper()


def normalize_course_name(value):
    return re.sub(r'\s*\(', ' (', normalize_text(value))


def normalize_paper_code(value):
    return normalize_text(value).upper()


def normalize_header(value):
    return str(value or '').strip().lower().replace(' ', '_')


def parse_int_value(value):
    if value is None:
        return None
    value = str(value).strip()
    if not value:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


_NATURAL_SPLIT = re.compile(r'(\d+)')


def natural_key(value):
    """Sort key that orders 'Semester 2' before 'Semester 10'."""
    return [int(part) if part.isdigit() else part.casefold() for part in _NATURAL_SPLIT.split(value or '')]


def sort_semesters(semesters):
    return sorted(semesters, key=lambda s: natural_key(s.name))


def infer_department_branch(course_name):
    title = normalize_course_name(course_name)
    match = re.search(r'\(([A-Za-z0-9&./-]+)\)\s*$', title)
    if match:
        return normalize_branch(match.group(1))
    if title.isupper() and ' ' not in title and len(title) <= 12:
        return title

    words = re.findall(r'[A-Za-z0-9]+', title)
    ignored = {'of', 'and', 'in', 'the', 'for'}
    acronym = ''.join(word[0] for word in words if word.lower() not in ignored).upper()
    return acronym[:12] if acronym else ''


def parse_iso_date(value):
    """A date from a form field in YYYY-MM-DD form, or None."""
    try:
        return datetime.strptime((value or '').strip(), '%Y-%m-%d').date()
    except ValueError:
        return None


def parse_date_range(date_from, date_to, today):
    """Validate report date filters.

    Returns (parsed_from, parsed_to, date_from, date_to, error) where invalid or
    rejected values are cleared so the form does not re-submit them.
    """
    error = None
    parsed_from = parsed_to = None
    date_from = (date_from or '').strip()
    date_to = (date_to or '').strip()

    if date_from:
        try:
            parsed_from = datetime.strptime(date_from, '%Y-%m-%d').date()
            if parsed_from > today:
                error = "From Date cannot be a future date."
                parsed_from, date_from = None, ''
        except ValueError:
            date_from = ''
    if date_to:
        try:
            parsed_to = datetime.strptime(date_to, '%Y-%m-%d').date()
            if parsed_to > today:
                error = "To Date cannot be a future date."
                parsed_to, date_to = None, ''
        except ValueError:
            date_to = ''
    if parsed_from and parsed_to and parsed_from > parsed_to:
        error = "From Date cannot be later than To Date."
        parsed_from = parsed_to = None
        date_from = date_to = ''

    return parsed_from, parsed_to, date_from, date_to, error
