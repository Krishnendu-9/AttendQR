"""The class routine: the Calendar page (bell schedules, holidays) and each class's routine and its editor."""
from datetime import timedelta

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError

from ..access import (
    SPECIAL_CLASS_SUBJECT_NAME,
    can_access_department,
    current_department_id,
    current_teacher_id,
    is_principal,
    log_activity,
    ordered_departments_query,
    own_teacher_profile,
    role_required,
    session_display_name,
    subject_display_name,
    taught_classes,
    user_role,
    visible_semesters_query,
)
from ..extensions import db
from ..models import (
    STAFF_ROLES,
    BellSchedule,
    ClassSession,
    Holiday,
    RoutineChange,
    Semester,
    Subject,
    Teacher,
    Timetable,
    TimetableEntry,
)
from ..security import is_safe_redirect_target
from ..services.changes import (
    add_extra_class,
    allowed_kinds,
    can_undo,
    change_occurrence,
    describe_change,
    is_manager_of,
    may_run,
)
from ..services.groups import class_groups
from ..services.locks import semester_lock_error
from ..services.occurrences import find, link_session, occurrences
from ..services.reports import render_excel
from ..services.routine import (
    PATTERNS,
    RoutineError,
    apply_entry,
    clashes,
    class_name,
    clock,
    copy_day,
    copy_from,
    describe_entry,
    draft_for,
    entry_label,
    holidays_between,
    parse_day,
    parse_entry_form,
    parse_periods,
    print_table,
    publish,
    published_versions,
    save_schedule,
    start_draft,
    version_on,
    visible_schedules,
    week_grid,
    week_periods,
)
from ..services.routine import week_start as routine_week_start
from ..services.routine_import import import_rows, read_rows, template_rows
from ..services.settings import WEEKDAYS, get_setting, institution_name
from ..utils import local_now, natural_key, normalize_text, utc_now
from .attendance import get_or_create_special_subject

bp = Blueprint('routine', __name__)

MANAGERS = ('principal', 'hod')
STAFF = STAFF_ROLES


# ── Access ───────────────────────────────────────────────────

def can_manage_scope(department_id):
    """Institution-wide schedules and holidays: the Principal. A department's own: its HOD or the Principal."""
    if department_id is None:
        return is_principal()
    return user_role() in MANAGERS and can_access_department(department_id)


def _class(semester_id, manage=False):
    """The semester if the current user may see its routine (or, with manage, edit it)."""
    semester = db.get_or_404(Semester, semester_id)
    role = user_role()
    if role in MANAGERS:
        if not can_access_department(semester.department_id):
            abort(403)
    elif manage or role != 'teacher' or (semester.department_id, semester.id) not in taught_classes():
        abort(403)
    return semester


def _draft(id):
    draft = db.get_or_404(Timetable, id)
    if not draft.is_draft:
        abort(404)
    _class(draft.semester_id, manage=True)
    return draft


def _to_editor(semester_id):
    return redirect(url_for('routine.editor', semester_id=semester_id))


def _locked(semester):
    error = semester_lock_error(semester)
    if error:
        flash(error, 'warning')
    return error


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


# ── Routines ─────────────────────────────────────────────────

@bp.route('/routine/classes')
@login_required
@role_required(*STAFF)
def classes():
    """Every class the user can see, with its routine's state."""
    today = local_now().date()
    semesters = visible_semesters_query().all()
    if user_role() == 'teacher':
        semesters = [s for s in semesters if (s.department_id, s.id) in taught_classes()]
    rows = [{'semester': s, 'current': version_on(s.id, today), 'draft': draft_for(s.id)}
            for s in sorted(semesters, key=lambda s: (natural_key(s.department.display_name), natural_key(s.name)))]
    return render_template('routine_classes.html', rows=rows, can_edit=user_role() in MANAGERS)


def _entry_values(entry):
    """What the entry dialog is filled with when a class is opened in the editor."""
    return {
        'weekday': entry.weekday, 'start_time': clock(entry.start_time), 'end_time': clock(entry.end_time),
        'subject_id': entry.subject_id or '', 'title': entry.title or '', 'group_id': entry.group_id or '',
        'teacher_ids': [t.id for t in entry.teachers], 'room': entry.room or '', 'week_pattern': entry.week_pattern,
        'valid_from': entry.valid_from.isoformat() if entry.valid_from else '',
        'valid_to': entry.valid_to.isoformat() if entry.valid_to else '', 'note': entry.note or '',
        'delete_url': url_for('routine.delete_entry', id=entry.id),
    }


