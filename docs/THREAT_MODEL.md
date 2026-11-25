# Threat model

What AttendQR protects, who might attack it, what stops them, and what is left.
Each defence points to the code or setting that implements it; the README has the
operator detail.

## What is worth protecting

| Asset | Why it matters |
| --- | --- |
| Attendance records | They decide who may sit exams (the 80% rule). The main target. |
| Accounts | A Principal or HOD account can change records, accounts and leave for a whole department. |
| Personal data | Names, emails, roll numbers, scan IP addresses, leave reasons (sometimes medical). |
| Availability during class | The few minutes a QR is on screen are when everything must work. |

## Who attacks

1. **A student** who wants attendance without being in the room (the common case), or wants to mark it for a friend.
2. **An outsider** on the internet or the campus network, guessing passwords or probing the app.
3. **A staff member** editing records they should not, or more than they should.

## Trust boundaries

- The student's phone, its browser and its cookies are **untrusted**: anything in a cookie can be copied to another browser.
- The network path is untrusted unless served over HTTPS (`SESSION_COOKIE_SECURE=1`).
- Client IP addresses are trusted only as far as `TRUSTED_PROXY_COUNT` says.
- Staff are trusted within their role and department, and every sensitive change they make is logged.

## Threats and defences

### Proxy attendance (students)

| Attack | What stops it | Residual risk |
| --- | --- | --- |
| Photograph the QR and send it to an absent friend | The code changes every 10 s and each token expires 30 s after it is shown (`QR_REFRESH_SECONDS`, `QR_TOKEN_MAX_AGE`; `services/qr.py`). On a LAN deployment the link points to a private address the friend at home cannot reach. | A friend who opens the link within 30 s **and** can reach the server. Public deployments need `SCAN_ALLOWED_NETWORKS` set to the campus ranges, or the network barrier is gone. |
| Open the link from home over the internet | `/scan/` is refused unless the client address is in `SCAN_ALLOWED_NETWORKS` (private ranges by default; checked again on confirmation). Misconfiguration fails closed: malformed values stop the app; `0.0.0.0/0` is rejected; forwarded headers are refused without `TRUSTED_PROXY_COUNT` (`services/network.py`). | Campus Wi-Fi that also covers hostels. |
| Copy the scan cookie to another browser | A scan grant is bound to the phone's device cookie and the network is checked again when attendance is confirmed. Each grant can mark attendance once: its id is stored with a unique index (`uq_attendance_scan_grant`). | None found in tests. |
| Mark attendance for two students from one phone | One registered device per student and one student per device (unique index `uq_student_device_id`; `device_binding_error`). | |
| Register a friend's phone as yours | The device lock is a cookie, so the first registration can come from any browser. Every registration is flagged **New device** in the session view, and the integrity report lists students who keep registering phones and sessions with bursts of them. Spot checks catch the absent student. | The real weakness. Passkeys (WebAuthn) would bind a non-copyable key to the phone, but browsers allow them only over HTTPS, so they belong with an HTTPS deployment. |
| Be marked present without being in class | **Spot checks**: 3 students marked present, picked with the system's random source, are looked for in the room; anyone missing loses the attendance and is flagged. The **integrity report** also flags buddy pairs (scans seconds apart from one address). The live count and scan addresses (kept 30 days) help the teacher. | Spot checks are a deterrent and sample, not a full roll call. |

### Accounts (outsiders and students)

| Attack | What stops it | Residual risk |
| --- | --- | --- |
| Guess a password | Paused after 5 failures per account per device for 10 min; 30 per account per hour; 100 per address across accounts (`security.py`). Hashes are scrypt. The password policy rejects common passwords and words-plus-digits, and passwords containing the email name or roll number. Staff can add two-step sign-in (TOTP); Principal and HOD accounts must. Wrong codes count towards the same pauses, each code works once, and the secrets are stored encrypted with a key that is not in the database. | Teachers are not required to use two-step sign-in (configurable). |
| Find which accounts exist, or are still on their first password | A wrong password gets the same message for every email. Every attempt checks one hash at the full cost and one at the cheaper cost used for one-time passwords, whichever the account has, so the response time is the same for an unknown email, an account on its first password and one with a chosen password (`auth.check_login_password`). | |
| Claim a new student's account before they do | Initial passwords are random (about 59 bits), shown once to staff in the page itself (never in the session cookie, which is signed but readable), never stored, and must be changed at first sign-in. | |
| Reuse a stolen session cookie | Cookies are HttpOnly and SameSite=Lax, and Secure under HTTPS. Logging out, a password change or reset, and deactivation rotate a per-account token that every session must match, so all copies stop working. Sessions end after 60 idle minutes. | A copied cookie works until one of those happens. |
| Use the first Principal password from the logs | It is written only to a file readable by the server's user, never logged, deleted once replaced, and must be changed at first sign-in. | |

### The web application

| Attack | What stops it |
| --- | --- |
| Cross-site request forgery | A per-session token on every POST (form field or `X-CSRFToken` header). |
| Cross-site scripting | Jinja autoescaping. The CSP allows only same-origin scripts and nonce-tagged inline blocks, and no inline styles. Chart tooltips set text with `textContent`. |
| Open redirect after sign-in | `next` must be a same-site path (`is_safe_redirect_target`). |
| Seeing or changing another department's data | Every view checks role and scope (`access.py`: `can_access_*`, scoped queries). Tests cover each role against each page. |
| Formula injection in Excel exports | Cells starting with `= + - @` are escaped (`_excel_safe`). |
| Huge uploads | Requests are capped at 16 MB, and imports at 2,000 rows. |
| Running code through the debugger | Debug mode binds to 127.0.0.1 only. |
| A weak or published secret key | One is generated per installation; published placeholder values refuse to start. |

