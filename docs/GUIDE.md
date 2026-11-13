# How AttendQR works

The rules the app enforces, for staff and students. Installing and running it is in [DEPLOYMENT.md](DEPLOYMENT.md).

## Account Rules

- Staff account emails must end with one of `ALLOWED_STAFF_DOMAINS`, for example `@rcciit.org.in`. It has no default, so set it before creating HOD or Teacher accounts; the server warns at startup if it is empty or includes a public domain such as `@gmail.com`
- Principal can create Principal, HOD, and Teacher accounts; HOD can create Teacher accounts for their department
- Only one active HOD is allowed per department
- Passwords need at least 8 characters with letters and numbers, must not contain the user's email name or roll number, and must not be a common password or a common word plus digits (such as `Welcome@2026`)
- New and reset accounts must change their password before using the app
- Changing or resetting a password, deactivating an account, or logging out signs out all of that account's existing sessions, on every device. A copied login cookie stops working too.
- A login ends when the browser is closed, or after 60 minutes without activity (`SESSION_IDLE_MINUTES`), so a session left open on a shared lab computer does not stay usable
- After 5 failed logins from one device, that device is paused for 10 minutes for that account. Other devices are unaffected, so classmates cannot lock each other out. An account-wide limit (30 failures per hour) still bounds brute force. Resetting the password clears the lock. A wrong current password on the Account page counts the same way.
- One address that fails 100 logins in 10 minutes, across any accounts, is paused for 10 minutes (`LOGIN_IP_TOTAL_FAILURE_LIMIT`). On a LAN each phone has its own address. On a public deployment where the whole campus shares one public address, raise it or set it to `0`, or a burst of failures there pauses everyone.

### Student Accounts

Student accounts are created automatically when a student is added on the Students page or by bulk upload. The temporary password is shown once to the staff member: in a message after adding or resetting one student, and on a one-time list (with CSV download and print) after a bulk import. It is never stored, so hand it over then or reset it later. It is random rather than the roll number because roll numbers and student emails are easy to guess.

```text
username: student email
initial password: a random temporary password such as K7RM-2XQD-9HFA (must be changed at first login)
```

## Bulk Student Import

Columns: `name, roll_no, email, semester`. Principal uploads also need `department` (the label shown in the downloadable template) or `department_id`. HOD and Teacher uploads are automatically scoped to their department. A file may hold up to 2,000 students (`MAX_IMPORT_ROWS`); split larger lists.

## QR Attendance Flow