def _grid_context(semester, timetable, editing=False):
    entries = list(timetable.entries) if timetable else []
    return {'grid': week_grid(entries, semester.department_id, keep_empty_days=editing), 'entries': entries}


@bp.route('/routine/class/<int:semester_id>')
@login_required
@role_required(*STAFF)
def view(semester_id):
    semester = _class(semester_id)
    today = local_now().date()
    versions = published_versions(semester.id)
    chosen = request.args.get('version', type=int)
    timetable = next((v for v in versions if v.id == chosen), None) if chosen else version_on(semester.id, today)
    upcoming = [v for v in versions if v.effective_from > today]
    if timetable is None and upcoming:
        timetable = upcoming[-1]
    return render_template('routine_class.html', semester=semester, timetable=timetable, versions=versions,
                           draft=draft_for(semester.id), editing=False, can_edit=user_role() in MANAGERS,
                           class_label=class_name(semester), today=today, **_grid_context(semester, timetable))


@bp.route('/routine/class/<int:semester_id>/draft', methods=['GET', 'POST'])
@login_required
@role_required(*MANAGERS)
def editor(semester_id):
    semester = _class(semester_id, manage=True)
    if request.method == 'POST':
        if _locked(semester):
            return redirect(url_for('routine.view', semester_id=semester.id))
        draft, created = start_draft(semester, current_user)
        if created:
            log_activity('routine_draft_created', f"Started a draft routine for {class_name(semester)} "
                         f"({len(draft.entries)} classes copied).", 'timetable', draft.id, semester.department_id)
            db.session.commit()
        return _to_editor(semester.id)
    draft = draft_for(semester.id)
    if draft is None:
        return redirect(url_for('routine.view', semester_id=semester.id))
    subjects = sorted((s for s in Subject.query.filter_by(semester_id=semester.id) if s.name != '__SPECIAL_CLASS__'),
                      key=lambda s: natural_key(s.paper_code or s.name))
    other_versions = (Timetable.query.filter(Timetable.status == Timetable.PUBLISHED,
                                             Timetable.department_id == semester.department_id)
                      .order_by(Timetable.semester_id, Timetable.effective_from.desc()).all())
    latest = (published_versions(semester.id) or [None])[0]
    periods = {day: [{'label': p.label, 'start': clock(p.start_time), 'end': clock(p.end_time), 'break': p.is_break}
                     for p in items] for day, items in week_periods(semester.department_id).items()}
    return render_template(
        'routine_class.html', semester=semester, timetable=draft, versions=published_versions(semester.id),
        draft=draft, editing=not semester_lock_error(semester), can_edit=True, class_label=class_name(semester),
        today=local_now().date(), subjects=subjects, groups=class_groups(semester.id),
        teachers=Teacher.query.filter_by(department_id=semester.department_id).order_by(Teacher.name).all(),
        patterns=PATTERNS, weekdays=WEEKDAYS, other_versions=other_versions, periods=periods,
        # A replacement starts after the current version and not in the past (services/routine.publish).
        earliest_start=max(latest.effective_from + timedelta(days=1), local_now().date()) if latest else None,
        unassigned=sum(1 for e in draft.entries if e.is_unassigned), lock_error=semester_lock_error(semester),
        add_entry_url=url_for('routine.add_entry', id=draft.id), entry_values=_entry_values,
        day_choices=list(enumerate(WEEKDAYS)),
        **_grid_context(semester, draft, editing=True),
    )


def _save_entry(draft, entry):
    """Validate the dialog and store it in `entry` (a new or existing one). Returns the redirect."""
    if _locked(draft.semester):
        return _to_editor(draft.semester_id)
    try:
        values = parse_entry_form(request.form, draft)
        errors, warnings = clashes(draft, values, exclude_id=entry.id if entry.id else None)
    except RoutineError as exc:
        db.session.rollback()
        flash(str(exc), 'danger')
        return _to_editor(draft.semester_id)
    if errors:
        db.session.rollback()
        flash("Not saved: " + ' '.join(errors), 'danger')
        return _to_editor(draft.semester_id)
    if warnings and not request.form.get('confirm_overlap'):
        db.session.rollback()
        flash("Not saved: " + ' '.join(warnings) + ' If they never attend both, tick "Save anyway" and save again.',
              'warning')
        return _to_editor(draft.semester_id)
    if entry.id is None:
        entry.timetable = draft
        db.session.add(entry)
    apply_entry(entry, values)
    db.session.commit()
    return _to_editor(draft.semester_id)


