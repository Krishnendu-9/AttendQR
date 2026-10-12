"""Small, dependency-free helpers shared across the application."""
import re
from datetime import datetime

import pytz

IST = pytz.timezone('Asia/Kolkata')


def ist_now():
    """Current IST time as a naive datetime, matching how timestamps are stored."""
    return datetime.now(IST).replace(tzinfo=None)


def format_12h(dt, include_date=True):
    if not dt:
        return ''
    fmt = '%d-%m-%Y %I:%M %p' if include_date else '%I:%M %p'
    return dt.strftime(fmt)


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