### Staff (insiders)

| Attack | What stops it | Residual risk |
| --- | --- | --- |
| A teacher adds or removes attendance quietly | Manual changes need a reason, are labelled `P*` in reports and exports, and are written to the activity log with who and why. | A teacher can still do it, visibly. |
| Deleting a student to erase their record | Only the HOD or Principal can delete a student. One with attendance needs a reason, logged with the number of records erased, and a student of a locked semester (now or before a move) cannot be deleted. | Before the lock, an HOD can still do it, visibly. |
| Deleting a session to erase absences | Once a session has attendance, only the HOD or Principal can delete it, with a reason that is logged. | |
| Approving leave to fix a percentage | Only the student's HOD or the Principal can decide. Every request and decision is logged with the decider, and a rejection needs a note. | Certificates are checked outside the app. |
| Cancelling classes, or leaving them unrecorded, to avoid teaching them | Every one-date change needs a reason and is in the activity log; Planned and held shows every class not recorded, and dashboards warn about them each week. A teacher can change a class only until it ends, so a missed class cannot be cancelled afterwards to hide it, and a published routine cannot be replaced from a past date. | A class never recorded counts for no student, so it cannot be used to mark students absent either. |
| Switching a subject to "does not count" to lift percentages | Only the HOD or Principal can edit a subject; the change is in the activity log, and the subject's sessions stay listed in every report. | It changes every student's percentage in that class at once, visibly. |
| Taking a student out of a group to hide a missed lab | Group membership keeps its history: a session counts for whoever was a member when it was held, so removing someone afterwards changes nothing for past sessions. Every group change is in the activity log. | Adding a student to a group late lets staff mark them for that group's past sessions, visibly (`P*`). |
| An HOD acting outside their department | Scoped queries and per-object checks; another department's data is not even listed. | |
| Changing records after exams | The HOD or Principal locks the semester; nothing in it can change until the Principal unlocks it with a logged reason. Each page checks the lock, and a guard on every database write (`locks.refuse_locked_changes`) refuses any change to a locked semester's attendance, sessions, spot checks, leave, students or counted subjects, so a page that forgot the check cannot change them either. | Bulk statements and foreign key cascades skip the guard; the app uses them only where a lock does not apply. |
| Lowering the eligibility or condonation percentage to let students sit exams | Only the Principal can open the Settings page, and every change is in the activity log with the old and new value. Security settings (scan networks, QR timings, two-step sign-in, idle sign-out) are not on the page at all: they stay in the server's environment. | The Principal can still do it, visibly. A locked semester keeps the percentages in force when it was locked, so a later change cannot alter final results. |
| Editing or deleting the activity log in the database | Entries are chained with an HMAC keyed from `SECRET_KEY`, which is not in the database. `flask verify-audit` finds the first changed or missing entry. | Cutting off the newest entries together with the head record; keep the printed head with backups. |

### Data and privacy

| Concern | Handling |
| --- | --- |
| Scan IP addresses | Stored only for scans, only shown in the session view, deleted after `SCAN_IP_RETENTION_DAYS` (30) by the app itself. |
| Logs | The access log records route patterns (`/scan/<token>`) and status codes, never client addresses, query strings or tokens. Request ids tie lines together. |
| Metrics | `/metrics` is off unless `METRICS_TOKEN` is set, and requires it. |
| Backups | `flask backup-db` takes a consistent copy readable by the owner only. [DEPLOYMENT.md](DEPLOYMENT.md#backups) asks for the same retention as live data. |
| Leave reasons | Visible only to the student and to their HOD and the Principal. |

### Supply chain

- Exact versions in `requirements.lock`, each with the hashes of its published files, so pip refuses a download that differs (the Docker image installs with `--require-hashes`). Audited by `pip-audit` on every push and weekly.
- GitHub Actions pinned to commit SHAs.
- Alpine.js, htmx, fonts and icons are vendored, not loaded from a CDN.
- The Tailwind binary is checked against its published SHA-256.

## Known gaps, in priority order

1. **The device lock is a cookie.** It stops one phone serving two students, not a student registering a friend's phone first. Passkeys over HTTPS are the fix.
2. **Public deployments without `SCAN_ALLOWED_NETWORKS`** lose the network barrier; only the 30-second token remains. The setting fails closed when empty, but an operator can set `any`.
3. **Everything rests on `SECRET_KEY`.** Sessions, QR tokens, the audit chain and the encryption of two-step secrets all derive from it. It is generated per installation and never stored in the database, but whoever has both the database and the key has everything, and rotating it breaks audit verification until key versions exist.
4. **Buddy pairs assume one address per phone.** On a campus LAN each phone has its own address. On a public deployment behind campus NAT, every phone on the campus network shares one public address, so the integrity report's buddy pairs would flag almost everyone; read that flag with care there. Spot checks are unaffected.
5. **Availability is not engineered for**: one process, SQLite by default. The load test (`scripts/load_test.py`, results in the README) shows the headroom for a class scanning at once.
