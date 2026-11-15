"""Planned against held: what happened to each class in the routine over some days.

Each class in the routine on those days is held (a session holds it), not recorded (it
passed without one), cancelled, or still to come. Totals per subject and per teacher show
the held share: held / (held + not recorded). Cancelled classes are planned but never due.

Nothing here touches attendance percentages: a class that was never recorded counts for
nobody (services/occurrences.py).
"""
from collections import defaultdict
from dataclasses import dataclass

from .occurrences import CANCELLED, HELD, NOT_RECORDED
from .routine import clock


@dataclass
class Tally:
    label: str
    planned: int = 0
    held: int = 0
    not_recorded: int = 0
    cancelled: int = 0
    to_come: int = 0

    @property
    def held_rate(self):
        due = self.held + self.not_recorded
        return round(self.held / due * 100) if due else None

    def add(self, status):
        self.planned += 1
        if status == HELD:
            self.held += 1
        elif status == NOT_RECORDED:
            self.not_recorded += 1
        elif status == CANCELLED:
            self.cancelled += 1
        else:
            self.to_come += 1


def subject_label(occurrence):
    subject = occurrence.subject
    if subject is None:
        return occurrence.title or 'Untitled'
    return f"{subject.paper_code} - {subject.name}" if subject.paper_code else subject.name


def summarise(found):
    """(overall, per subject, per teacher) Tallies, worst held share first."""
    overall = Tally('All classes')
    subjects, teachers = {}, {}
    for occurrence in found:
        overall.add(occurrence.status)
        label = subject_label(occurrence)
        subjects.setdefault(label, Tally(label)).add(occurrence.status)
        for teacher in occurrence.teachers or []:
            teachers.setdefault(teacher.id, Tally(teacher.name)).add(occurrence.status)

    def order(tally):
        return (tally.held_rate if tally.held_rate is not None else 101, tally.label.lower())
    return overall, sorted(subjects.values(), key=order), sorted(teachers.values(), key=order)


def export_rows(found):
    headers = ['Date', 'Day', 'Starts', 'Ends', 'Class', 'Subject', 'Group', 'Teachers', 'Room', 'Status', 'Note']
    data = []
    for o in found:
        note = ''
        if o.change is not None and getattr(o.change, 'reason', None):
            note = f"{o.change.kind_label}: {o.change.reason}"
        elif o.holiday is not None:
            note = f"Holiday: {o.holiday.title}"
        data.append([
            o.day.strftime('%d-%m-%Y'), o.day.strftime('%a'), clock(o.start_time), clock(o.end_time),
            f"{o.semester.department.display_name} {o.semester.name}", subject_label(o),
            o.group.name if o.group else 'Whole class', ', '.join(t.name for t in o.teachers),
            o.room or '', o.status_label, note,
        ])
    return headers, data


def by_day(found):
    days = defaultdict(list)
    for occurrence in found:
        days[occurrence.day].append(occurrence)
    return sorted(days.items())

