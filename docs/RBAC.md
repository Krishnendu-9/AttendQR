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
- Can reset student passwords in the assigned department.
- Cannot create Principal or HOD accounts.
- Cannot access other departments.

## Teacher

- Generates QR sessions only for assigned subjects.
- Views attendance only for own sessions.
- Creates or bulk-imports student profiles for the assigned department.
- Can reset student passwords in the assigned department.
- Cannot manage users or academic master data.

## Student

- Views only personal attendance.
- Sees subject-wise percentage, overall percentage, and classes needed for the 80% target.
- Logs in with the student email and initial password as roll number.

## Account Verification

Sensitive staff accounts are created by Principal or scoped HOD users from the Accounts page. Student accounts are created automatically from student profiles or bulk student imports.

HOD and Teacher usernames must use an allowed email domain:

- `@rcciit.org.in`
- `@gmail.com` for testing

Teacher login usernames must match the linked teacher profile email.

Student login usernames are the student email, and the initial password is the student roll number.

Newly created or reset accounts are marked with a password-change reminder after login.

## Additional Governance

- Subject access for teachers is now based on subject assignment, not department-wide access.
- The Accounts page keeps an activity log for sensitive account actions.
- Student password reset returns the password to the roll number.
- Accounts are temporarily locked after repeated failed login attempts.
- Department, semester, subject, and teacher deletes are blocked when linked records would make the action unsafe.

Bulk student import is available from the Students page. Principal uploads include department information; HOD and Teacher imports are automatically limited to their assigned department.
