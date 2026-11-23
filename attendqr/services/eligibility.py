"""Exam eligibility: who may sit the semester's exams on attendance.

Each current student of the class is placed by their overall percentage for the
semester, computed exactly as in the reports (approved leave, void sessions and
special classes are left out):

- eligible: at least the eligibility percentage
- condoned: between the condonation limit and the eligibility percentage, and the HOD
  or the Principal has condoned it, with a reason
- needs condonation: in that band, not condoned yet
- detained: below the condonation limit (cannot be condoned)

Both percentages are settings (services/settings.py): the Principal sets them in the
app, starting from ELIGIBILITY_PERCENT and CONDONATION_PERCENT. A locked semester uses
the percentages saved when it was locked.
"""
from collections import Counter
from dataclasses import dataclass, field

from ..extensions import db
from ..models import Condonation, Semester
from .enrollment import judged_elsewhere
from .reports import ReportFilters, build_attendance_matrix
from .settings import get_setting

ELIGIBLE = 'eligible'
CONDONED = 'condoned'
NEEDS_CONDONATION = 'needs_condonation'
DETAINED = 'detained'
NO_CLASSES = 'no_classes'
LABELS = {
    ELIGIBLE: 'Eligible', CONDONED: 'Eligible (condoned)', NEEDS_CONDONATION: 'Needs condonation',
    DETAINED: 'Detained', NO_CLASSES: 'No classes yet',
}


@dataclass
class EligibilityReport:
    rows: list = field(default_factory=list)  # dicts: student, attended, held, percentage, status, condonation
    counts: Counter = field(default_factory=Counter)
    eligible_at: int = 75
    condonable_at: int = 65
    limits_from_lock: bool = False  # the percentages saved when the semester was locked


def status_for(percentage, held, condoned, eligible_at, condonable_at):
    if not held:
        return NO_CLASSES
    if percentage >= eligible_at:
        return ELIGIBLE
    if percentage >= condonable_at:
        return CONDONED if condoned else NEEDS_CONDONATION
    return DETAINED


def eligibility_limits(semester):
    """(eligible_at, condonable_at, from_lock): the percentages saved when the semester was
    locked, else the current settings."""
    if semester is not None and semester.attendance_locked and semester.locked_eligibility_percent is not None:
        return semester.locked_eligibility_percent, semester.locked_condonation_percent, True
    return get_setting('eligibility_percent'), get_setting('condonation_percent'), False


def build_eligibility(department_id, semester_id):
    semester = db.session.get(Semester, semester_id)
    eligible_at, condonable_at, from_lock = eligibility_limits(semester)
    report = EligibilityReport(eligible_at=eligible_at, condonable_at=condonable_at, limits_from_lock=from_lock)
    filters = ReportFilters(department_id=department_id, semester_id=semester_id, subject_id=None, parsed_from=None,
                            parsed_to=None, date_from='', date_to='', error=None)
    _, rows = build_attendance_matrix(filters)
    condonations = {c.student_id: c for c in Condonation.query.filter_by(semester_id=semester_id)}
    for row in rows:
        if judged_elsewhere(row['left_at'], semester):
            continue  # moved to another class while it was open; judged there
        student = row['student']
        held, attended = row['counted'], row['total']
        percentage = round(attended / held * 100, 2) if held else 0
        condonation = condonations.get(student.id)
        status = status_for(percentage, held, condonation is not None, report.eligible_at, report.condonable_at)
        report.rows.append({'student': student, 'attended': attended, 'held': held, 'percentage': percentage,
                            'status': status, 'condonation': condonation})
        report.counts[status] += 1
    order = {DETAINED: 0, NEEDS_CONDONATION: 1, CONDONED: 2, ELIGIBLE: 3, NO_CLASSES: 4}
    report.rows.sort(key=lambda r: (order[r['status']], r['percentage'], r['student'].roll_no))
    return report


def export_rows(report):
    headers = ['Roll No', 'Name', 'Email', 'Attended', 'Classes held', '%', 'Status', 'Condoned by', 'Reason']
    data = []
    for row in report.rows:
        condonation = row['condonation'] if row['status'] == CONDONED else None
        data.append([
            row['student'].roll_no, row['student'].name, row['student'].email, row['attended'], row['held'],
            row['percentage'], LABELS[row['status']],
            (condonation.granted_by.username if condonation.granted_by else 'Deleted account') if condonation else '',
            condonation.reason if condonation else '',
        ])
    return headers, data