@bp.route('/routine/drafts/<int:id>/entries', methods=['POST'])
@login_required
@role_required(*MANAGERS)
def add_entry(id):
    return _save_entry(_draft(id), TimetableEntry())


@bp.route('/routine/entries/<int:id>', methods=['POST'])
@login_required
@role_required(*MANAGERS)
def update_entry(id):
    entry = db.get_or_404(TimetableEntry, id)
    draft = _draft(entry.timetable_id)
    return _save_entry(draft, entry)


@bp.route('/routine/entries/<int:id>/delete', methods=['POST'])
@login_required
@role_required(*MANAGERS)
def delete_entry(id):
    entry = db.get_or_404(TimetableEntry, id)
    draft = _draft(entry.timetable_id)
    if not _locked(draft.semester):
        db.session.delete(entry)
        db.session.commit()
    return _to_editor(draft.semester_id)


@bp.route('/routine/drafts/<int:id>/copy-day', methods=['POST'])
@login_required
@role_required(*MANAGERS)
def copy_day_view(id):
    draft = _draft(id)
    source, target = request.form.get('from_day', type=int), request.form.get('to_day', type=int)
    if _locked(draft.semester):
        return _to_editor(draft.semester_id)
    if source is None or target is None or not (0 <= source <= 6 and 0 <= target <= 6) or source == target:
        flash("Choose two different days.", 'danger')
        return _to_editor(draft.semester_id)
    copied, skipped = copy_day(draft, source, target)
    db.session.commit()
    flash(f"Copied {copied} class(es) from {WEEKDAYS[source]} to {WEEKDAYS[target]}"
          + (f"; {skipped} skipped because they would clash." if skipped else "."), 'success' if not skipped else 'warning')
    return _to_editor(draft.semester_id)


@bp.route('/routine/drafts/<int:id>/copy-from', methods=['POST'])
@login_required
@role_required(*MANAGERS)
def copy_from_view(id):
    draft = _draft(id)
    source = db.session.get(Timetable, request.form.get('source_id', type=int) or 0)
    if _locked(draft.semester):
        return _to_editor(draft.semester_id)
    if not source or source.status != Timetable.PUBLISHED or not can_access_department(source.department_id):
        flash("Choose a published routine to copy.", 'danger')
        return _to_editor(draft.semester_id)
    unassigned = copy_from(draft, source)
    db.session.commit()
    flash(f"Copied the routine of {class_name(source.semester)}"
          + (f". {unassigned} class(es) had no subject with the same paper code here: assign them before publishing."
             if unassigned else "."), 'warning' if unassigned else 'success')
    return _to_editor(draft.semester_id)


@bp.route('/routine/drafts/<int:id>/publish', methods=['POST'])
@login_required
@role_required(*MANAGERS)
def publish_view(id):
    draft = _draft(id)
    semester = draft.semester
    if _locked(semester):
        return _to_editor(semester.id)
    try:
        effective_from = parse_day(request.form.get('effective_from'))
        if not effective_from:
            raise RoutineError("Choose the date the routine starts.")
        previous = publish(draft, effective_from, current_user)
    except RoutineError as exc:
        db.session.rollback()
        flash(f"Not published: {exc}", 'danger')
        return _to_editor(semester.id)
    if previous:
        log_activity('routine_superseded', f"The routine of {class_name(semester)} from "
                     f"{previous.effective_from:%d %b %Y} now ends on {previous.effective_to:%d %b %Y}.",
                     'timetable', previous.id, semester.department_id)
    log_activity('routine_published', f"Published the routine of {class_name(semester)} from "
                 f"{effective_from:%d %b %Y} with {len(draft.entries)} classes.", 'timetable', draft.id,
                 semester.department_id)
    db.session.commit()
    flash(f"Routine published from {effective_from:%d %b %Y}.", 'success')
    return redirect(url_for('routine.view', semester_id=semester.id, version=draft.id))


@bp.route('/routine/drafts/<int:id>/discard', methods=['POST'])
@login_required
@role_required(*MANAGERS)
def discard(id):
    draft = _draft(id)
    semester = draft.semester
    log_activity('routine_draft_discarded', f"Discarded the draft routine of {class_name(semester)}.",
                 'timetable', draft.id, semester.department_id)
    db.session.delete(draft)
    db.session.commit()
    flash("Draft discarded.", 'success')
    return redirect(url_for('routine.view', semester_id=semester.id))


