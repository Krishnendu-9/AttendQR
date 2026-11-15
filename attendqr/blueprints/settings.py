"""The Settings page: institution-wide options the Principal changes (services/settings.py)."""
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from ..access import role_required
from ..extensions import db
from ..services.settings import (
    SECTIONS,
    SETTINGS,
    WEEKDAYS,
    SettingError,
    display,
    get_setting,
    reset_setting,
    save_settings,
    saved_rows,
    timezone_names,
)

bp = Blueprint('settings', __name__)


def _submitted(form):
    """The raw values the form sent, by key. Working days come as a list (the page always sends the field)."""
    values = {}
    for key, spec in SETTINGS.items():
        if key in form:
            values[key] = form.getlist(key) if spec.kind in ('weekdays', 'bool') else form.get(key)
    return values


def _shown_value(key, form):
    """What the field shows: what was typed after a failed save, otherwise the value in use."""
    if form is None:
        return get_setting(key)
    if SETTINGS[key].kind == 'weekdays':
        return [int(day) for day in form.getlist(key) if day.isdigit()]
    if SETTINGS[key].kind == 'bool':
        return '1' in form.getlist(key)
    return form.get(key, '')


def _render(errors=None, form=None):
    errors = errors or {}
    saved = saved_rows()
    sections = []
    for section_key, title, description in SECTIONS:
        fields = [{
            'spec': spec,
            'value': _shown_value(key, form),
            'default': display(key, spec.default()),
            'saved': saved.get(key),
            'error': errors.get(key),
        } for key, spec in SETTINGS.items() if spec.section == section_key]
        sections.append({'title': title, 'description': description, 'fields': fields})
    return render_template('settings.html', sections=sections, weekdays=WEEKDAYS,
                           day_options=list(enumerate(WEEKDAYS)), timezones=timezone_names(), saved_keys=sorted(saved))


@bp.route('/settings', methods=['GET', 'POST'])
@login_required
@role_required('principal')
def index():
    if request.method == 'GET':
        return _render()
    try:
        changed = save_settings(_submitted(request.form), current_user)
    except SettingError as exc:
        # Nothing was written; show the form again with what was typed.
        return _render({exc.key: str(exc)}, request.form), 400
    if not changed:
        flash("Nothing changed.", 'info')
        return redirect(url_for('settings.index'))
    db.session.commit()
    labels = ', '.join(SETTINGS[key].label for key in changed)
    flash(f"Saved: {labels}.", 'success')
    return redirect(url_for('settings.index'))


@bp.route('/settings/<key>/reset', methods=['POST'])
@login_required
@role_required('principal')
def reset(key):
    if key not in SETTINGS:
        abort(404)
    try:
        was_saved = reset_setting(key)
    except SettingError as exc:
        flash(str(exc), 'danger')
        return redirect(url_for('settings.index'))
    if was_saved:
        db.session.commit()
        flash(f"{SETTINGS[key].label} is back to the server default.", 'success')
    return redirect(url_for('settings.index'))
