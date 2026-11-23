"""Shared by the routine's route modules: who may see or edit a class's routine, and going back."""

from flask import abort, flash, redirect, request, url_for

from ...access import (
    can_access_department,
    is_principal,
    taught_classes,
    user_role,
)
from ...extensions import db
from ...models import (
    STAFF_ROLES,
    Semester,
    Timetable,
)
from ...security import is_safe_redirect_target
from ...services.locks import semester_lock_error

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



def _back(default):
    target = request.form.get('next')
    return redirect(target if is_safe_redirect_target(target) else default)
