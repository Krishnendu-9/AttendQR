"""Approved leave: which classes are excused for which students, and validating requests.

Rule: a class held during a student's approved leave does not count for that student
either way. It is left out of both classes held and classes attended, so leave
neither lowers nor raises a percentage. Reports show such classes as "L".
"""
from collections import defaultdict
from datetime import timedelta

from ..extensions import db
from ..models import ActivityLog, LeaveRequest
from ..utils import local_day_start_utc, local_now, normalize_text, parse_iso_date, utc_now

# Matrix mark for a class held during the student's approved leave.
ON_LEAVE = 'on_leave'
ON_LEAVE_MARK = 'L'
LEAVE_LEGEND = f"{ON_LEAVE_MARK} = on approved leave (not counted)"

MAX_LEAVE_DAYS = 30
# Leave can be asked for afterwards (a medical certificate usually comes later), but not indefinitely.
MAX_DAYS_IN_PAST = 30
MAX_DAYS_AHEAD = 90
MAX_REASON_LENGTH = 300
MAX_NOTE_LENGTH = 200
# Requests a student may send in REQUEST_LIMIT_DAYS, withdrawn and rejected ones included, so one
# student cannot fill the HOD's list by asking again after every rejection.
MAX_REQUESTS_PER_PERIOD = 5
REQUEST_LIMIT_DAYS = 7


class LeaveWindows:
    """Approved leave of some students, as UTC [start, end) windows matching stored timestamps."""

    def __init__(self, student_ids):
        self._windows = defaultdict(list)
        ids = list(student_ids)
        if not ids:
            return
        rows = db.session.query(LeaveRequest.student_id, LeaveRequest.start_date, LeaveRequest.end_date).filter(
            LeaveRequest.student_id.in_(ids), LeaveRequest.status == LeaveRequest.APPROVED,
        )
        for student_id, start, end in rows:
            self._windows[student_id].append(
                (local_day_start_utc(start), local_day_start_utc(end + timedelta(days=1)))
            )

    def __bool__(self):
        return bool(self._windows)

    def has_leave(self, student_id):
        return student_id in self._windows

    def excused(self, student_id, when):
        """Whether a class that started at `when` (stored UTC) falls in the student's approved leave."""
        return any(start <= when < end for start, end in self._windows.get(student_id, ()))


# ── Requests ─────────────────────────────────────────────────

def parse_leave_form(form, student):
    """Validate a student's leave request. Returns (values, error)."""
    kind = (form.get('kind') or '').strip()
    reason = normalize_text(form.get('reason'))
    start = parse_iso_date(form.get('start_date'))
    end = parse_iso_date(form.get('end_date'))
    today = local_now().date()

    if kind not in LeaveRequest.KINDS:
        return None, "Choose the type of leave."
    if not start or not end:
        return None, "Enter the first and last day of the leave."
    if end < start:
        return None, "The last day cannot be before the first day."
    if (end - start).days + 1 > MAX_LEAVE_DAYS:
        return None, f"A request can cover at most {MAX_LEAVE_DAYS} days. Split longer leave into several requests."
    if start < today - timedelta(days=MAX_DAYS_IN_PAST):
        return None, f"Leave can be requested for days up to {MAX_DAYS_IN_PAST} days ago."
    if end > today + timedelta(days=MAX_DAYS_AHEAD):
        return None, f"Leave can be requested at most {MAX_DAYS_AHEAD} days ahead."
    if not reason:
        return None, "Give a short reason."
    if len(reason) > MAX_REASON_LENGTH:
        return None, f"Keep the reason under {MAX_REASON_LENGTH} characters."
    overlapping = overlapping_request(student.id, start, end)
    if overlapping:
        return None, (
            f"You already have a {overlapping.status} request covering "
            f"{overlapping.start_date:%d %b} to {overlapping.end_date:%d %b}."
        )
    return {'kind': kind, 'reason': reason, 'start_date': start, 'end_date': end}, None


def recent_requests_error(user_id):
    """Why the student may not send another request now, or None.

    Counted from the activity log rather than the requests, so a request withdrawn
    (deleted) counts too: otherwise asking, withdrawing and asking again has no end.
    """
    since = utc_now() - timedelta(days=REQUEST_LIMIT_DAYS)
    sent = ActivityLog.query.filter(
        ActivityLog.actor_user_id == user_id,
        ActivityLog.action == 'leave_requested',
        ActivityLog.created_at >= since,
    ).count()
    if sent >= MAX_REQUESTS_PER_PERIOD:
        return (f"You have sent {sent} leave requests in the last {REQUEST_LIMIT_DAYS} days, the most allowed. "
                "Wait a few days, or ask your HOD.")
    return None


def overlapping_request(student_id, start, end, exclude_id=None):
    """A pending or approved request of the student that shares a day with start..end, if any.

    A student has at most one such request for any day, so a decision never has to
    weigh two requests against each other.
    """
    query = LeaveRequest.query.filter(
        LeaveRequest.student_id == student_id,
        LeaveRequest.status.in_((LeaveRequest.PENDING, LeaveRequest.APPROVED)),
        LeaveRequest.start_date <= end,
        LeaveRequest.end_date >= start,
    )
    if exclude_id is not None:
        query = query.filter(LeaveRequest.id != exclude_id)
    return query.first()


def describe_range(leave):
    if leave.start_date == leave.end_date:
        return f"{leave.start_date:%d %b %Y}"
    return f"{leave.start_date:%d %b} to {leave.end_date:%d %b %Y}"
