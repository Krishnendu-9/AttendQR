from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required
from sqlalchemy.orm import joinedload

from ..access import (
    can_access_department,
    log_activity,
    ordered_departments_query,
    role_required,
    visible_semesters_query,
    visible_subjects_query,
)
from ..extensions import db
from ..models import ClassSession, Condonation, Department, Semester, Student, Subject
from ..services.analytics import PERIODS, build_class_trends, parse_weeks
from ..services.eligibility import LABELS, NEEDS_CONDONATION, build_eligibility, export_rows
from ..services.integrity import (
    BUDDY_MIN_SESSIONS,
    BUDDY_WINDOW_SECONDS,
    NEW_DEVICE_BURST,
    build_integrity_report,
)
from ..services.leave import LEAVE_LEGEND, ON_LEAVE
from ..services.reports import (
    MANUAL_MARK_LEGEND,
    MOVED_LEGEND,
    SPECIAL_CLASS_LEGEND,
    attendance_counts,
    build_attendance_matrix,
    build_export_rows,
    build_shortage_export_rows,
    build_shortage_report,
    export_filename,
    is_special_session,
    parse_report_filters,
    parse_threshold,
    render_excel,
    render_pdf,
    report_sessions_query,
    total_attendance,
)
from ..utils import local_now, normalize_paper_code, normalize_text, sort_semesters

bp = Blueprint('reports', __name__)

STAFF = ('principal', 'hod', 'teacher')


def _load_class(department_id, semester_id):
    """Department and semester for a report, enforcing scope."""
    if not can_access_department(department_id):
        abort(403)
    department = db.get_or_404(Department, department_id)
    semester = db.get_or_404(Semester, semester_id)
    if semester.department_id != department.id:
        abort(404)
    return department, semester


def _filter_choices(filters):
    """Options for the department/semester/subject dropdowns, matching the current selection."""
    semesters = subjects = []
    if filters.department_id:
        semesters = sort_semesters(
            visible_semesters_query().filter(Semester.department_id == filters.department_id).all()
        )
    if filters.semester_id:
        subjects = (
            visible_subjects_query()
            .filter(Subject.semester_id == filters.semester_id)
            .order_by(Subject.paper_code, Subject.name)
            .all()
        )
    return {
        'departments': ordered_departments_query().all(),
        'semesters': semesters,
        'subjects': subjects,
        'today': local_now().date().isoformat(),
    }


@bp.route('/reports')
@login_required
@role_required(*STAFF)
def index():
    filters = parse_report_filters(request.args)
    page = None
    counts = {}
    stats = {}
    if filters.department_id and filters.semester_id:
        _load_class(filters.department_id, filters.semester_id)
        base_query = report_sessions_query(filters)
        page = (
            base_query.options(joinedload(ClassSession.teacher), joinedload(ClassSession.subject))
            .order_by(ClassSession.created_at.desc())
            .paginate(
                page=request.args.get('page', 1, type=int),
                per_page=current_app.config['SESSIONS_PER_PAGE'],
                error_out=False,
            )
        )
        counts = attendance_counts([s.id for s in page.items])

        countable = report_sessions_query(filters, countable_only=True)
        total_sessions = countable.count()
        total_students = Student.query.filter_by(
            department_id=filters.department_id, semester_id=filters.semester_id
        ).count()
        present = total_attendance(countable)
        stats = {
            'total_students': total_students,
            'total_sessions': total_sessions,
            'total_attendance': present,
            'avg_attendance': round(present / (total_sessions * total_students) * 100, 2)
            if total_sessions and total_students else 0,
        }

    return render_template(
        'reports.html',
        page=page,
        counts=counts,
        stats=stats,
        filters=filters,
        filter_args=filters.query_args(),
        page_args={
            'department_id': filters.department_id,
            'semester_id': filters.semester_id,
            **filters.query_args(),
        },
        **_filter_choices(filters),
    )


@bp.route('/reports/analytics')
@login_required
@role_required(*STAFF)
def analytics():
    """Weekly attendance trend, per-subject attendance and students at risk for one class."""
    filters = parse_report_filters(request.args)
    weeks = parse_weeks(request.args.get('weeks'))
    trends = department = semester = None
    if filters.department_id and filters.semester_id:
        department, semester = _load_class(filters.department_id, filters.semester_id)
        trends = build_class_trends(filters.department_id, filters.semester_id, weeks)
    return render_template(
        'analytics.html', trends=trends, filters=filters, weeks=weeks, periods=PERIODS,
        department=department, semester=semester, **_filter_choices(filters),
    )


