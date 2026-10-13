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
- Views all reports and exports.

## HOD

- Manages teachers, students, subjects, and semesters for the assigned department.
- Creates Teacher accounts for the assigned department from the Accounts page, including teacher profile setup.
- Can activate, deactivate, reset, or delete Teacher accounts in the assigned department.
- Creates or bulk-imports student profiles from the Students page; student login accounts are auto-created.
- Can reset student passwords and device locks in the assigned department.
- Can correct attendance on any session in the assigned department and view the department shortage list.
- Cannot create Principal or HOD accounts.
- Cannot access other departments.

## Teacher

- Generates QR sessions only for assigned subjects.
- Can manually mark students present in, or remove entries from, their own sessions (with a reason).
- Sees the shortage list for their own sessions.
- Views attendance only for own sessions.
- Creates or bulk-imports student profiles for the assigned department.
- Can reset student passwords and device locks in the assigned department.
- Cannot manage users or academic master data.

## Student

- Views only personal attendance.
- Sees subject-wise percentage, overall percentage, and classes needed for the 80% target.
- Logs in with the student email and initial password as roll number, and must set a private password at first login.
- Marks attendance only from their own registered phone; each phone can be registered to one student.

## Account Verification

Sensitive staff accounts are created by Principal or scoped HOD users from the Accounts page. Student accounts are created automatically from student profiles or bulk student imports.

HOD and Teacher usernames must use an allowed email domain:

- `@rcciit.org.in`
- `@gmail.com` for testing

Teacher login usernames must match the linked teacher profile email.

Student login usernames are the student email, and the initial password is the student roll number.

Newly created or reset accounts must change their password before they can use the app. Passwords need at least 8 characters with letters and numbers.

## Additional Governance

- Subject access for teachers is now based on subject assignment, not department-wide access.
- The Accounts page keeps an activity log for sensitive account actions.
- Student password reset returns the password to the roll number.
- Repeated failed logins pause that account on that device for 10 minutes; other devices are unaffected. Resetting the password clears it.
- Changing or resetting a password, or deactivating an account, signs out all of its existing sessions.
- Accounts with an unknown role get no access.
- Department, semester, subject, and teacher deletes are blocked when linked records would make the action unsafe.

Bulk student import is available from the Students page. Principal uploads include department information; HOD and Teacher imports are automatically limited to their assigned department.
