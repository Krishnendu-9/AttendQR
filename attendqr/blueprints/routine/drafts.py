"""Each class's routine: viewing it, the draft editor, publishing, printing and importing a spreadsheet."""
from datetime import timedelta

from flask import flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required

from ...access import (
    can_access_department,
    log_activity,
    role_required,
    subject_display_name,
    taught_classes,
    user_role,
    visible_semesters_query,
)
from ...extensions import db
from ...models import (
    Subject,
    Teacher,
    Timetable,
    TimetableEntry,
)
from ...services.groups import class_groups
from ...services.locks import semester_lock_error
from ...services.reports import render_excel
from ...services.routine import (
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
    holidays_between,
    parse_day,
    parse_entry_form,
    print_table,
    publish,
    published_versions,
    start_draft,
    version_on,
    week_grid,
    week_periods,
)
from ...services.routine_import import import_rows, read_rows, template_rows
from ...services.settings import WEEKDAYS, get_setting, institution_name
from ...utils import local_now, natural_key, utc_now
from . import bp
from .common import (
    MANAGERS,
    STAFF,
    _class,
    _draft,
    _locked,
    _to_editor,
)

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
