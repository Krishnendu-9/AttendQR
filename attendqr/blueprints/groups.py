"""Groups within a class (lab batches, electives): the HOD or Principal manages them; teachers of the class can look."""
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import login_required

from ..access import can_access_department, log_activity, role_required, taught_classes, user_role
from ..extensions import db
from ..models import ClassGroup, Semester
from ..services.groups import (
    GroupError,
    class_students,
    clean_name,
    create_group,
    current_members,
    delete_block,
    equal_ranges,
    parse_ranges,
    set_members,
    split_by_roll,
    summaries,
)
from ..utils import parse_int_value

bp = Blueprint('groups', __name__)

MANAGERS = ('principal', 'hod')


def class_label(semester):
    return f"{semester.department.display_name} - {semester.name}"


def _semester(id, manage):
    """The semester, if the current user may see (or, with manage, change) its groups."""
    semester = db.get_or_404(Semester, id)
    role = user_role()
    if role in MANAGERS:
        if not can_access_department(semester.department_id):
            abort(403)
    elif manage or (semester.department_id, semester.id) not in taught_classes():
        abort(403)
    return semester


def _group(id):
    group = db.get_or_404(ClassGroup, id)
    _semester(group.semester_id, manage=True)
    return group


def _back(semester_id):
    return redirect(url_for('groups.index', semester_id=semester_id))


@bp.route('/semesters/<int:semester_id>/groups', methods=['GET', 'POST'])
@login_required
@role_required(*MANAGERS, 'teacher')
def index(semester_id):
    semester = _semester(semester_id, manage=request.method == 'POST')
    if request.method == 'POST':
        try:
            group = create_group(semester, request.form.get('name'))
        except GroupError as exc:
            flash(str(exc), 'danger')
            return _back(semester.id)
        log_activity('group_created', f"Created group {group.name} in {class_label(semester)}.",
                     'class_group', group.id, semester.department_id)
        db.session.commit()
        flash(f"Group {group.name} created. Add its members next.", 'success')
        return redirect(url_for('groups.members', id=group.id))
    return render_template('groups.html', semester=semester, groups=summaries(semester.id),
                           class_size=len(class_students(semester)), can_manage=user_role() in MANAGERS)


@bp.route('/groups/<int:id>/rename', methods=['POST'])
@login_required
@role_required(*MANAGERS)
def rename(id):
    group = _group(id)
    old = group.name
    try:
        group.name = clean_name(group.semester, request.form.get('name'), exclude_id=group.id)
    except GroupError as exc:
        flash(str(exc), 'danger')
        return _back(group.semester_id)
    if group.name != old:
        log_activity('group_renamed', f"Renamed group {old} to {group.name} in {class_label(group.semester)}.",
                     'class_group', group.id, group.department_id)
        db.session.commit()
    return _back(group.semester_id)


@bp.route('/groups/<int:id>/delete', methods=['POST'])
@login_required
@role_required(*MANAGERS)
def delete(id):
    group = _group(id)
    block = delete_block(group)
    if block:
        flash(block, 'warning')
        return _back(group.semester_id)
    semester_id = group.semester_id
    log_activity('group_deleted', f"Deleted group {group.name} in {class_label(group.semester)}.",
                 'class_group', group.id, group.department_id)
    db.session.delete(group)
    db.session.commit()
    flash("Group deleted.", 'success')
    return _back(semester_id)


@bp.route('/groups/<int:id>/members', methods=['GET', 'POST'])
@login_required
@role_required(*MANAGERS)
def members(id):
    group = _group(id)
    semester = group.semester
    if request.method == 'POST':
        wanted = {parse_int_value(value) for value in request.form.getlist('student_id')}
        added, removed = set_members(group, wanted - {None})
        if added or removed:
            log_activity('group_members_changed',
                         f"Changed {group.name} in {class_label(semester)}: added {len(added)}, removed {len(removed)}"
                         + (f" ({', '.join(s.roll_no for s in added[:10])}{'...' if len(added) > 10 else ''} in)"
                            if added else '') + '.',
                         'class_group', group.id, group.department_id)
            db.session.commit()
            flash(f"{group.name}: {len(added)} added, {len(removed)} removed.", 'success')
        else:
            flash("No change to the members.", 'info')
        return _back(semester.id)
    member_ids = {student.id for student in current_members(group.id)}
    return render_template('group_members.html', group=group, semester=semester,
                           students=class_students(semester), member_ids=member_ids)


@bp.route('/semesters/<int:semester_id>/groups/split', methods=['POST'])
@login_required
@role_required(*MANAGERS)
def split(semester_id):
    semester = _semester(semester_id, manage=True)
    try:
        if request.form.get('mode') == 'equal':
            count = parse_int_value(request.form.get('count')) or 0
            prefix = (request.form.get('prefix') or '').lstrip()[:20] or 'Gr-'  # "Batch " keeps its space
            ranges = equal_ranges(class_students(semester), count, prefix)
        else:
            ranges = parse_ranges(request.form.get('ranges'))
        result = split_by_roll(semester, ranges)
    except GroupError as exc:
        db.session.rollback()
        flash(str(exc), 'danger')
        return _back(semester.id)
    described = ', '.join(f"{group.name} +{len(added)}" for group, added in result)
    log_activity('class_split', f"Split {class_label(semester)} by roll number: {described}.",
                 'semester', semester.id, semester.department_id)
    db.session.commit()
    flash(f"Split by roll number: {described}.", 'success')
    return _back(semester.id)