1. The teacher selects department, semester, subject, and duration, and the system creates a `ClassSession`.
2. The teacher's screen shows a QR code that changes every 10 seconds. Each code expires 30 seconds after it is shown, so screenshots and forwarded links stop working almost immediately.
3. The student scans with their own phone. A valid scan gives that phone's browser a 5-minute window to log in (and set a new password on first login) before confirming.
4. The system checks the session is still active, the student belongs to that department and semester, and the phone is the student's registered device. The scan window belongs to the phone and browser that scanned, the campus network is checked again when attendance is confirmed, and each scan can mark attendance once, so copying the browser's cookies elsewhere does not help.
5. Attendance is stored once per student per session. The IP address the scan came from is stored with it for 30 days (see [Scan IP addresses](DEPLOYMENT.md#scan-ip-addresses)).

Device rules: a student's first successful attendance registers that phone, and each phone can belong to only one student. Teachers, HODs, and the Principal can reset a student's device lock from the Students page (for example after a phone change). The device lock is a browser cookie, so a student who has not registered a phone yet could register any browser; scans that register a device are labelled **New device** in the session view, with a count, so several at once (especially from one address) stand out.

Sessions deactivated before anyone marked attendance are treated as void (created by mistake) and do not count as classes held. Deactivating after attendance was taken still counts the class.

### Manual Corrections

When a student was in class but could not scan (dead phone, device lock reset), the teacher who ran the session, the department HOD, or the Principal can open the session from Generate QR and mark them present. Entries can also be removed, for example if a student marked attendance without being in the room. Both actions require a short reason. Manual entries show as `P*` in the detailed report and exports, show the staff member and reason in the session view, and are recorded in the activity log.

Deleting a whole session erases its attendance records. A session nobody marked can be deleted by anyone who can open it. Once it has attendance, only the department HOD or the Principal can delete it, with a reason that goes into the activity log; a teacher can deactivate it or remove single entries instead.

### Special Classes

The Generate QR page also supports special classes (aptitude, workshops). Teacher selection is optional, and a custom title can be entered.

## Leave

Students ask to be excused from classes on the **Leave** page: the type (medical, personal, college duty), the first and last day, and a reason. A request covers at most 30 days, can start up to 30 days in the past (certificates often come later) and at most 90 days ahead, and cannot overlap another waiting or approved request. Until it is decided, the student can withdraw it.

The student's HOD or the Principal approves or rejects it on **Leave requests**. A rejection needs a note, which the student sees. Every request and decision is in the activity log, with who decided.

**Classes held during approved leave do not count for the student, either way.** They are left out of both classes held and classes attended, on the student dashboard, in the attendance report and exports (marked `L`), in the shortage list and in analytics. Waiting and rejected requests change nothing.

## Spot checks

After students scan, the teacher can open **Spot check** in the session view and pick 3 students at random from those marked present by scan (the system's random source, so nobody can predict who). The teacher looks for each one in the room: **In the room** keeps the attendance; **Not here** removes it, writes the reason to the activity log, and shows up in the integrity report. More can be checked any time during the session and up to 2 hours after it ends. The teacher of the session, the department's HOD and the Principal can do this.

## Integrity report

**Integrity** (HOD and Principal) lists, for one class over the last 4 to 16 weeks, the students worth a closer look, each with the reason in plain words. Nothing is decided automatically.

| Signal | Rule | Weight |
| --- | --- | --- |
| Failed spot check | Marked present but not in the room | 5 per failure |
| Buddy pair | Scanned within 20 s of the same classmate, from the same address, in at least 3 sessions and half of the sessions either scanned in | 3 |
| New phones | Registered a new phone 2 or more times; and sessions where 3 or more phones were registered at once | 2 |
| Manual entries | Marked present by hand 3 or more times (usually innocent; shown so a pattern is visible) | 1 |

Buddy pairs need scan addresses, which are kept for `SCAN_IP_RETENTION_DAYS`; the report says how many scans still have one.

## Locking a semester

When a semester's attendance is final (usually after exams), the HOD or the Principal locks it from **Semesters**. From then on nothing in it can change: no new sessions, no scans, no manual entries or removals, no spot checks, no session deletions, and no leave decisions for its students. Only the Principal can unlock it, and must give a reason. Locking and unlocking are in the activity log. Condonations (below) can still be granted.

## Exam eligibility

**Exam eligibility** (HOD and Principal) places each student of a class by their percentage over the whole semester, counted as in the reports:

- **Eligible**: at least `ELIGIBILITY_PERCENT` (75%).
- **Needs condonation**: from `CONDONATION_PERCENT` (65%) up to 75%. The HOD or Principal can **condone** it with a reason; the student is then *Eligible (condoned)*. A condonation can be revoked.
- **Detained**: below 65%; cannot be condoned.

The Excel export lists every student with their status and, for condoned students, who condoned it and why.

## Two-step sign-in

Staff can add a second step to their sign-in from **Account → Two-step sign-in**: scan the QR code with an authenticator app (Google, Microsoft, Zoho, 2FAS, Aegis, a password manager...) and type the 6-digit code it shows. AttendQR then asks for a current code after the password at every sign-in. Each code works once.

Turning it on shows 10 recovery codes, once. Each signs in once in place of a code, for a lost phone. New ones can be made from the same page.

Accounts in `TOTP_REQUIRED_ROLES` (Principal and HOD by default) must set it up before they can use anything else, and cannot turn it off. If someone loses their phone and their recovery codes, the Principal (or the HOD, for a teacher) uses **Reset two-step sign-in** on the Accounts page; they are signed out everywhere and set it up again. Students do not use two-step sign-in.

## Reporting

- Session-wise attendance reports with pagination
- Detailed student-by-session matrix (uses the same subject and date filters as the report)
- Excel and PDF export
- Student-wise attendance percentage and classes needed to reach 80%
- Shortage list: students below a chosen percentage (default 80%) overall or in any subject, worst first, with the consecutive classes each needs and Excel export
- Students moved to another semester or department stay in their old class's reports, shortage list and exports, marked "Moved out". Sessions held after the move show `–` and do not count for them, and staff can still correct the old class's sessions for them.
- Analytics for one class over the last 4 to 16 weeks: the weekly attendance rate against the target, each subject's rate, and the students below the target over the period or in the last two weeks. Every number is computed from the same data as the reports, so leave, moved students, void sessions and special classes are handled the same way.
