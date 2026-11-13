"""Spot checks: confirm that randomly chosen students marked present are really in the room.

A forwarded QR or a borrowed phone leaves a record that looks exactly like a real scan.
The only proof is a person. A teacher who checks three random names after scanning
closes makes every proxy a gamble, and a failed check is the strongest signal the
integrity report has.

Choices are made with the operating system's random source, so nobody can work
out in advance who will be checked.
"""
import random
from datetime import timedelta

from ..extensions import db
from ..models import Attendance, SpotCheck
from ..utils import utc_now

SPOT_CHECK_SIZE = 3
# A check means "in the room now", so it is only offered during the session and shortly after.
SPOT_CHECK_WINDOW = timedelta(hours=2)
FAILED_CHECK_NOTE = 'Not in the room at a spot check'

_random = random.SystemRandom()


def spot_check_open(class_session, now=None):
    return (now or utc_now()) <= class_session.expires_at + SPOT_CHECK_WINDOW


def checks_for(class_session):
    return (
        SpotCheck.query.filter_by(session_id=class_session.id)
        .order_by(SpotCheck.created_at, SpotCheck.id)
        .all()
    )


def candidates(class_session):
    """Students marked present by QR scan who have not been checked in this session yet."""
    checked = db.session.query(SpotCheck.student_id).filter(SpotCheck.session_id == class_session.id)
    return [
        student_id for (student_id,) in db.session.query(Attendance.student_id).filter(
            Attendance.session_id == class_session.id,
            Attendance.source == Attendance.SOURCE_SCAN,
            Attendance.student_id.notin_(checked),
        ).order_by(Attendance.student_id)
    ]


def start_spot_check(class_session, size=SPOT_CHECK_SIZE):
    """Pick up to `size` students at random and add pending checks for them. Returns the new checks."""
    pool = candidates(class_session)
    chosen = _random.sample(pool, min(size, len(pool)))
    checks = [SpotCheck(session_id=class_session.id, student_id=student_id) for student_id in chosen]
    db.session.add_all(checks)
    db.session.flush()
    return checks


def resolve_spot_check(check, present, user):
    """Record the teacher's answer. A student who is not in the room loses this attendance.

    Returns the attendance entry that was removed, if any (the caller logs it).
    """
    check.result = SpotCheck.PRESENT if present else SpotCheck.ABSENT
    check.checked_by_user_id = user.id
    check.resolved_at = utc_now()
    if present:
        return None
    entry = Attendance.query.filter_by(session_id=check.session_id, student_id=check.student_id).first()
    if entry is not None:
        db.session.delete(entry)
    return entry
