"""Exam eligibility: who may sit the semester's exams on attendance.

Each current student of the class is placed by their overall percentage for the
semester, computed exactly as in the reports (approved leave, void sessions and
special classes are left out):

- eligible: at least ELIGIBILITY_PERCENT
- condoned: between CONDONATION_PERCENT and ELIGIBILITY_PERCENT, and the HOD or the
  Principal has condoned it, with a reason
- needs condonation: in that band, not condoned yet
- detained: below CONDONATION_PERCENT (cannot be condoned)
"""
from collections import Counter
from dataclasses import dataclass, field

from flask import current_app

from ..models import Condonation
from .reports import ReportFilters, build_attendance_matrix

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


def status_for(percentage, held, condoned, eligible_at, condonable_at):
    if not held:
        return NO_CLASSES
    if percentage >= eligible_at:
        return ELIGIBLE
    if percentage >= condonable_at:
        return CONDONED if condoned else NEEDS_CONDONATION
    return DETAINED


def build_eligibility(department_id, semester_id):
    cfg = current_app.config
    report = EligibilityReport(eligible_at=cfg['ELIGIBILITY_PERCENT'], condonable_at=cfg['CONDONATION_PERCENT'])
    filters = ReportFilters(department_id=department_id, semester_id=semester_id, subject_id=None, parsed_from=None,
                            parsed_to=None, date_from='', date_to='', error=None)
    _, rows = build_attendance_matrix(filters)
    condonations = {c.student_id: c for c in Condonation.query.filter_by(semester_id=semester_id)}
    for row in rows:
        if row['left_at'] is not None:
            continue  # moved to another class; judged there
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
