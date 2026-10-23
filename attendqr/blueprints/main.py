from flask import Blueprint, abort, current_app, jsonify, render_template
from flask_login import current_user, login_required
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import joinedload

from ..access import (
    current_department_id,
    is_super_admin,
    role_required,
    session_display_name,
    user_role,
    visible_activity_logs_query,
    visible_departments_query,
    visible_semesters_query,
    visible_sessions_query,
    visible_students_query,
    visible_subjects_query,
    visible_teachers_query,
)
from ..extensions import db
from ..models import ActivityLog, Admin, ClassSession, Subject
from ..services.reports import attendance_counts, build_student_attendance_summary, total_attendance
from ..utils import ist_now

bp = Blueprint('main', __name__)


def render_student_dashboard():
    if not current_user.student:
        abort(403)
    summary = build_student_attendance_summary(current_user.student)
    return render_template('student_dashboard.html', student=current_user.student, summary=summary)


@bp.route('/healthz')
def healthz():
    """Liveness and database check for container health probes. Reveals nothing else."""
    try:
        db.session.execute(text('SELECT 1'))
    except SQLAlchemyError:
        current_app.logger.exception("Health check could not reach the database")
        return jsonify({'status': 'error'}), 503
    return jsonify({'status': 'ok'})


@bp.route('/')
@login_required
def dashboard():
    if user_role() == 'student':
        return render_student_dashboard()

    now = ist_now()
    recent_sessions = (
        visible_sessions_query()
        .options(
            joinedload(ClassSession.subject).joinedload(Subject.department),
            joinedload(ClassSession.subject).joinedload(Subject.semester),
            joinedload(ClassSession.teacher),
        )
        .order_by(ClassSession.created_at.desc())
        .limit(3)
        .all()
    )
    counts = attendance_counts([s.id for s in recent_sessions])
    recent_rows = [{
        'id': s.id,
        'name': session_display_name(s),
        'department': s.subject.department.display_name if s.subject and s.subject.department else '',
        'semester': s.subject.semester.name if s.subject and s.subject.semester else '',
        'teacher': s.teacher.name if s.teacher else 'Unassigned',
        'created_at': s.created_at,
        'expires_at': s.expires_at,
        'is_active': s.is_live(now),
        'attendance_count': counts.get(s.id, 0),
    } for s in recent_sessions]

    if is_super_admin():
        principals = Admin.query.filter_by(role='super_admin').count()
        hods = Admin.query.filter_by(role='hod').count()
        teachers = Admin.query.filter_by(role='teacher').count()
        accounts = {'total': principals + hods + teachers, 'principals': principals, 'hods': hods, 'teachers': teachers}
    else:
        teachers = Admin.query.filter_by(role='teacher', department_id=current_department_id()).count()
        accounts = {'total': teachers, 'principals': None, 'hods': None, 'teachers': teachers}

    return render_template(
        'dashboard.html',
        departments=visible_departments_query().count(),
        semesters=visible_semesters_query().count(),
        subjects=visible_subjects_query().count(),
        teachers=visible_teachers_query().count(),
        students=visible_students_query().count(),
        sessions=visible_sessions_query().count(),
        attendance=total_attendance(visible_sessions_query()),
        recent_sessions=recent_rows,
        recent_account_logs=visible_activity_logs_query().order_by(ActivityLog.created_at.desc()).limit(3).all(),
        accounts=accounts,
    )


@bp.route('/student/attendance')
@login_required
@role_required('student')
def student_attendance():
    return render_student_dashboard()
