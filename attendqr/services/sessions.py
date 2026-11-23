"""QR sessions: starting one, from the Generate form or from the routine, and who is absent from one.

The routes (blueprints/attendance and blueprints/routine) read the request and show the result;
the rules for what a session may be are here, so both ways of starting one follow the same ones.
Starting a session commits it: telling a double click from a class started at the same moment
by someone else needs the database's unique indexes, which only a commit consults.
"""
import re
from dataclasses import dataclass
from datetime import timedelta

from flask import current_app
from sqlalchemy.exc import IntegrityError

from ..access import (
    SPECIAL_CLASS_SUBJECT_NAME,
    can_access_department,
    can_access_session,
    can_access_subject,
    can_access_teacher,
    current_teacher_id,
    log_activity,
    own_teacher_profile,
    session_display_name,
    subject_display_name,
    user_role,
)
from ..extensions import db
from ..models import Attendance, ClassGroup, ClassSession, RoutineChange, Semester, Subject, Teacher
from ..utils import local_now, normalize_text, parse_int_value, utc_now
from .enrollment import class_roster
from .groups import GroupRosters, current_members
from .locks import semester_lock_error
from .occurrences import link_session, matching_occurrence


class SessionError(ValueError):
    """A session that cannot be started as asked; the message is for the person who asked."""


class SessionForbidden(Exception):
    """The person may not start a session for that department, subject or teacher."""


def get_or_create_special_subject(department_id, semester_id):
    """The hidden subject that a class's special classes (workshops, talks) belong to."""
    subject = Subject.query.filter_by(
        department_id=department_id,
        semester_id=semester_id,
        name=SPECIAL_CLASS_SUBJECT_NAME,
    ).first()
    if subject:
        return subject
    subject = Subject(name=SPECIAL_CLASS_SUBJECT_NAME, paper_code=None,
                      department_id=department_id, semester_id=semester_id)
    db.session.add(subject)
    db.session.flush()
    return subject


# ── From the Generate form ───────────────────────────────────

_CREATE_TOKEN = re.compile(r'^[0-9a-f]{32}$')


@dataclass
class SessionRequest:
    """What the Generate form asks for."""
    class_mode: str
    department_id: int | None
    semester_id: int | None
    subject_id: int | None
    teacher_id: int | None
    special_title: str
    duration: int | None
    group_id: int | None
    # The form's one-time token (None: an older form, or a request made by hand).
    token: str | None

    @classmethod
    def from_form(cls, form):
        token = form.get('create_token', '')
        return cls(
            class_mode=form.get('class_mode', 'regular'),
            department_id=parse_int_value(form.get('department_id')),
            semester_id=parse_int_value(form.get('semester_id')),
            subject_id=parse_int_value(form.get('subject_id')),
            teacher_id=parse_int_value(form.get('teacher_id')),
            special_title=normalize_text(form.get('special_title')),
            duration=parse_int_value(form.get('duration')),
            group_id=parse_int_value(form.get('group_id')),
            token=token if _CREATE_TOKEN.fullmatch(token) else None,
        )

    @property
    def special(self):
        return self.class_mode == 'special'


def session_created_with(token):
    """The session an earlier submission of the same form created, or None."""
    return ClassSession.query.filter_by(create_token=token).first() if token else None


def _validated(req):
    """(semester, subject, group, teacher id, special title) for the request, or SessionError / SessionForbidden."""
    cfg = current_app.config
    if req.duration is None or not cfg['MIN_QR_DURATION'] <= req.duration <= cfg['MAX_QR_DURATION']:
        raise SessionError(f"Duration must be between {cfg['MIN_QR_DURATION']} and {cfg['MAX_QR_DURATION']} minutes.")
    if not req.department_id or not req.semester_id:
        raise SessionError("Department and semester are required.")
    if not can_access_department(req.department_id):
        raise SessionForbidden
    semester = db.session.get(Semester, req.semester_id)
    if not semester or semester.department_id != req.department_id:
        raise SessionError("The selected semester does not belong to the selected department.")
    lock_error = semester_lock_error(semester)
    if lock_error:
        raise SessionError(lock_error)
    group = None
    if req.group_id:
        group = db.session.get(ClassGroup, req.group_id)
        if not group or group.semester_id != semester.id:
            raise SessionError("The selected group does not belong to the selected semester.")

    teacher_id = req.teacher_id
    if req.special:
        if not req.special_title:
            raise SessionError("Enter the special class details.")
        if len(req.special_title) > 200:
            raise SessionError("Special class details must be at most 200 characters.")
        subject, special_title = get_or_create_special_subject(semester.department_id, semester.id), req.special_title
    else:
        if not req.subject_id:
            raise SessionError("Select a subject.")
        subject = db.session.get(Subject, req.subject_id)
        if not subject or subject.department_id != semester.department_id or subject.semester_id != semester.id:
            raise SessionError("The selected subject does not belong to the selected semester.")
        if not can_access_subject(subject):
            raise SessionForbidden
        special_title = None
        if user_role() != 'teacher' and subject.teacher_id and not teacher_id:
            teacher_id = subject.teacher_id

    if user_role() == 'teacher':
        teacher_id = current_teacher_id()
    elif teacher_id:
        teacher = db.session.get(Teacher, teacher_id)
        if not teacher or teacher.department_id != subject.department_id:
            raise SessionError("The selected teacher does not belong to this department.")
        if not can_access_teacher(teacher):
            raise SessionForbidden
    elif not req.special:
        raise SessionError("Select a teacher for regular classes.")
    return semester, subject, group, teacher_id, special_title