@bp.route('/routine/class/<int:semester_id>/print')
@login_required
@role_required(*STAFF)
def print_view(semester_id):
    semester = _class(semester_id)
    timetable = db.session.get(Timetable, request.args.get('version', type=int) or 0)
    if timetable is None or timetable.semester_id != semester.id:
        timetable = version_on(semester.id, local_now().date()) or draft_for(semester.id)
    entries = list(timetable.entries) if timetable else []
    days = sorted(set(get_setting('working_days')) | {e.weekday for e in entries})
    return render_template('routine_print.html', semester=semester, timetable=timetable, class_label=class_name(semester),
                           table=print_table(entries, semester.department_id, days),
                           grid=week_grid(entries, semester.department_id, days), entries=entries,
                           printed_at=utc_now(), subject_display_name=subject_display_name,
                           holidays=holidays_between(semester.department_id, local_now().date(),
                                                     local_now().date() + timedelta(days=120)),
                           describe_entry=describe_entry)


# ── Dated classes: my routine, a class's week, starting sessions, changes ──

def _week(value):
    """The first day of the week holding ?week=YYYY-MM-DD, or this week."""
    try:
        day = parse_day(value) or local_now().date()
    except RoutineError:
        day = local_now().date()
    return routine_week_start(day)


def _back(default):
    target = request.form.get('next')
    return redirect(target if is_safe_redirect_target(target) else default)


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