@bp.route('/reports/integrity')
@login_required
@role_required('principal', 'hod')
def integrity():
    """Patterns that suggest proxy attendance in one class, each with its reason."""
    filters = parse_report_filters(request.args)
    weeks = parse_weeks(request.args.get('weeks'))
    report = department = semester = None
    if filters.department_id and filters.semester_id:
        department, semester = _load_class(filters.department_id, filters.semester_id)
        report = build_integrity_report(filters.department_id, filters.semester_id, weeks)
    return render_template(
        'integrity.html', report=report, filters=filters, weeks=weeks, periods=PERIODS,
        department=department, semester=semester, buddy_window=BUDDY_WINDOW_SECONDS,
        buddy_min_sessions=BUDDY_MIN_SESSIONS, burst_size=NEW_DEVICE_BURST,
        ip_retention=current_app.config['SCAN_IP_RETENTION_DAYS'], **_filter_choices(filters),
    )


@bp.route('/reports/eligibility')
@login_required
@role_required('principal', 'hod')
def eligibility():
    """Who may sit the semester's exams on attendance, with condonations."""
    filters = parse_report_filters(request.args)
    report = department = semester = None
    if filters.department_id and filters.semester_id:
        department, semester = _load_class(filters.department_id, filters.semester_id)
        report = build_eligibility(filters.department_id, filters.semester_id)
    return render_template('eligibility.html', report=report, filters=filters, department=department,
                           semester=semester, labels=LABELS, **_filter_choices(filters))


def _eligibility_page(semester):
    return redirect(url_for('reports.eligibility', department_id=semester.department_id, semester_id=semester.id))


@bp.route('/reports/eligibility/<int:semester_id>/students/<int:student_id>/condone', methods=['POST'])
@login_required
@role_required('principal', 'hod')
def condone(semester_id, student_id):
    semester = db.get_or_404(Semester, semester_id)
    _load_class(semester.department_id, semester.id)
    reason = normalize_text(request.form.get('note'))
    if not reason or len(reason) > 200:
        flash("Give a short reason for the condonation (at most 200 characters).", 'danger')
        return _eligibility_page(semester)
    row = next((r for r in build_eligibility(semester.department_id, semester.id).rows
                if r['student'].id == student_id), None)
    if row is None:
        abort(404)
    if row['status'] != NEEDS_CONDONATION:
        flash(f"{row['student'].name} is {LABELS[row['status']].lower()}; only students between the condonation "
              "and eligibility limits can be condoned.", 'warning')
        return _eligibility_page(semester)
    db.session.add(Condonation(student_id=student_id, semester_id=semester.id, reason=reason,
                               granted_by_user_id=current_user.id))
    log_activity('attendance_condoned', f"Condoned {row['student'].name} ({row['student'].roll_no}) at "
                 f"{row['percentage']}% for {semester.name}. Reason: {reason}", 'student', student_id,
                 semester.department_id)
    db.session.commit()
    flash(f"{row['student'].name} may sit the exams (condoned).", 'success')
    return _eligibility_page(semester)


@bp.route('/reports/eligibility/condonations/<int:id>/revoke', methods=['POST'])
@login_required
@role_required('principal', 'hod')
def revoke_condonation(id):
    condonation = db.get_or_404(Condonation, id)
    semester = db.get_or_404(Semester, condonation.semester_id)
    _load_class(semester.department_id, semester.id)
    student = condonation.student
    log_activity('condonation_revoked', f"Revoked the condonation of {student.name} ({student.roll_no}) for "
                 f"{semester.name}.", 'student', student.id, semester.department_id)
    db.session.delete(condonation)
    db.session.commit()
    flash(f"Condonation for {student.name} revoked.", 'success')
    return _eligibility_page(semester)


@bp.route('/reports/eligibility/export')
@login_required
@role_required('principal', 'hod')
def eligibility_export():
    filters = parse_report_filters(request.args)
    if not filters.department_id or not filters.semester_id:
        abort(400)
    department, semester = _load_class(filters.department_id, filters.semester_id)
    report = build_eligibility(department.id, semester.id)
    headers, data = export_rows(report)
    notes = [(f"Eligible at {report.eligible_at}% or more; {report.condonable_at}% to {report.eligible_at}% only if "
             f"condoned; below {report.condonable_at}% detained. Approved leave, void sessions and special classes "
             f"are not counted. Exported on {local_now().strftime('%d/%m/%Y %I:%M %p')}.")]
    name = ''.join(ch if ch.isalnum() else '_' for ch in f"{department.branch_name or department.course_name}_"
                   f"{semester.name}_eligibility")
    return send_file(render_excel(headers, data, notes, title='Exam eligibility'), download_name=f'{name}.xlsx',
                     as_attachment=True)


