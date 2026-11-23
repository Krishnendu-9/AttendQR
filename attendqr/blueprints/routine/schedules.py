"""The Calendar page: bell schedules and holidays, for the institution or one department."""
from datetime import timedelta

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import login_required

from ...access import (
    current_department_id,
    is_principal,
    log_activity,
    ordered_departments_query,
    role_required,
)
from ...extensions import db
from ...models import (
    BellSchedule,
    Holiday,
)
from ...services.routine import (
    RoutineError,
    clock,
    parse_day,
    parse_periods,
    save_schedule,
    visible_schedules,
)
from ...services.settings import WEEKDAYS
from ...utils import local_now, normalize_text
from . import bp
from .common import (
    MANAGERS,
    can_manage_scope,
)

# ── Calendar: bell schedules and holidays ────────────────────

def _scope_from_form():
    """None for the institution, else a department id the user may manage."""
    scope = request.form.get('scope') or ''
    department_id = None if scope in ('', 'institution') else int(scope) if scope.isdigit() else -1
    if department_id is None and not is_principal():
        department_id = current_department_id()
    if not can_manage_scope(department_id):
        abort(403)
    return department_id


@bp.route('/calendar')
@login_required
@role_required(*MANAGERS)
def calendar():
    department_id = None if is_principal() else current_department_id()
    schedules = (sorted(BellSchedule.query.all(), key=lambda s: (s.department_id is not None, s.name.lower()))
                 if is_principal() else visible_schedules(department_id))
    today = local_now().date()
    holidays = Holiday.query.filter(Holiday.end_date >= today - timedelta(days=60)).order_by(Holiday.start_date).all()
    if not is_principal():
        holidays = [h for h in holidays if h.department_id in (None, department_id)]
    return render_template('calendar.html', schedules=schedules, holidays=holidays, weekdays=WEEKDAYS,
                           departments=ordered_departments_query().all(), can_manage_scope=can_manage_scope,
                           today=today)


@bp.route('/calendar/schedules', methods=['POST'])
@login_required
@role_required(*MANAGERS)
def new_schedule():
    department_id = _scope_from_form()
    name = normalize_text(request.form.get('name'))
    if not name:
        flash("Give the schedule a name, such as Weekday or Saturday half-day.", 'danger')
        return redirect(url_for('routine.calendar'))
    schedule = BellSchedule(name=name[:60], department_id=department_id)
    db.session.add(schedule)
    db.session.flush()
    log_activity('bell_schedule_created', f"Created bell schedule '{schedule.name}'"
                 + (f" for {schedule.department.display_name}." if schedule.department else " for the institution."),
                 'bell_schedule', schedule.id, department_id)
    db.session.commit()
    return redirect(url_for('routine.edit_schedule', id=schedule.id))


@bp.route('/calendar/schedules/<int:id>', methods=['GET', 'POST'])
@login_required
@role_required(*MANAGERS)
def edit_schedule(id):
    schedule = db.get_or_404(BellSchedule, id)
    if not can_manage_scope(schedule.department_id):
        abort(403)
    error = None
    if request.method == 'POST':
        try:
            periods = parse_periods(request.form.getlist('label'), request.form.getlist('start'),
                                    request.form.getlist('end'), set(request.form.getlist('is_break')))
            weekdays = {day for day in request.form.getlist('weekday', type=int) if 0 <= day <= 6}
            save_schedule(schedule, request.form.get('name'), periods, weekdays)
        except RoutineError as exc:
            db.session.rollback()
            error = str(exc)
        else:
            days = ', '.join(WEEKDAYS[d][:3] for d in sorted(weekdays)) or 'no days yet'
            log_activity('bell_schedule_updated', f"Updated bell schedule '{schedule.name}': {len(periods)} periods, "
                         f"used on {days}.", 'bell_schedule', schedule.id, schedule.department_id)
            db.session.commit()
            flash(f"Saved {schedule.name}.", 'success')
            return redirect(url_for('routine.calendar'))
    rows = [{'label': p.label, 'start': clock(p.start_time), 'end': clock(p.end_time), 'is_break': p.is_break}
            for p in list(schedule.periods)]
    if request.method == 'POST':
        rows = [{'label': label, 'start': start, 'end': end, 'is_break': str(i) in request.form.getlist('is_break')}
                for i, (label, start, end) in enumerate(zip(request.form.getlist('label'), request.form.getlist('start'),
                                                            request.form.getlist('end'), strict=False))]
    rows += [{'label': '', 'start': '', 'end': '', 'is_break': False} for _ in range(4)]  # room to add periods
    used = {d.weekday for d in list(schedule.days)} if request.method == 'GET' else set(request.form.getlist('weekday', type=int))
    return render_template('bell_schedule.html', schedule=schedule, rows=rows[:16], used=used, weekdays=WEEKDAYS,
                           error=error), 400 if error else 200


@bp.route('/calendar/schedules/<int:id>/delete', methods=['POST'])
@login_required
@role_required(*MANAGERS)
def delete_schedule(id):
    schedule = db.get_or_404(BellSchedule, id)
    if not can_manage_scope(schedule.department_id):
        abort(403)
    log_activity('bell_schedule_deleted', f"Deleted bell schedule '{schedule.name}'.", 'bell_schedule', schedule.id,
                 schedule.department_id)
    db.session.delete(schedule)
    db.session.commit()
    flash("Schedule deleted. Classes in routines keep their times.", 'success')
    return redirect(url_for('routine.calendar'))


@bp.route('/calendar/holidays', methods=['POST'])
@login_required
@role_required(*MANAGERS)
def add_holiday():
    department_id = _scope_from_form()
    title = normalize_text(request.form.get('title'))
    try:
        start = parse_day(request.form.get('start_date'))
        if not start or not title:
            raise RoutineError("Enter the first day and a title.")
        end = parse_day(request.form.get('end_date')) or start
        if end < start:
            raise RoutineError("The last day cannot be before the first day.")
        if (end - start).days > 120:
            raise RoutineError("A holiday can cover at most 120 days.")
    except RoutineError as exc:
        flash(str(exc), 'danger')
        return redirect(url_for('routine.calendar'))
    holiday = Holiday(start_date=start, end_date=end, title=title[:100], department_id=department_id)
    db.session.add(holiday)
    db.session.flush()
    span = f"{start:%d %b %Y}" if start == end else f"{start:%d %b} to {end:%d %b %Y}"
    log_activity('holiday_added', f"Added holiday '{holiday.title}' ({span})"
                 + (f" for {holiday.department.display_name}." if holiday.department else "."),
                 'holiday', holiday.id, department_id)
    db.session.commit()
    flash(f"Holiday added: {holiday.title}.", 'success')
    return redirect(url_for('routine.calendar'))


@bp.route('/calendar/holidays/<int:id>/delete', methods=['POST'])
@login_required
@role_required(*MANAGERS)
def delete_holiday(id):
    holiday = db.get_or_404(Holiday, id)
    if not can_manage_scope(holiday.department_id):
        abort(403)
    log_activity('holiday_removed', f"Removed holiday '{holiday.title}' ({holiday.start_date:%d %b %Y}).",
                 'holiday', holiday.id, holiday.department_id)
    db.session.delete(holiday)
    db.session.commit()
    return redirect(url_for('routine.calendar'))