@bp.route('/routine/occurrences/start', methods=['POST'])
@login_required
@role_required(*STAFF)
def start_occurrence():
    """Start (or open) the QR session for a class in the routine."""
    occurrence = find(request.form.get('key', ''))
    if occurrence is None:
        abort(404)
    if not may_run(occurrence):
        abort(403)
    if occurrence.session is not None:
        return redirect(url_for('attendance.generate_qr', session_id=occurrence.session.id))
    lock_error = semester_lock_error(occurrence.semester)
    if lock_error:
        flash(lock_error, 'warning')
        return _back(url_for('main.dashboard'))
    now = local_now()
    if not occurrence.can_start(now):
        flash("A session can be started from 15 minutes before the class until it ends.", 'warning')
        return _back(url_for('main.dashboard'))
    cfg = current_app.config
    minutes_left = int((occurrence.ends_at - now).total_seconds() // 60) + 1
    duration = min(max(minutes_left, cfg['MIN_QR_DURATION']), cfg['MAX_QR_DURATION'])
    if occurrence.subject is not None:
        subject, special_title = occurrence.subject, None
    else:
        subject = get_or_create_special_subject(occurrence.semester.department_id, occurrence.semester.id)
        special_title = occurrence.title
    # The session's teacher: whoever starts it, if they teach this class (a teacher, or an HOD who
    # also teaches); for an HOD starting someone else's class, its first teacher.
    own = own_teacher_profile()
    if own is not None and any(t.id == own.id for t in occurrence.teachers):
        teacher_id = own.id
    elif user_role() == 'teacher':
        teacher_id = current_teacher_id()
    else:
        teacher_id = occurrence.teachers[0].id if occurrence.teachers else None
    started = utc_now()
    class_session = ClassSession(subject_id=subject.id, special_title=special_title, teacher_id=teacher_id,
                                 group_id=occurrence.group.id if occurrence.group else None,
                                 created_at=started, expires_at=started + timedelta(minutes=duration))
    db.session.add(class_session)
    db.session.flush()
    link_session(class_session, occurrence)
    log_activity('qr_session_created', f"Generated QR session for '{session_display_name(class_session)}' from the "
                 f"routine ({occurrence.day:%d %b} {clock(occurrence.start_time)}).",
                 'class_session', class_session.id, occurrence.semester.department_id)
    try:
        db.session.commit()
    except IntegrityError:
        # A co-teacher started it at the same moment: open theirs.
        db.session.rollback()
        occurrence = find(request.form.get('key', ''))
        if occurrence is None or occurrence.session is None:
            raise
        return redirect(url_for('attendance.generate_qr', session_id=occurrence.session.id))
    return redirect(url_for('attendance.generate_qr', session_id=class_session.id))


@bp.route('/routine/occurrences/change', methods=['POST'])
@login_required
@role_required(*STAFF)
def change_view():
    occurrence = find(request.form.get('key', ''))
    if occurrence is None:
        abort(404)
    back = url_for('routine.class_week', semester_id=occurrence.semester.id, week=occurrence.day.isoformat())
    if user_role() == 'teacher':
        back = url_for('routine.mine', week=occurrence.day.isoformat())
    try:
        change = change_occurrence(occurrence, request.form.get('kind'), request.form, current_user)
        db.session.flush()
    except RoutineError as exc:
        db.session.rollback()
        flash(f"Not changed: {exc}", 'danger')
        return _back(back)
    label = entry_label(occurrence.subject, occurrence.title, occurrence.group)
    log_activity(f'routine_{change.kind}', describe_change(change, f"{label} ({class_name(occurrence.semester)})"),
                 'routine_change', change.id, occurrence.semester.department_id)
    db.session.commit()
    flash(f"{change.kind_label}: {label} on {occurrence.original_day:%d %b}.", 'success')
    return _back(back)


@bp.route('/routine/changes/<int:id>/undo', methods=['POST'])
@login_required
@role_required(*STAFF)
def undo_change(id):
    change = db.get_or_404(RoutineChange, id)
    if not can_undo(change):
        abort(403)
    label = entry_label(change.subject, change.title, change.group)
    semester_id, day = change.semester_id, change.date
    log_activity('routine_change_undone', f"Undid the change '{change.kind_label}' to {label} "
                 f"({class_name(change.semester)}) on {change.date:%d %b %Y}.", 'routine_change', change.id,
                 change.department_id)
    db.session.delete(change)
    db.session.commit()
    flash("Change undone.", 'success')
    return _back(url_for('routine.class_week', semester_id=semester_id, week=day.isoformat()))


@bp.route('/routine/class/<int:semester_id>/extra', methods=['POST'])
@login_required
@role_required(*MANAGERS)
def add_extra(semester_id):
    semester = _class(semester_id, manage=True)
    try:
        change = add_extra_class(semester, request.form, current_user)
        db.session.flush()
    except RoutineError as exc:
        db.session.rollback()
        flash(f"Not added: {exc}", 'danger')
        return _back(url_for('routine.class_week', semester_id=semester.id))
    label = entry_label(change.subject, change.title, change.group)
    log_activity('routine_extra', describe_change(change, f"{label} ({class_name(semester)})"),
                 'routine_change', change.id, semester.department_id)
    db.session.commit()
    flash(f"Extra class added on {change.date:%d %b}.", 'success')
    return redirect(url_for('routine.class_week', semester_id=semester.id, week=change.date.isoformat()))


# ── Importing a routine from a spreadsheet (optional) ───────

@bp.route('/routine/class/<int:semester_id>/import-template')
@login_required
@role_required(*MANAGERS)
def import_template(semester_id):
    semester = _class(semester_id, manage=True)
    headers, rows = template_rows(semester)
    notes = [("One row per class. Day: Monday to Sunday (or 1 to 7). Either start and end (HH:MM), or a period label of "
             "the bell schedule and a length in periods."),
             ("paper_code of a subject of this class, or a title. group: a group's name, empty for the whole class. "
             "teachers: emails or short names, such as MG+SC (empty: the subject's teachers)."),
             ("repeats: every, odd or even. valid_from and valid_to (YYYY-MM-DD) are optional. "
             "Delete these three sample rows before importing.")]
    name = ''.join(ch if ch.isalnum() else '_' for ch in f"routine_{semester.department.display_name}_{semester.name}")
    return send_file(render_excel(headers, rows, notes, title='Routine', institution=institution_name()),
                     download_name=f'{name}.xlsx', as_attachment=True)


@bp.route('/routine/drafts/<int:id>/import', methods=['POST'])
@login_required
@role_required(*MANAGERS)
def import_draft(id):
    draft = _draft(id)
    semester = draft.semester
    if _locked(semester):
        return _to_editor(semester.id)
    upload = request.files.get('file')
    if upload is None or not upload.filename:
        flash("Choose a .xlsx or .csv file to import.", 'danger')
        return _to_editor(semester.id)
    try:
        rows = read_rows(upload)
    except ValueError as exc:
        flash(str(exc), 'danger')
        return _to_editor(semester.id)
    added, notes, errors = import_rows(draft, rows)
    if added:
        log_activity('routine_imported', f"Imported {added} class(es) into the draft routine of {class_name(semester)}"
                     + (f"; {len(errors)} row(s) refused." if errors else "."), 'timetable', draft.id,
                     semester.department_id)
    db.session.commit()
    return render_template('routine_import_result.html', semester=semester, class_label=class_name(semester),
                           added=added, notes=notes, errors=errors, total=len(rows))