@bp.route('/reports/detailed')
@login_required
@role_required(*STAFF)
def detailed():
    filters = parse_report_filters(request.args)
    if not filters.department_id or not filters.semester_id:
        return redirect(url_for('reports.index'))
    department, semester = _load_class(filters.department_id, filters.semester_id)
    sessions, rows = build_attendance_matrix(filters)
    return render_template(
        'detailed_report.html',
        dept=department,
        sem=semester,
        sessions=sessions,
        attendance_data=rows,
        filters=filters,
        filter_args=filters.query_args(),
    )


@bp.route('/export/<int:department_id>/<int:semester_id>/<fmt>')
@login_required
@role_required(*STAFF)
def export(department_id, semester_id, fmt):
    if fmt not in ('excel', 'pdf'):
        abort(404)
    department, semester = _load_class(department_id, semester_id)
    filters = parse_report_filters(request.args, department_id=department_id, semester_id=semester_id)
    sessions, rows = build_attendance_matrix(filters)
    headers, data = build_export_rows(sessions, rows)
    filename = export_filename(department, semester, sessions, filters)
    notes = [MANUAL_MARK_LEGEND]
    if any(is_special_session(s) for s in sessions):
        notes.append(SPECIAL_CLASS_LEGEND)
    if any(row['left_at'] for row in rows):
        notes.append(MOVED_LEGEND)
    if any(ON_LEAVE in row['attendance'].values() for row in rows):
        notes.append(LEAVE_LEGEND)

    if fmt == 'excel':
        return send_file(render_excel(headers, data, notes), download_name=f'{filename}.xlsx', as_attachment=True)

    if filters.subject_id and sessions and sessions[0].subject:
        subject = sessions[0].subject
        title_subject = normalize_paper_code(subject.paper_code) or subject.name
    else:
        title_subject = department.display_name
    title = (
        f"Attendance Report — {title_subject} | {semester.name} | "
        f"Exported on {local_now().strftime('%d/%m/%Y')}"
    )
    return send_file(render_pdf(headers, data, title, notes), download_name=f'{filename}.pdf', as_attachment=True)


# ── Shortage list ────────────────────────────────────────────

def _shortage_request():
    filters = parse_report_filters(request.args)
    threshold = parse_threshold(request.args.get('threshold'), current_app.config['ATTENDANCE_TARGET'])
    return filters, threshold


def _shortage_args(filters, threshold):
    args = {'department_id': filters.department_id, 'semester_id': filters.semester_id, **filters.query_args()}
    if threshold != current_app.config['ATTENDANCE_TARGET']:
        args['threshold'] = threshold
    return args


@bp.route('/reports/shortage')
@login_required
@role_required(*STAFF)
def shortage():
    filters, threshold = _shortage_request()
    report = department = semester = None
    if filters.department_id and filters.semester_id:
        department, semester = _load_class(filters.department_id, filters.semester_id)
        report = build_shortage_report(filters, threshold)

    return render_template(
        'shortage.html',
        report=report,
        dept=department,
        sem=semester,
        filters=filters,
        threshold=threshold,
        shortage_args=_shortage_args(filters, threshold) if report else {},
        report_args={'department_id': filters.department_id, 'semester_id': filters.semester_id,
                     **filters.query_args()} if report else {},
        **_filter_choices(filters),
    )


@bp.route('/reports/shortage/export')
@login_required
@role_required(*STAFF)
def shortage_export():
    filters, threshold = _shortage_request()
    if not filters.department_id or not filters.semester_id:
        abort(404)
    department, semester = _load_class(filters.department_id, filters.semester_id)
    report = build_shortage_report(filters, threshold)
    headers, data = build_shortage_export_rows(report)
    notes = [
        (
            f"Students below {threshold}% overall or in any subject, out of {report['class_size']} in "
            f"{department.display_name} - {semester.name}. Classes held: {report['classes_held']}."
        ),
        f"Exported on {local_now().strftime('%d/%m/%Y %I:%M %p')}. Void sessions and special classes are not counted.",
    ]
    base = department.branch_name or department.course_name
    filename = ''.join(ch if ch.isalnum() or ch in '-_.' else '_' for ch in f'{base}_{semester.name}_shortage')
    return send_file(
        render_excel(headers, data, notes, title="Shortage List"),
        download_name=f'{filename}.xlsx',
        as_attachment=True,
    )
