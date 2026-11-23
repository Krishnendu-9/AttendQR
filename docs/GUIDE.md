# How AttendQR works

The rules the app enforces, for staff and students. Installing and running it is in [DEPLOYMENT.md](DEPLOYMENT.md).

## Account Rules

- Staff account emails must end with one of `ALLOWED_STAFF_DOMAINS`, for example `@college.example`. It has no default, so set it before creating HOD or Teacher accounts; the server warns at startup if it is empty or includes a public domain such as `@gmail.com`
- Principal can create Principal, HOD, and Teacher accounts; HOD can create Teacher accounts for their department
- Only one active HOD is allowed per department
- An HOD who also teaches: fill in **Teacher name** on the HOD's account (Accounts). It links a teacher profile with the account's email, creating one if there is none, so subjects can be assigned to the HOD. Emptying the field unlinks it; the profile, its subjects and its sessions stay
- Passwords need at least 8 characters with letters and numbers, must not contain the user's email name or roll number, and must not be a common password or a common word plus digits (such as `Welcome@2026`)
- New and reset accounts must change their password before using the app
- Changing or resetting a password, deactivating an account, or logging out signs out all of that account's existing sessions, on every device. A copied login cookie stops working too.
- A login ends when the browser is closed, or after 60 minutes without activity (`SESSION_IDLE_MINUTES`), so a session left open on a shared lab computer does not stay usable
- After 5 failed logins from one device, that device is paused for 10 minutes for that account. Other devices are unaffected, so classmates cannot lock each other out. An account-wide limit (30 failures per hour) still bounds brute force. Resetting the password clears the lock. A wrong current password on the Account page counts the same way.
- One address that fails 100 logins in 10 minutes, across any accounts, is paused for 10 minutes (`LOGIN_IP_TOTAL_FAILURE_LIMIT`). On a LAN each phone has its own address. On a public deployment where the whole campus shares one public address, raise it or set it to `0`, or a burst of failures there pauses everyone.

### Student Accounts

Student accounts are created automatically when a student is added on the Students page or by bulk upload. The temporary password is shown once to the staff member, on a page of its own (with CSV download and print): right after adding or resetting one student, and as a list after a bulk import. It is never stored, and never put in a message, which would carry it in the session cookie, so hand it over then or reset it later. Resetting a staff account's password works the same way. It is random rather than the roll number because roll numbers and student emails are easy to guess.

The HOD or the Principal can delete a student, which erases their login and every record: attendance, leave, spot checks and condonations. A student with attendance is deleted from their own page, with a reason that goes into the activity log with the number of records erased. A student of a locked semester, now or before a move, cannot be deleted at all. Changing a student's email changes their login to match; the page says so, and the password stays the same.

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

Device rules: a student's first successful attendance registers that phone, and each phone can belong to only one student. The HOD and the Principal can reset a student's device lock from the Students page (for example after a phone change), and so can a teacher, for students in a class where they teach a subject. The same rule applies to resetting a student's password, which shows the staff member a working one-time password. The device lock is a browser cookie, so a student who has not registered a phone yet could register any browser; scans that register a device are labelled **New device** in the session view, with a count, so several at once (especially from one address) stand out.

Sessions deactivated before anyone marked attendance are treated as void (created by mistake) and do not count as classes held. Deactivating after attendance was taken still counts the class.

### Manual Corrections

When a student was in class but could not scan (dead phone, device lock reset), the teacher who ran the session, the department HOD, or the Principal can open the session from Generate QR and mark them present. Entries can also be removed, for example if a student marked attendance without being in the room. Both actions require a short reason. Manual entries show as `P*` in the detailed report and exports, show the staff member and reason in the session view, and are recorded in the activity log.

Deleting a whole session erases its attendance records. A session nobody marked can be deleted by anyone who can open it. Once it has attendance, only the department HOD or the Principal can delete it, with a reason that goes into the activity log; a teacher can deactivate it or remove single entries instead.

### Special Classes

The Generate QR page also supports special classes (aptitude, workshops). Teacher selection is optional, and a custom title can be entered. Each Generate form starts one session: a double click, or the browser sending the form again, opens the session it already started instead of holding the class twice. The same goes for **Start** on a class in the routine, extra classes included.

