"""Dated classes to look at: a teacher's own routine and a class's week."""
from datetime import timedelta

from flask import abort, render_template, request
from flask_login import current_user, login_required

from ...access import (
    SPECIAL_CLASS_SUBJECT_NAME,
    role_required,
    user_role,
)
from ...models import (
    Subject,
    Teacher,
)
from ...services.changes import (
    allowed_kinds,
    can_undo,
    is_manager_of,
    may_run,
)
from ...services.groups import class_groups
from ...services.occurrences import occurrences
from ...services.routine import (
    RoutineError,
    class_name,
    parse_day,
    week_grid,
)
from ...services.routine import week_start as routine_week_start
from ...services.settings import get_setting
from ...utils import local_now
from . import bp
from .common import (
    STAFF,
    _class,
)

# ── Dated classes: my routine, a class's week, starting sessions, changes ──

def _week(value):
    """The first day of the week holding ?week=YYYY-MM-DD, or this week."""
    try:
        day = parse_day(value) or local_now().date()
    except RoutineError:
        day = local_now().date()
    return routine_week_start(day)


def _week_days(found):
    return sorted(set(get_setting('working_days')) | {o.weekday for o in found})


@bp.route('/routine/me')
@login_required
@role_required('teacher', 'hod', 'student')
def mine():
    """The current user's week: a student's classes (their groups only), or every class a teacher (or an
    HOD who also teaches) teaches."""
    start = _week(request.args.get('week'))
    end = start + timedelta(days=6)
    if user_role() == 'student':
        student = current_user.student
        if student is None:
            abort(403)
        found = occurrences(start, end, student=student)
        department_id = student.department_id
    else:
        teacher = current_user.teacher
        if teacher is None:
            abort(403)
        found = occurrences(start, end, teacher_id=teacher.id)
        department_id = teacher.department_id
    return render_template('routine_mine.html', found=found, start=start, end=end, today=local_now().date(),
                           grid=week_grid(found, department_id, _week_days(found)),
                           previous_week=start - timedelta(days=7), next_week=start + timedelta(days=7),
                           allowed_kinds=allowed_kinds, can_undo=can_undo, may_run=may_run,
                           show_class=user_role() in ('teacher', 'hod'))


@bp.route('/routine/class/<int:semester_id>/week')
@login_required
@role_required(*STAFF)
def class_week(semester_id):
    """A class's classes in one week, with what happened to each, and the changes for one date."""
    semester = _class(semester_id)
    start = _week(request.args.get('week'))
    end = start + timedelta(days=6)
    found = occurrences(start, end, semester_ids=[semester.id])
    return render_template(
        'routine_week.html', semester=semester, class_label=class_name(semester), found=found,
        start=start, end=end, today=local_now().date(),
        previous_week=start - timedelta(days=7), next_week=start + timedelta(days=7),
        allowed_kinds=allowed_kinds, can_undo=can_undo, may_run=may_run, is_manager=is_manager_of(semester),
        subjects=[s for s in Subject.query.filter_by(semester_id=semester.id) if s.name != SPECIAL_CLASS_SUBJECT_NAME],
        groups=class_groups(semester.id),
        teachers=Teacher.query.filter_by(department_id=semester.department_id).order_by(Teacher.name).all(),
    )
