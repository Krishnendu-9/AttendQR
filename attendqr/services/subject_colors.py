"""Subject colours: how each subject shows in the routine, like the coloured cells of a printed routine.

A subject has a colour the HOD chose on the Subjects page, or none, and then it gets one
automatically: the class's subjects take the palette in turn (by when they were added), so
they differ without anyone setting them. A class that is not a subject ("Special Tutorial
Class") is grey. The colours are CSS classes (assets/tailwind.css, .subject-<key>), so pages
need no inline styles under the CSP.
"""
from flask import g

from ..access import SPECIAL_CLASS_SUBJECT_NAME
from ..extensions import db
from ..models import Subject

# key: name shown on the Subjects page. Automatic colours are handed out in this order.
SUBJECT_COLORS = {
    'blue': 'Blue', 'violet': 'Violet', 'magenta': 'Magenta', 'rose': 'Rose', 'orange': 'Orange',
    'amber': 'Amber', 'lime': 'Lime', 'green': 'Green', 'teal': 'Teal', 'grey': 'Grey',
}
NO_SUBJECT = 'grey'
_AUTOMATIC = [key for key in SUBJECT_COLORS if key != NO_SUBJECT]


def parse_color(value):
    """The colour from a form: a palette key, or None for automatic. Raises ValueError otherwise."""
    value = (value or '').strip().lower()
    if not value:
        return None
    if value not in SUBJECT_COLORS:
        raise ValueError("Choose a colour from the list.")
    return value


def _class_order(semester_id):
    """The ids of a class's subjects in the order they were added (special classes left out), once per request."""
    cache = g.setdefault('subject_color_order', {})
    if semester_id not in cache:
        cache[semester_id] = [subject_id for (subject_id,) in db.session.query(Subject.id).filter(
            Subject.semester_id == semester_id, Subject.name != SPECIAL_CLASS_SUBJECT_NAME).order_by(Subject.id)]
    return cache[semester_id]


def subject_color(subject):
    """The colour key a subject shows in: its own, or the automatic one. Grey without a subject."""
    if subject is None or subject.name == SPECIAL_CLASS_SUBJECT_NAME:
        return NO_SUBJECT
    if subject.color in SUBJECT_COLORS:
        return subject.color
    order = _class_order(subject.semester_id)
    index = order.index(subject.id) if subject.id in order else subject.id
    return _AUTOMATIC[index % len(_AUTOMATIC)]
