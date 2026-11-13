# Role-Based Access Control

The system supports four roles:

| Role | Access |
| --- | --- |
| Principal | Full system access |
| HOD | Own department only |
| Teacher | Own department students, assigned subjects only, own QR sessions, and attendance reports |
| Student | Own attendance dashboard |

## Principal

- Manages all academic data.
- Creates Principal, HOD, and Teacher accounts from the Accounts page, including teacher profile setup.
- Keeps one active HOD per department.
- Can activate, deactivate, reset, or delete non-student accounts from the Accounts page.
- Creates or bulk-imports student profiles from the Students page; student login accounts are auto-created.
- Views all reports, exports and analytics.
- Approves or rejects leave requests from any department.
- Sees the integrity report and exam eligibility for every class, and condones students in the condonation band.
- Locks and unlocks semesters (unlocking needs a reason).
- Resets two-step sign-in for HOD and Teacher accounts (lost phone).
- Must use two-step sign-in (by default).

## HOD

- Manages teachers, students, subjects, and semesters for the assigned department.
- Creates Teacher accounts for the assigned department from the Accounts page, including teacher profile setup.
- Can activate, deactivate, reset, or delete Teacher accounts in the assigned department.
- Creates or bulk-imports student profiles from the Students page; student login accounts are auto-created.
- Can reset student passwords and device locks in the assigned department.
- Can correct attendance on any session in the assigned department and view the department shortage list.
- Can delete a session that has attendance in the assigned department, with a reason (this erases its records).
- Approves or rejects leave requests from the department's students (a rejection needs a note).
- Views analytics, the integrity report and exam eligibility for the department's classes, and condones students in the condonation band.
- Locks the department's semesters; cannot unlock them.
- Runs spot checks in any of the department's sessions, and resets two-step sign-in for the department's teachers.
- Must use two-step sign-in (by default).
- Cannot create Principal or HOD accounts.
- Cannot access other departments.

## Teacher

- Generates QR sessions only for assigned subjects.
- Can manually mark students present in, or remove entries from, their own sessions (with a reason).
- Can delete their own sessions only while nobody has marked attendance in them; after that, deactivate instead or ask the HOD.
- Sees the shortage list and analytics for their own sessions.
- Runs spot checks in their own sessions.
- May turn on two-step sign-in (optional by default).
- Cannot decide leave requests; classes during approved leave show as `L` in their reports.
- Views attendance only for own sessions.
- Creates or bulk-imports student profiles for the assigned department.
- Can reset student passwords and device locks in the assigned department.
- Cannot manage users or academic master data.

## Student

- Views only personal attendance.
- Sees subject-wise percentage, overall percentage, and classes needed for the 80% target.
- Logs in with the student email and the temporary password given by staff, and must set a private password at first login.
- Marks attendance only from their own registered phone; each phone can be registered to one student.
- Applies for leave and can withdraw a request until it is decided; sees the decision and any note.

## Account Verification

Sensitive staff accounts are created by Principal or scoped HOD users from the Accounts page. Student accounts are created automatically from student profiles or bulk student imports.

HOD and Teacher usernames must use an email domain listed in `ALLOWED_STAFF_DOMAINS`, for example `@rcciit.org.in`. There is no default: until it is set, no staff accounts can be created. Public domains such as `@gmail.com` belong there only for testing.

Teacher login usernames must match the linked teacher profile email.

Student login usernames are the student email. The initial password is a random temporary password, shown once to the staff member who added or imported the student (bulk imports show a one-time list to download or print).

Newly created or reset accounts must change their password before they can use the app. Passwords need at least 8 characters with letters and numbers, must not contain the user's email name or roll number, and must not be a common password.

## Additional Governance

- Subject access for teachers is now based on subject assignment, not department-wide access.
- The Accounts page keeps an activity log for sensitive account actions.
- Staff sign-ins, failed sign-in attempts on real accounts, and the pauses they trigger are recorded in the activity log. Each user sees failed attempts and pauses on their own account under Recent Security Activity.
- Student password reset sets a new random temporary password, shown once to the staff member.
- Repeated failed logins pause that account on that device for 10 minutes; other devices are unaffected. Resetting the password clears it.
- Changing or resetting a password, or deactivating an account, signs out all of its existing sessions.
- Accounts with an unknown role get no access. A teacher account without a teacher profile, or an HOD without a department, gets no department or session access.
- Logging out signs out every session of that account.
- Department, semester, subject, and teacher deletes are blocked when linked records would make the action unsafe.

Bulk student import is available from the Students page. Principal uploads include department information; HOD and Teacher imports are automatically limited to their assigned department.