## Subject options

Besides its paper code, name and main teacher, a subject (on **Subjects**) has:

- **Co-teachers**: teachers who share it, such as the two teachers of a lab. They can start its sessions, open and correct every session of it (whoever started it), and see it in their reports.
- **Only for group**: for an elective or a lab one batch takes. New sessions of the subject start for that group.
- **Counts towards attendance** (on by default): untick it for something taught but never counted, such as a non-credit course. Its sessions are still listed in the reports and exports, but count in nobody's percentage, shortage, analytics or exam eligibility, like special classes.
- **Colour in the routine** (HOD and Principal): *Automatic* by default, which gives the subjects of a class different colours in the order they were added. Pick one of ten colours to fix it. The colour is used in the routine grid, the week list on phones, the printed sheet and the Subjects page. A class in the routine that is not a subject (such as a tutorial slot) is grey.

Teachers also have a **short name** (edited with their account; empty uses their initials, "Dr. Sutapa Chatterjee" is SC), used in the class routine.

## Groups (lab batches, electives)

A class can have **groups**: lab batches, electives or tutorial sets. The HOD or Principal manages them from **Semesters → Groups**; teachers can see the groups of classes they teach.

- **Create** a group by name, then tick its members, or **split** the class by roll number: into N equal groups, or by ranges such as `Gr-1: 1-37` and `Gr-2: 38-75` (a plain number matches the number at the end of a roll number, so `37` matches `MCA037`). A student can be in several groups.
- **Start a session for a group** by choosing it under **Group** on Generate QR. Leave it on *Whole class* for a normal lecture.
- **The rule:** a group's session counts only for the students who were in the group when it was held. For everyone else in the class it counts neither way, shown as `–`, so the other lab batch is never marked absent. Only members can scan into it or be marked present; a student added to the group later can still be marked for it by staff.
- **History is kept.** Removing a student ends their membership instead of deleting it, so sessions they were in keep counting for them. Moving a student to another class ends their groups in the old class.
- A group that has sessions cannot be deleted (it would lose who they counted for); rename it instead.

## Class routine

The routine (timetable) says which classes each class has in a week. It is built in the app; nothing has to be imported.

### Bell schedules and holidays (Calendar)

- A **bell schedule** lists a day's periods, such as 10:00-10:50, 10:50-11:40 and a lunch break, and the weekdays that use it. A department can have its own schedule for a day, which it uses before the institution's (a Saturday half-day, a Sunday batch). The Principal manages the institution's schedules; an HOD manages their department's.
- **Holidays** have no classes: for everyone, or for one department.
- **Working days** and the **first day of the week** are on **Settings**. Sunday can be a working day.

### Building and publishing a routine

