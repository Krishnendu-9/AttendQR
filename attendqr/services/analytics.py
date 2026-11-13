"""Attendance trends for one class: weekly rate, per-subject rate, and students at risk.

Everything is aggregated from build_attendance_matrix(), the same matrix the
reports and exports use. A student counts towards a class only when it counts for
them there: moved-out students, approved leave and void sessions are handled the
same way, and special classes are left out, as in every percentage.
"""
from dataclasses import dataclass, field
from datetime import timedelta

from flask import current_app

from ..utils import local_now, to_local
from .enrollment import class_roster
from .leave import ON_LEAVE
from .reports import NOT_IN_CLASS, ReportFilters, build_attendance_matrix, is_special_session

PERIODS = (4, 8, 12, 16)  # weeks
DEFAULT_WEEKS = 8
RECENT_DAYS = 14

# Chart geometry (SVG user units; the SVG scales to its container).
CHART_WIDTH, CHART_HEIGHT = 640, 240
PLOT_LEFT, PLOT_RIGHT, PLOT_TOP, PLOT_BOTTOM = 44, 76, 16, 32  # the right margin holds the end labels


@dataclass
class Tally:
    present: int = 0
    expected: int = 0

    @property
    def rate(self):
        return round(self.present / self.expected * 100, 1) if self.expected else None


@dataclass
class ClassTrends:
    weeks: list = field(default_factory=list)       # dicts: start, label, present, expected, rate, sessions
    subjects: list = field(default_factory=list)    # dicts: subject, present, expected, rate
    at_risk: list = field(default_factory=list)     # dicts: student, percentage, recent, change
    overall: Tally = field(default_factory=Tally)
    sessions: int = 0
    class_size: int = 0
    below_target: int = 0
    target: int = 80
    chart: dict = field(default_factory=dict)


def parse_weeks(value):
    try:
        weeks = int(value)
    except (TypeError, ValueError):
        return DEFAULT_WEEKS
    return weeks if weeks in PERIODS else DEFAULT_WEEKS


def week_start(day):
    return day - timedelta(days=day.weekday())  # Monday


def build_class_trends(department_id, semester_id, weeks=DEFAULT_WEEKS):
    target = current_app.config['ATTENDANCE_TARGET']
    today = local_now().date()
    first_week = week_start(today) - timedelta(weeks=weeks - 1)
    filters = ReportFilters(department_id=department_id, semester_id=semester_id, subject_id=None,
                            parsed_from=first_week, parsed_to=today, date_from=first_week.isoformat(),
                            date_to=today.isoformat(), error=None)
    sessions, rows = build_attendance_matrix(filters)
    sessions = [s for s in sessions if not is_special_session(s)]

    weekly = {first_week + timedelta(weeks=i): Tally() for i in range(weeks)}
    sessions_per_week = dict.fromkeys(weekly, 0)
    subjects = {}
    overall = Tally()
    recent_from = today - timedelta(days=RECENT_DAYS - 1)
    per_student = {row['student'].id: (Tally(), Tally()) for row in rows}  # whole period, recent days

    for s in sessions:
        day = to_local(s.created_at).date()
        week = weekly.get(week_start(day))
        if week is None:
            continue
        sessions_per_week[week_start(day)] += 1
        subject = subjects.setdefault(s.subject_id, (s.subject, Tally()))[1]
        for row in rows:
            mark = row['attendance'][s.id]
            if mark in (NOT_IN_CLASS, ON_LEAVE):
                continue  # not one of this student's classes
            present = 1 if mark else 0
            for tally in (week, subject, overall, per_student[row['student'].id][0]):
                tally.present += present
                tally.expected += 1
            if day >= recent_from:
                per_student[row['student'].id][1].present += present
                per_student[row['student'].id][1].expected += 1

    trends = ClassTrends(target=target, overall=overall, sessions=len(sessions))
    trends.weeks = [{
        'start': start,
        'label': f"{start:%d %b}",
        'present': tally.present,
        'expected': tally.expected,
        'rate': tally.rate,
        'sessions': sessions_per_week[start],
    } for start, tally in weekly.items()]
    trends.subjects = sorted(({
        'subject': subject,
        'present': tally.present,
        'expected': tally.expected,
        'rate': tally.rate,
    } for subject, tally in subjects.values() if tally.expected), key=lambda item: item['rate'])

    for row in rows:
        whole, recent = per_student[row['student'].id]
        if row['left_at'] is not None or whole.rate is None:
            continue
        if whole.rate < target:
            trends.below_target += 1
        # At risk: below the target now, or dropping fast towards it.
        change = round(recent.rate - whole.rate, 1) if recent.rate is not None else None
        if whole.rate < target or (recent.rate is not None and recent.rate < target):
            trends.at_risk.append({'student': row['student'], 'percentage': whole.rate,
                                   'recent': recent.rate, 'change': change})
    trends.at_risk.sort(key=lambda item: (item['percentage'], item['student'].roll_no))
    trends.class_size = sum(1 for _, left_at in class_roster(department_id, semester_id) if left_at is None)
    trends.chart = _line_chart(trends.weeks, target)
    return trends


def y_domain(rates, target):
    """The y range: from a clean step below the lowest value (and the target) up to 100%.

    A line need not start at zero, and attendance usually sits between 70% and 100%,
    where a 0-100 scale would flatten every change into a straight line.
    """
    low = min([*rates, target])
    floor = max(0, int((low - 5) // 10) * 10)
    step = 5 if 100 - floor <= 30 else 10 if 100 - floor <= 80 else 20
    floor -= floor % step  # ticks land on multiples of the step, and on 100
    return floor, list(range(floor, 101, step))


def _line_chart(weeks, target):
    """Positions for the weekly line: one x slot per week, y fitted to the data and the target."""
    plot_w = CHART_WIDTH - PLOT_LEFT - PLOT_RIGHT
    plot_h = CHART_HEIGHT - PLOT_TOP - PLOT_BOTTOM
    step = plot_w / max(len(weeks) - 1, 1)
    floor, ticks = y_domain([w['rate'] for w in weeks if w['rate'] is not None], target)

    def y(rate):
        return round(PLOT_TOP + plot_h * (1 - (rate - floor) / (100 - floor)), 1)

    points = []
    for index, week in enumerate(weeks):
        x = round(PLOT_LEFT + step * index, 1)
        points.append(dict(week, x=x, y=y(week['rate']) if week['rate'] is not None else None))
    # The line breaks across weeks without classes (holidays) instead of inventing a value.
    segments, current = [], []
    for point in points:
        if point['y'] is None:
            if current:
                segments.append(current)
            current = []
        else:
            current.append(f"{point['x']},{point['y']}")
    if current:
        segments.append(current)
    plotted = [p for p in points if p['y'] is not None]
    last = plotted[-1] if plotted else None
    target_y = y(target)
    return {
        'width': CHART_WIDTH, 'height': CHART_HEIGHT,
        'left': PLOT_LEFT, 'right': CHART_WIDTH - PLOT_RIGHT, 'top': PLOT_TOP, 'bottom': PLOT_TOP + plot_h,
        'ticks': [{'value': value, 'y': y(value)} for value in ticks],
        'target_y': target_y,
        'points': points,
        'segments': [' '.join(segment) for segment in segments],
        'last': last,
        # End labels sit in the right margin; when the two would touch, the value goes above its point.
        'last_label_above': bool(last and abs(last['y'] - target_y) < 14),
        'hit_width': round(step, 1) if len(weeks) > 1 else plot_w,
        # Label every week when they fit, otherwise every other one.
        'label_every': 1 if len(weeks) <= 8 else 2,
    }
