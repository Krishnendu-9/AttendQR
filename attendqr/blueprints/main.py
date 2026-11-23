from flask import Blueprint, abort, current_app, jsonify, render_template
from flask_login import current_user, login_required
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import joinedload

from ..access import (
    department_filter,
    is_principal,
    own_teacher_profile,
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
from ..models import ActivityLog, ClassSession, Student, Subject, User
from ..services.changes import allowed_kinds, can_undo, may_run
from ..services.occurrences import NOT_RECORDED, occurrences
from ..services.reports import attendance_counts, build_student_attendance_summary, total_attendance
from ..services.routine import week_start
from ..utils import local_now, utc_now

bp = Blueprint('main', __name__)


def render_student_dashboard():
    if not current_user.student:
        abort(403)
    summary = build_student_attendance_summary(current_user.student)
    today = local_now().date()
    return render_template('student_dashboard.html', student=current_user.student, summary=summary,
                           today_classes=occurrences(today, today, student=current_user.student))


def routine_overview():
    """Today's classes for whoever teaches (a teacher, or an HOD who also teaches), and the classes
    passed without a session this week (the teacher's own; the department's for the HOD and Principal)."""
    today = local_now().date()
    role = user_role()
    teacher = own_teacher_profile()
    week_from = week_start(today)
    if role in ('principal', 'hod'):
        semester_ids = [s.id for s in visible_semesters_query().all()]
        week = occurrences(week_from, today, semester_ids=semester_ids) if semester_ids else []
    elif teacher is not None:
        week = occurrences(week_from, today, teacher_id=teacher.id)
    else:
        week = []
    return {
        'show_today_classes': teacher is not None,
        'today_classes': [o for o in week if o.day == today and any(t.id == teacher.id for t in o.teachers)]
        if teacher is not None else [],
        'not_recorded_today': [o for o in week if o.day == today and o.status == NOT_RECORDED],
        'not_recorded_week': [o for o in week if o.status == NOT_RECORDED],
        'routine_week_from': week_from,
    }


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

    now = utc_now()
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

    if is_principal():
        principals = User.query.filter_by(role='principal').count()
        hods = User.query.filter_by(role='hod').count()
        teachers = User.query.filter_by(role='teacher').count()
        accounts = {'total': principals + hods + teachers, 'principals': principals, 'hods': hods, 'teachers': teachers}
    else:
        teachers = User.query.filter(User.role == 'teacher', department_filter(User.department_id)).count()
        accounts = {'total': teachers, 'principals': None, 'hods': None, 'teachers': teachers}

    return render_template(
        'dashboard.html',
        departments=visible_departments_query().count(),
        semesters=visible_semesters_query().count(),
        subjects=visible_subjects_query().count(),
        teachers=visible_teachers_query().count(),
        students=visible_students_query().filter(Student.left_at.is_(None)).count(),
        sessions=visible_sessions_query().count(),
        attendance=total_attendance(visible_sessions_query()),
        recent_sessions=recent_rows,
        recent_account_logs=visible_activity_logs_query().order_by(ActivityLog.created_at.desc()).limit(3).all(),
        accounts=accounts,
        allowed_kinds=allowed_kinds, can_undo=can_undo, may_run=may_run,
        **routine_overview(),
    )


@bp.route('/dev/components')
@login_required
def dev_components():
    """Every interface component in light and dark, for checking the design. Only in debug mode."""
    if not current_app.debug:
        abort(404)
    return render_template('dev/components.html')


@bp.route('/student/attendance')
@login_required
@role_required('student')
def student_attendance():
    return render_student_dashboard()