1. On **Class routines**, open a class and choose **Create routine** (or **Edit routine**). This opens a **draft**; nobody else sees it.
2. Click a period in the grid (a row per day, with the times across the top, like the printed sheet; two batches in the same slot share it, one above the other), or **Add class**: choose the day, a period and its length (or any start and end time), the subject (or a title such as "Special Tutorial Class"), the group, the teachers, the room, and whether it repeats every week, in odd weeks or in even weeks. A class can also be limited to some dates.
3. Two lab batches can run side by side. The draft refuses a clash, and names it: the same teacher in two places (also in another class's routine), the same room twice, a whole-class class beside anything else, or one group twice. Two groups that share students only warn; tick **Save anyway** if they never attend both.
4. **Copy a day** repeats one day on another. **Copy a routine** starts from another class: subjects are matched by paper code, and classes without a match stay to be assigned.
5. **Publish** from a date. The routine in force until then ends the day before. A published routine never changes: editing makes a new draft, so every past week keeps the routine it really had. For the same reason a new version starts today or later; only a class's first routine can start in the past. **Print** gives the sheet for the notice board: days against periods, with the teachers' short names.

A locked semester's routine can be viewed but not changed.

### Using it every day

- **Today's classes** on a teacher's dashboard lists their classes, with **Start session** from 15 minutes before a class until it ends. The session gets the subject, the group and a duration up to the class's end. Once started it shows **Open session**, also for a co-teacher.
- A session started by hand on **Generate QR** that matches a class under way (same class, subject and group) holds that class. Otherwise it is marked **Unscheduled**, but only in classes that have a routine; nothing is refused.
- **My routine** shows a teacher every class they teach in a week, and a student their classes, with their groups only. The student dashboard shows **Today**.
- An HOD with a teacher profile (see Account Rules) also gets **Today's classes** on the dashboard and **My routine**, and is recorded as the teacher of the sessions they start.

### Changes for one date

On a class's week (**Class routines → This week**) or in a teacher's routine:

- **Cancel** or **Move** a class (to another time or date), with a reason. A teacher can do this for their own classes until the class ends, if **Settings → Teachers may change their own classes** is on (it is by default). A class that passed without a session stays **Not recorded** unless the HOD or Principal records what happened. A class cannot be moved to a time that has passed or onto a holiday; if a holiday is declared later on the date a class was moved to, it shows as cancelled.
- **Substitute**: another teacher takes the class that day and can run its session. HOD and Principal only.
- **Extra class**: a class on one date that is not in the routine. HOD and Principal only.

Every change is checked for clashes on that date, is in the activity log, and can be undone until the class is held. Students see cancellations and moves in their Today card and week.

### Importing a routine (optional)

The routine is normally built in the editor. To start from a spreadsheet instead, open the draft and use **Import from a spreadsheet**. **Download the template** gives the columns: one row per class, with the day; a start and end time, or a period of the bell schedule and its length in periods; the paper code of a subject or a title; the group; the teachers (emails or short names, such as `MG+SC`); the room; and whether it repeats every week, in odd weeks or in even weeks. Each row is checked like the editor's dialog, clashes included. Rows that cannot be taken are listed with the reason, and nothing is published until the HOD reviews the draft. The import does not read the merged-cell layout of a printed routine.

### Planned and held

**Planned and held** lists every class of the routine over some days (this month by default) and whether a QR session held it: **Held**, **Not recorded** (it passed without a session), **Cancelled**, or still to come; with totals per subject and per teacher and an Excel export. Dashboards warn about classes not recorded this week. Teachers see their own classes.

The rule that never changes: a class in the routine that nobody recorded counts for no student, in any percentage, list or report. Only sessions count.

## Leave

Students ask to be excused from classes on the **Leave** page: the type (medical, personal, college duty), the first and last day, and a reason. A request covers at most 30 days, can start up to 30 days in the past (certificates often come later) and at most 90 days ahead, and cannot overlap another waiting or approved request. Until it is decided, the student can withdraw it. A student can send at most 5 requests in 7 days, withdrawn and rejected ones included.

The student's HOD or the Principal approves or rejects it on **Leave requests**. A rejection needs a note, which the student sees. A decision made by mistake can be reopened from **Recent decisions**, with a reason: the request waits for a decision again, and its days count as usual until then. A request cannot be reopened while another waiting or approved request covers the same days. Every request, decision and reopening is in the activity log, with who made it.

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

Buddy pairs need scan addresses, which are kept for `SCAN_IP_RETENTION_DAYS`; the report says how many scans still have one. They assume each phone reaches the app with its own address, as on a campus LAN. On a public deployment where every campus phone shares one outgoing address, nearly every pair would match, so read buddy pairs with care there (see the threat model).

## Locking a semester

When a semester's attendance is final (usually after exams), the HOD or the Principal locks it from **Semesters**. From then on nothing in it can change: no new sessions, no scans, no manual entries or removals, no spot checks, no session deletions, no leave decisions (or reopened decisions) for days in it, including for students who have moved to another class since, no adding students to it, no deleting its students, current or former, and no change to whether a subject counts towards attendance. A student who scanned just before the lock is told at once, before they confirm. Only the Principal can unlock it, and must give a reason. Locking and unlocking are in the activity log. The lock also keeps the exam eligibility percentages in force at that moment, so a later change in **Settings** does not change a final semester's eligibility; unlocking lets it follow the current settings again. Condonations (below) can still be granted. Moving students on is allowed: the locked semester keeps everyone who was in it when it was locked, so its eligibility report does not change. Underneath the pages, the database itself refuses any change that would alter a locked semester's records, so a page that forgot to check could not change them either.

## Exam eligibility

**Exam eligibility** (HOD and Principal) places each student of a class by their percentage over the whole semester, counted as in the reports:

- **Eligible**: at least the eligibility percentage (75% unless changed in **Settings**).
- **Needs condonation**: from the condonation limit (65% unless changed) up to the eligibility percentage. The HOD or Principal can **condone** it with a reason; the student is then *Eligible (condoned)*. A condonation can be revoked.
- **Detained**: below the condonation limit; cannot be condoned.

The Excel export lists every student with their status and, for condoned students, who condoned it and why.

## Two-step sign-in

Staff can add a second step to their sign-in from **Account → Two-step sign-in**: scan the QR code with an authenticator app (Google, Microsoft, Zoho, 2FAS, Aegis, a password manager...) and type the 6-digit code it shows. AttendQR then asks for a current code after the password at every sign-in. Each code works once.

Turning it on shows 10 recovery codes, once. Each signs in once in place of a code, for a lost phone. New ones can be made from the same page.

Accounts in `TOTP_REQUIRED_ROLES` (Principal and HOD by default) must set it up before they can use anything else, and cannot turn it off. If someone loses their phone and their recovery codes, the Principal (or the HOD, for a teacher) uses **Reset two-step sign-in** on the Accounts page; they are signed out everywhere and set it up again. Students do not use two-step sign-in.

## Settings

The Principal changes institution-wide options on **Settings**. A college that changes nothing keeps the behaviour the server's settings give.

- **Institution name**: shown in the sidebar, page titles, the sign-in page and exported reports (in Excel, on printed pages and as the first note; in PDF, above the title). Empty shows only "AttendQR".
- **Attendance target** (80%): the percentage students are warned against on their dashboard, the shortage list's starting threshold, and the line in analytics.
- **Exam eligibility** (75%) and **condonation limit** (65%): the bands of the eligibility report. The condonation limit cannot be above the eligibility percentage.
- **Time zone** (Asia/Kolkata): times are shown in it, and "today", report dates and leave days follow it. Set it before classes start: changing it later moves past leave days and report dates as well.
- **Working days** (Monday to Saturday; Sunday can be added): the days classes can be held, for the class routine.
- **First day of the week** (Monday): where weeks begin in analytics, the integrity report, and odd and even weeks in the routine. Changing it during a term can swap which weeks are odd and even in published routines, past weeks included.

- **Teachers may change their own classes** (on): whether a teacher can cancel or move a routine class they teach, for one date, until it ends.
- **Terms**: the words the app uses, for an institution that says "Course" for department, "Batch" for semester, "Paper" for subject, "Section" for group, or "Director" and "Coordinator" for the Principal and HOD. The menus, page titles, form labels, role names and messages follow them. Web addresses and earlier activity log entries keep the usual words.

Each field shows the server's default, and who changed it here and when. **Reset to default** goes back to the server's value. Every change is in the activity log. Security settings (the campus network, QR timings, two-step sign-in, idle sign-out) are not on the page: they stay with whoever runs the server.

## Reporting

- Session-wise attendance reports with pagination
- Detailed student-by-session matrix (uses the same subject and date filters as the report)
- Excel and PDF export
- Student-wise attendance percentage and classes needed to reach the attendance target (80% by default)
- Shortage list: students below a chosen percentage (default: the attendance target) overall or in any subject, worst first, with the consecutive classes each needs and Excel export
- Sessions for a group (a lab batch) count only for its members; the others see `–` and are not counted, in every report and export
- Students moved to another semester or department stay in their old class's reports, shortage list and exports, marked "Moved out". Sessions held after the move show `–` and do not count for them, and staff can still correct the old class's sessions for them.
- Students added to a class, or moved into it, after it has held sessions are marked "Joined late": the sessions before they joined show `–` and do not count for them. If staff marked them present for one (a class they attended before their account existed), it counts. Students from before this rule existed count every session, as before.
- Analytics for one class over the last 4 to 16 weeks: the weekly attendance rate against the target, each subject's rate, and the students below the target over the period or in the last two weeks. Every number is computed from the same data as the reports, so leave, late joiners, moved students, void sessions and special classes are handled the same way.
