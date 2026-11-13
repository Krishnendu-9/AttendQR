"""Integrity report: patterns that suggest attendance marked for someone who was not there.

Nothing here decides anything. Each flag is a plain rule with its reason spelled out,
for the HOD to look into:

- Failed spot checks: the student was marked present but was not in the room. The only
  direct evidence, so it weighs most.
- Buddy pairs: two students who repeatedly scan within seconds of each other from the
  same address. On a campus network each phone has its own address, so one address for
  both suggests one device (or one hotspot) marking for two.
- New devices: a student who keeps registering new phones, and sessions where several
  phones were registered at once.
- Manual entries: a student often marked present by hand. Usually innocent (a dead
  phone), listed so a pattern is visible.

Scan addresses are kept for SCAN_IP_RETENTION_DAYS, so buddy pairs only cover that
period; the report says how many scans had an address.
"""
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import timedelta
from itertools import combinations

from ..extensions import db
from ..models import Attendance, ClassSession, SpotCheck, Student
from ..utils import local_now
from .analytics import DEFAULT_WEEKS, week_start
from .enrollment import class_roster
from .reports import ReportFilters, report_sessions_query

BUDDY_WINDOW_SECONDS = 20
BUDDY_MIN_SESSIONS = 3
# The pair scanned together in at least this share of the sessions the less frequent one scanned in.
BUDDY_MIN_SHARE = 0.5
NEW_DEVICE_BURST = 3
FREQUENT_NEW_DEVICES = 2
FREQUENT_MANUAL = 3

WEIGHTS = {'spot_check': 5, 'buddy': 3, 'new_device': 2, 'manual': 1}


@dataclass
class Flag:
    kind: str
    text: str
    weight: int


@dataclass
class FlaggedStudent:
    student: Student
    flags: list

    @property
    def score(self):
        return sum(flag.weight for flag in self.flags)


@dataclass
class IntegrityReport:
    students: list = field(default_factory=list)   # FlaggedStudent, highest score first
    pairs: list = field(default_factory=list)      # dicts: first, second, together, share, address
    bursts: list = field(default_factory=list)     # dicts: session, count, addresses
    sessions: int = 0
    scans: int = 0
    scans_with_address: int = 0
    checks: int = 0
    failed_checks: int = 0


def build_integrity_report(department_id, semester_id, weeks=DEFAULT_WEEKS):
    today = local_now().date()
    first_day = week_start(today) - timedelta(weeks=weeks - 1)
    filters = ReportFilters(department_id=department_id, semester_id=semester_id, subject_id=None,
                            parsed_from=first_day, parsed_to=today, date_from=first_day.isoformat(),
                            date_to=today.isoformat(), error=None)
    sessions = {s.id: s for s in report_sessions_query(filters).all()}
    report = IntegrityReport(sessions=len(sessions))
    students = {student.id: student for student, _ in class_roster(department_id, semester_id)}
    flags = defaultdict(list)
    if not sessions:
        return report

    entries = db.session.query(
        Attendance.student_id, Attendance.session_id, Attendance.timestamp, Attendance.source,
        Attendance.scan_ip, Attendance.new_device,
    ).filter(Attendance.session_id.in_(list(sessions))).all()
    scans = [e for e in entries if e.source == Attendance.SOURCE_SCAN]
    report.scans = len(scans)
    report.scans_with_address = sum(1 for e in scans if e.scan_ip)

    _spot_checks(report, sessions, flags)
    _buddy_pairs(report, scans, students, flags)
    _new_devices(report, scans, sessions, flags)
    manual = Counter(e.student_id for e in entries if e.source == Attendance.SOURCE_MANUAL)
    for student_id, count in manual.items():
        if count >= FREQUENT_MANUAL:
            flags[student_id].append(Flag('manual', f"Marked present by hand in {count} sessions", WEIGHTS['manual']))

    report.students = sorted(
        (FlaggedStudent(students[student_id], sorted(student_flags, key=lambda flag: -flag.weight))
         for student_id, student_flags in flags.items() if student_id in students),
        key=lambda row: (-row.score, row.student.roll_no),
    )
    return report


def _spot_checks(report, sessions, flags):
    results = defaultdict(Counter)
    for student_id, result in db.session.query(SpotCheck.student_id, SpotCheck.result).filter(
            SpotCheck.session_id.in_(list(sessions)), SpotCheck.result != SpotCheck.PENDING):
        results[student_id][result] += 1
        report.checks += 1
        report.failed_checks += result == SpotCheck.ABSENT
    for student_id, counts in results.items():
        failed, total = counts[SpotCheck.ABSENT], counts[SpotCheck.ABSENT] + counts[SpotCheck.PRESENT]
        if failed:
            flags[student_id].append(Flag(
                'spot_check', f"Not in the room at {failed} of {total} spot check{'s' if total != 1 else ''}",
                WEIGHTS['spot_check'] * failed))


def _buddy_pairs(report, scans, students, flags):
    by_session = defaultdict(list)
    scans_per_student = Counter()
    for scan in scans:
        scans_per_student[scan.student_id] += 1
        if scan.scan_ip:
            by_session[scan.session_id].append(scan)

    together = Counter()
    addresses = defaultdict(Counter)
    window = timedelta(seconds=BUDDY_WINDOW_SECONDS)
    for session_scans in by_session.values():
        for a, b in combinations(sorted(session_scans, key=lambda s: s.student_id), 2):
            if a.scan_ip == b.scan_ip and abs(a.timestamp - b.timestamp) <= window:
                together[(a.student_id, b.student_id)] += 1
                addresses[(a.student_id, b.student_id)][a.scan_ip] += 1

    for (first, second), count in together.items():
        share = count / min(scans_per_student[first], scans_per_student[second])
        if count < BUDDY_MIN_SESSIONS or share < BUDDY_MIN_SHARE or first not in students or second not in students:
            continue
        address = addresses[(first, second)].most_common(1)[0][0]
        report.pairs.append({'first': students[first], 'second': students[second], 'together': count,
                             'share': round(share * 100), 'address': address})
        for me, other in ((first, second), (second, first)):
            flags[me].append(Flag(
                'buddy', f"Scanned within {BUDDY_WINDOW_SECONDS} s of {students[other].name} from the same address "
                         f"in {count} sessions", WEIGHTS['buddy']))
    report.pairs.sort(key=lambda pair: (-pair['together'], pair['first'].roll_no))


def _new_devices(report, scans, sessions, flags):
    per_student = Counter(scan.student_id for scan in scans if scan.new_device)
    for student_id, count in per_student.items():
        if count >= FREQUENT_NEW_DEVICES:
            flags[student_id].append(Flag('new_device', f"Registered a new phone {count} times", WEIGHTS['new_device']))

    per_session = defaultdict(list)
    for scan in scans:
        if scan.new_device:
            per_session[scan.session_id].append(scan.scan_ip)
    for session_id, ips in per_session.items():
        if len(ips) >= NEW_DEVICE_BURST:
            session: ClassSession = sessions[session_id]
            report.bursts.append({'session': session, 'count': len(ips),
                                  'addresses': len({ip for ip in ips if ip})})
    report.bursts.sort(key=lambda burst: (-burst['count'], burst['session'].created_at))