def start_session(req):
    """Start (and commit) the session the Generate form asks for, and return it.

    A form sent again (a double click, or the browser resending it) returns the session it
    already started. A session that matches a class under way in the routine (same class,
    subject and group) holds that class; otherwise it is "Unscheduled" where the class has a
    routine. Raises SessionError or SessionForbidden.
    """
    token = req.token
    existing = session_created_with(token)
    if existing is not None:
        if can_access_session(existing):
            return existing
        token = None  # someone else's token, sent again by hand: it opens nothing of theirs

    semester, subject, group, teacher_id, special_title = _validated(req)
    group_id = group.id if group else None
    occurrence = None if req.special else matching_occurrence(semester.id, subject.id, group_id)
    for link in ((True, False) if occurrence else (False,)):
        now = utc_now()
        class_session = ClassSession(subject_id=subject.id, special_title=special_title, teacher_id=teacher_id,
                                     created_at=now, expires_at=now + timedelta(minutes=req.duration),
                                     group_id=group_id, create_token=token)
        db.session.add(class_session)
        db.session.flush()
        if link and not link_session(class_session, occurrence):
            db.session.rollback()  # another session holds that extra class already: keep this one unlinked
            continue
        log_activity(
            'qr_session_created',
            f"Generated QR session for '{special_title or subject_display_name(subject)}'"
            + (f" for {group.name}" if group else "")
            + (" (the routine class)." if link else "."),
            'class_session', class_session.id, semester.department_id,
        )
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            # The same form sent twice at once: return the session the other request made.
            existing = session_created_with(token)
            if existing is not None and can_access_session(existing):
                return existing
            # Someone linked a session to that routine class at the same moment: keep this one unlinked.
            continue
        return class_session
    raise RuntimeError("Could not start the session")


# ── From the routine ─────────────────────────────────────────

def routine_session_teacher_id(occurrence):
    """The session's teacher: whoever starts it, if they teach this class (a teacher, or an HOD who
    also teaches); for an HOD starting someone else's class, its first teacher."""
    own = own_teacher_profile()
    if own is not None and any(t.id == own.id for t in occurrence.teachers):
        return own.id
    if user_role() == 'teacher':
        return current_teacher_id()
    return occurrence.teachers[0].id if occurrence.teachers else None


def _session_holding(occurrence):
    """The session that holds the occurrence now, as committed (after a lost race)."""
    if occurrence.entry is not None:
        return ClassSession.query.filter_by(timetable_entry_id=occurrence.entry.id,
                                            occurrence_date=occurrence.original_day).first()
    change = db.session.get(RoutineChange, occurrence.change.id)
    return change.session if change is not None else None


def start_routine_session(occurrence):
    """Start (and commit) the session that holds a class in the routine, until the class ends, and return it.

    Two people starting it at the same moment (co-teachers, or a double click) get the one
    session. The caller checks who may start it and when (changes.may_run, Occurrence.can_start).
    """
    lock_error = semester_lock_error(occurrence.semester)
    if lock_error:
        raise SessionError(lock_error)
    cfg = current_app.config
    minutes_left = int((occurrence.ends_at - local_now()).total_seconds() // 60) + 1
    duration = min(max(minutes_left, cfg['MIN_QR_DURATION']), cfg['MAX_QR_DURATION'])
    if occurrence.subject is not None:
        subject, special_title = occurrence.subject, None
    else:
        subject = get_or_create_special_subject(occurrence.semester.department_id, occurrence.semester.id)
        special_title = occurrence.title
    started = utc_now()
    class_session = ClassSession(subject_id=subject.id, special_title=special_title,
                                 teacher_id=routine_session_teacher_id(occurrence),
                                 group_id=occurrence.group.id if occurrence.group else None,
                                 created_at=started, expires_at=started + timedelta(minutes=duration))
    db.session.add(class_session)
    db.session.flush()
    claimed = link_session(class_session, occurrence)
    if claimed:
        log_activity('qr_session_created', f"Generated QR session for '{session_display_name(class_session)}' from "
                     f"the routine ({occurrence.day:%d %b} {occurrence.start_time:%H:%M}).",
                     'class_session', class_session.id, occurrence.semester.department_id)
        try:
            db.session.commit()
            return class_session
        except IntegrityError:
            pass
    # Someone started it at the same moment: theirs is the class's session.
    db.session.rollback()
    existing = _session_holding(occurrence)
    if existing is None:
        raise SessionError("This class was started from another screen at the same moment. "
                           "Open it from Today's classes.")
    return existing


# ── Who is absent ────────────────────────────────────────────

def absent_students(class_session):
    """Students who were in the session's class (and group) when it was held and have no attendance entry for it.

    Includes students moved to another class since, so an old session can still be corrected,
    students added to the session's group since, and current students who joined the class after
    it (staff may mark them present for a class they attended before their account existed).
    """
    subject = class_session.subject
    marked = {
        student_id for (student_id,) in
        db.session.query(Attendance.student_id).filter(Attendance.session_id == class_session.id)
    }
    group_id = class_session.group_id
    rosters = GroupRosters([group_id])
    members_now = {student.id for student in current_members(group_id)} if group_id is not None else set()

    def in_group(student):
        return (group_id is None or student.id in members_now
                or rosters.is_member(student.id, group_id, class_session.created_at))

    return [
        member.student for member in class_roster(subject.department_id, subject.semester_id)
        if member.student.id not in marked and (member.is_current or member.in_class(class_session.created_at))
        and in_group(member.student)
    ]
