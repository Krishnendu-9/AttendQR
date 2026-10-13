from flask import Blueprint, abort, current_app, jsonify
from flask_login import login_required
from sqlalchemy.orm import joinedload

from ..access import (
    SPECIAL_CLASS_SUBJECT_NAME,
    can_access_department,
    can_access_session,
    current_teacher_id,
    role_required,
    session_display_name,
    subject_display_name,
    user_role,
    visible_subjects_query,
)
from ..extensions import db
from ..models import Attendance, ClassSession, Semester, Subject, Teacher
from ..services.qr import build_scan_url, make_qr_token, qr_data_uri
from ..utils import format_12h, sort_semesters

bp = Blueprint('api', __name__, url_prefix='/api')

STAFF = ('super_admin', 'hod', 'teacher')


def _subject_json(subject, include_semester=False):
    data = {
        'id': subject.id,
        'name': subject.name,
        'paper_code': subject.paper_code or '',
        'display_name': subject_display_name(subject),
        'teacher_id': subject.teacher_id,
        'teacher_name': subject.teacher.name if subject.teacher else '',
    }
    if include_semester:
        data['semester'] = subject.semester.name if subject.semester else ''
    return data


@bp.route('/semesters/<int:department_id>')
@login_required
@role_required(*STAFF)
def semesters(department_id):
    if not can_access_department(department_id):
        abort(403)
    rows = sort_semesters(Semester.query.filter_by(department_id=department_id).all())
    return jsonify([{'id': r.id, 'name': r.name} for r in rows])


@bp.route('/subjects/<int:semester_id>')
@login_required
@role_required(*STAFF)
def subjects(semester_id):
    semester = db.get_or_404(Semester, semester_id)
    if not can_access_department(semester.department_id):
        abort(403)
    rows = (
        visible_subjects_query()
        .options(joinedload(Subject.teacher))
        .filter(Subject.semester_id == semester_id)
        .order_by(Subject.paper_code, Subject.name)
        .all()
    )
    return jsonify([_subject_json(r) for r in rows])


@bp.route('/department-subjects/<int:department_id>')
@login_required
@role_required('super_admin', 'hod')
def department_subjects(department_id):
    if not can_access_department(department_id):
        abort(403)
    rows = (
        Subject.query.options(joinedload(Subject.teacher), joinedload(Subject.semester))
        .filter(Subject.department_id == department_id, Subject.name != SPECIAL_CLASS_SUBJECT_NAME)
        .order_by(Subject.paper_code, Subject.name)
        .all()
    )
    return jsonify([_subject_json(r, include_semester=True) for r in rows])


@bp.route('/teachers/<int:department_id>')
@login_required
@role_required(*STAFF)
def teachers(department_id):
    if not can_access_department(department_id):
        abort(403)
    rows = Teacher.query.filter_by(department_id=department_id)
    if user_role() == 'teacher':
        rows = rows.filter(Teacher.id == current_teacher_id())
    return jsonify([{'id': r.id, 'name': r.name} for r in rows.order_by(Teacher.name)])


@bp.route('/session/<int:id>/qr')
@login_required
@role_required(*STAFF)
def session_qr(id):
    """A freshly signed QR for a live session; the image is generated server-side from the session id."""
    class_session = db.get_or_404(ClassSession, id)
    if not can_access_session(class_session):
        abort(403)
    if not class_session.is_live():
        return jsonify({'success': False, 'ended': True, 'message': 'This session is no longer active.'}), 410

    scan_url = build_scan_url(make_qr_token(class_session.id))
    return jsonify({
        'success': True,
        'scan_url': scan_url,
        'image': qr_data_uri(scan_url),
        'refresh_seconds': current_app.config['QR_REFRESH_SECONDS'],
    })


@bp.route('/session_attendance/<int:session_id>')
@login_required
@role_required(*STAFF)
def session_attendance(session_id):
    class_session = db.get_or_404(ClassSession, session_id)
    if not can_access_session(class_session):
        abort(403)
    attendances = (
        Attendance.query.options(joinedload(Attendance.student), joinedload(Attendance.marked_by))
        .filter_by(session_id=session_id)
        .order_by(Attendance.timestamp)
        .all()
    )
    return jsonify({
        'session': {
            'id': class_session.id,
            'subject': session_display_name(class_session),
            'department': class_session.subject.department.display_name,
            'semester': class_session.subject.semester.name,
            'teacher': class_session.teacher.name if class_session.teacher else 'N/A',
            'created_at': format_12h(class_session.created_at),
            'expires_at': format_12h(class_session.expires_at),
        },
        'attendances': [{
            'id': a.id,
            'student_name': a.student.name,
            'student_roll': a.student.roll_no,
            'student_email': a.student.email,
            'timestamp': format_12h(a.timestamp),
            'source': a.source,
            'note': a.note or '',
            'marked_by': a.marked_by.username if a.marked_by else '',
        } for a in attendances],
        'total': len(attendances),
    })
