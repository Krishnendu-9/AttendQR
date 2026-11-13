# Deploying and running AttendQR

Installing, configuring, running and backing up AttendQR. How the app behaves for its users (accounts, the QR flow, leave, reports) is in [GUIDE.md](GUIDE.md); the threat model is in [THREAT_MODEL.md](THREAT_MODEL.md).

## Setup

```powershell
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.lock
Copy-Item .env.example .env   # optional; every setting has a default
```

`requirements.lock` pins exact versions. `requirements.txt` lists the direct dependencies with compatible ranges.

You do not need to set `SECRET_KEY`. On first start AttendQR generates a private key and stores it in `instance/secret_key`. If `SECRET_KEY` is set to a published placeholder value, the app refuses to start outside debug mode.

## Running the Application

Serve on the local network (uses the waitress production server):

```powershell
.\venv\Scripts\python.exe run.py
```

Phones on the same Wi-Fi can then reach `http://<this-computer's-LAN-IP>:5000`.

For development with auto-reload and the interactive debugger:

```powershell
$env:FLASK_DEBUG="1"; .\venv\Scripts\python.exe run.py
```

In debug mode the server binds to `127.0.0.1` only. Werkzeug's debugger can run arbitrary code, so it is never exposed to the network.

On Linux you can also use `gunicorn wsgi:app`. With several worker processes, set `AUTO_MIGRATE=0` and run `flask init-db` once per deployment.

### Docker

```powershell
$env:APP_BASE_URL="http://192.168.0.170:5000"   # the address phones use to reach this computer
docker compose up -d --build
```

- **`APP_BASE_URL` is required.** Inside a container the app cannot detect the host's LAN IP, so compose refuses to start without it. Other settings are read from `.env` if it exists.
- **Data:** the SQLite database and the generated secret key live in the `attendqr-data` volume. `docker compose down --volumes` deletes them.
- **First login:** `docker compose exec attendqr cat /app/instance/initial_principal_password` shows the Principal's one-time password (see [Initial Login](#initial-login)).
- **What runs:** `run.py` under waitress, as a non-root user, with only the packages in `requirements.lock`. `/healthz` checks the database, and the container reports healthy once it answers.
- **Client addresses:** on Docker Desktop (Windows and macOS), and on Linux whenever Docker's userland proxy handles a connection, the app sees every client as the Docker gateway (for example `172.22.0.1`). `SCAN_ALLOWED_NETWORKS` then cannot tell campus from anywhere else: with the default ranges, everyone looks like a LAN client. Scan IP addresses in the session view will also all be the same. On a Linux server, published ports normally keep the real client address. Behind a reverse proxy, set `TRUSTED_PROXY_COUNT` so the forwarded address is used instead. Either way, [check the app sees real client addresses](#check-the-app-sees-real-client-addresses) before relying on them.

CI builds the image, starts it with compose, waits for the health check, and runs [scripts/docker_smoke.py](../scripts/docker_smoke.py) against it. The script walks one QR attendance flow end to end: Principal first login and forced password change, class setup, QR session, student scan, login, password change, mark, and the Principal seeing the entry.

## Database and Migrations

- Default database: SQLite at `instance/attendance.db` (set `DATABASE_URL` to change it)
- Schema changes are managed by Alembic migrations in `migrations/`. Pending migrations run automatically at startup.
- Databases created by older versions of AttendQR are detected and upgraded automatically on first start, keeping all data. Before that first start, back up `instance/attendance.db` (stop the server and copy the file).

Useful commands:

```powershell
flask init-db                         # create or upgrade the database
flask db migrate -m "describe change" # generate a migration after editing models.py
flask db upgrade                      # apply migrations
```

### Upgrading

Back up the database first (below). Pending migrations then run at the next start. Two of them change existing data: `0009` renames the `admin` table to `user_account` and the role `super_admin` to `principal`, and `0010` converts every stored time from India Standard Time to UTC. Both can be reversed with `flask db downgrade`.

### Backups

The database holds every attendance record, so back it up regularly, for example daily from Task Scheduler or cron. Do not copy `attendance.db` with the file manager while the server runs: the copy can be inconsistent, and recent changes may still be in `attendance.db-wal`. Use:

```powershell
flask --app wsgi backup-db                 # writes instance/backups/attendance-<date>-<time>.db
flask --app wsgi backup-db --keep 14       # the same, then keeps only the 14 newest in that folder
flask --app wsgi backup-db -o D:\backups\attendqr.db
```

With Docker, run it in the container and copy the folder out (the volume itself is lost with `docker compose down --volumes`):

```powershell
docker compose exec attendqr flask --app wsgi backup-db --keep 14
docker compose cp attendqr:/app/instance/backups ./attendqr-backups
```

Backup files are readable only by the account running the server (on Windows they inherit the folder's permissions). To restore, stop the server and replace `instance/attendance.db` with the backup (delete any `attendance.db-wal` and `attendance.db-shm` beside it). Keep backups somewhere other than the server's disk. They contain personal data and scan IP addresses, so give them the same retention as the live data (see [Scan IP addresses](#scan-ip-addresses)). Other databases (`DATABASE_URL`) need their own tool, such as `pg_dump`.

## Initial Login

When the database has no accounts, the first Principal account is created as `DEFAULT_PRINCIPAL_EMAIL` (default `principal@example.com`) with a random one-time password. The password is written to `instance/initial_principal_password`, readable only by the account running the server. The server log shows that path, never the password itself:

```powershell
Get-Content instance\initial_principal_password                                  # run.py
docker compose exec attendqr cat /app/instance/initial_principal_password       # Docker
```

Log in with it. The Principal must choose a new password before anything else, and the file is deleted as soon as they do. If it is still there afterwards (for example because the password was reset another way), the next start deletes it.

To choose the first password yourself, set `DEFAULT_PRINCIPAL_PASSWORD`; it must still be changed at first login. `ChangeMe123!`, the placeholder older versions published, is ignored. When a database from a very old version is upgraded, its renamed `admin` account gets a generated password the same way.

## Demo Data

To try AttendQR without entering a college by hand, load the sample data into an empty database:

```powershell
flask --app wsgi seed-demo
```

It creates two departments, three classes, 40 students and six weeks of past QR sessions, including a few students below 80%, manual corrections, a special class and a void session. It then prints a Principal, HOD, Teacher and Student login, all with the password `AttendQR@demo1` and no forced password change. Staff addresses use the reserved domain `college.example`; to add more staff in the demo, add `@college.example` to `ALLOWED_STAFF_DOMAINS`.

- `--weeks 10` creates more history, `--password ...` sets a different password, and `--seed N` changes the attendance pattern (the same seed always gives the same data).
- It refuses to run on a database that already has records. `--reset` deletes **everything** first and asks for confirmation. Never use it on real data.

## QR Scan URL and Network Notes

Scan links use this computer's LAN IP unless overridden:

```powershell
$env:APP_BASE_URL="http://192.168.0.170:5000"   # or APP_HOST_IP / APP_PORT
```

Phones need to be on the same network as the server. When the site is served over HTTPS (recommended, since plain HTTP on shared Wi-Fi can be intercepted), set `SESSION_COOKIE_SECURE=1`. If it runs behind a reverse proxy, also set `TRUSTED_PROXY_COUNT`.

### Before deploying on a public address

On a LAN deployment the QR points to a private address (such as `192.168.x.x`) that only phones on the campus network can open. This is one of the main defences against a student forwarding the QR to an absent friend: the friend at home cannot reach the server, even with a valid link.

If `APP_BASE_URL` or `APP_HOST_IP` points to an address reachable from the internet, that defence is gone. A forwarded QR or link then works from anywhere, and what is left is:

- **Token age** (`QR_TOKEN_MAX_AGE`, 30 seconds). The token is checked only when the link is opened. Opening it starts a `SCAN_GRANT_SECONDS` (5 minute) window to log in and confirm, so the friend only has to open the link in time. A token is also not single-use, so one forwarded link works for every absent student who opens it in time.
- **Device binding.** This stops one phone marking attendance for two students, but an absent friend uses their own phone.
- **The teacher** comparing the live count with the room, and removing entries from the session view.

Shortening `QR_TOKEN_MAX_AGE` narrows the window but does not close it: pasting a link into a group chat takes a few seconds. It also has a floor. A code must stay valid for `QR_REFRESH_SECONDS` plus the time to scan and load on a slow phone, or students in the room get "QR Expired". Something like `QR_REFRESH_SECONDS=5` and `QR_TOKEN_MAX_AGE=15` is about as short as is practical.

To keep network locality on a public deployment, set `SCAN_ALLOWED_NETWORKS` (below).

### Restricting scans to the campus network

`SCAN_ALLOWED_NETWORKS` controls which client addresses may open a scan link. Staff pages, reports and student logins are not affected. Only `/scan/` is checked, because attendance can only be marked after a scan there.

| Value | Scans accepted from |
| --- | --- |
| unset or empty (default) | Private LAN addresses only (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, link-local, IPv6 ULA). Loopback is excluded. |
| `203.0.113.0/24,2001:db8:10::/48` | Only these ranges. Use the campus network's public (outgoing) ranges on a public deployment. |
| `any` | Anywhere. The app logs a warning at startup. |

On a public deployment, set it to the campus ranges. Students then have to be on campus Wi-Fi to scan, not mobile data, just as on a LAN deployment. Anyone else sees "Campus Network Required", and the refusal is written to the server log.

The setting is built to fail closed:

- **Left empty** on a public deployment, it accepts nobody from the internet, so the mistake shows up the first time a student scans, instead of quietly allowing everyone.
- **Malformed** (a typo, `10.0.0.0/33`, a host address with a prefix such as `203.0.113.7/24`, or `any` mixed with ranges): the app refuses to start and names the bad entry. Entries are never skipped.
- **Allow everything** can only be written as `any`. `0.0.0.0/0` and `::/0` are rejected, so it is always a deliberate, searchable choice.
- **Behind a reverse proxy** with `TRUSTED_PROXY_COUNT=0`, every request seems to come from the proxy. Scans carrying `X-Forwarded-For`, `X-Real-IP` or `Forwarded` are then refused until `TRUSTED_PROXY_COUNT` is set. Loopback is not in the default ranges for the same reason.

`run.py` prints the effective ranges at startup. Two things the app cannot check for you:

- With `TRUSTED_PROXY_COUNT` set, the app trusts the proxy's `X-Forwarded-For`. Make sure the app's own port is not reachable directly, or a client could send that header itself.
- A proxy on a private address that adds none of those headers looks like a LAN client to the default ranges. Set explicit ranges on any proxied deployment.

#### Check the app sees real client addresses

**Before relying on `SCAN_ALLOWED_NETWORKS` or scan IP addresses on a real server, check that the app sees each client's own address, not a Docker gateway or proxy address.** If it doesn't, the allowlist can't tell campus from anywhere else, and every scan IP looks the same. To check, before going live:

1. Set `SCAN_ALLOWED_NETWORKS=192.0.2.0/24` temporarily. This is a documentation range that no real client uses, so every scan is refused and logged. Restart the server.
2. From two phones, one on campus Wi-Fi and one on mobile data, open `<APP_BASE_URL>/scan/check`. Each should show "Campus Network Required".
3. Read the server log (`docker compose logs attendqr` for Docker). Each attempt logs `Refused QR scan: <address> is not in SCAN_ALLOWED_NETWORKS`.
4. Compare the addresses:
   - On a LAN deployment, each should be that phone's own Wi-Fi address (shown in the phone's Wi-Fi settings).
   - On a public one, it should be the network's public address (search "what is my IP" on the phone). The campus phone should show a campus address, and the mobile-data phone a different, carrier address.
   - Two phones on different networks showing the same address means the app is seeing an intermediary. For Docker, compare with the gateway: `docker network inspect attendqr_default --format "{{range .IPAM.Config}}{{.Gateway}}{{end}}"` (the network is named after the compose project, by default the folder name). Behind a reverse proxy, it is the proxy's address. Fix the setup first: publish the port on a Linux host, or set `TRUSTED_PROXY_COUNT` so the forwarded address is used.
5. Set `SCAN_ALLOWED_NETWORKS` back to its real value and restart.

After that, glance at the scan IPs in the first few session views. They should be the addresses you expect, not all one internal address.

Locality is only as tight as the network. If campus Wi-Fi also covers hostels or the library, a student there can still scan a forwarded code.

### Scan IP addresses

An IP address is personal data, so AttendQR keeps as little of it as it can, for as short a time as it can, and shows it to as few people as it can.

- **What is stored:** for each attendance entry made by QR scan, the IP address the scan link was opened from (`attendance.scan_ip`). On a LAN this is the phone's address on the campus Wi-Fi. On a public deployment it is usually the network's public address, which everyone on the same campus Wi-Fi or mobile carrier shares. Manual entries have none. Nothing else about the device, browser or location is recorded.
- **Why:** so staff can spot a scan that came from a different network than the rest of the class (a forwarded QR), and see that `SCAN_ALLOWED_NETWORKS` is doing its job. Nothing automatic is decided from it.
- **Who can see it:** only the session view, which is open to the teacher who ran the session, the HOD of its department, and the Principal. It is not shown to students, and is not in reports, the detailed report, Excel/PDF exports or the reports API. Students are told on the confirmation page that it is recorded and for how long.
- **How long:** `SCAN_IP_RETENTION_DAYS`, 30 days by default. The app clears older addresses itself, on the first request after startup and then hourly; the attendance entry stays. `flask purge-scan-ips` does the same on demand. `0` stops recording and clears every stored address.

The limit is built in rather than left to a scheduled job, because attendance entries are kept for years while an address stops being useful after a few weeks.

Outside the app, copies remain in database backups until those expire, and web server or proxy access logs record every client's IP address anyway. Give those the same limit. Failed-login throttling also stores an IP address with the username, and those rows are deleted on a later failed login once they are a day old. Mention the scan IP in the institution's privacy notice for students.

## Time zone

Times are stored in UTC and shown in the college's time zone, `TIMEZONE` (an IANA name, default `Asia/Kolkata`). Report date filters, "today" and the leave calendar use that zone's days. An unknown name stops the app at startup.

## PostgreSQL

SQLite suits one server process. For several worker processes, use PostgreSQL: install the driver (`pip install "psycopg[binary]"`), set `DATABASE_URL=postgresql+psycopg://user:password@host/attendqr`, run `flask --app wsgi init-db` once, and start the workers with `AUTO_MIGRATE=0`, for example `gunicorn -w 4 wsgi:app`. CI runs the whole test suite and the migration check on PostgreSQL as well as SQLite.

## Logs, request ids and metrics

- Every response carries an `X-Request-ID` header, and every log line includes it. A valid id sent by a reverse proxy is kept, so one id follows a request through the proxy and the app.
- `LOG_FORMAT=json` writes one JSON object per line for log collectors. The default is plain text.
- The access log (`ACCESS_LOG=1`, on by default) records the method, the **route pattern** (`/scan/<token>`), the status and the duration. It never records the real path, query string or client address, so QR tokens and IP addresses stay out of the logs.
- `/metrics` serves Prometheus metrics when `METRICS_TOKEN` is set, to requests with `Authorization: Bearer <METRICS_TOKEN>`; otherwise it is a 404. It reports requests and their duration per route, QR scan outcomes (`granted`, `marked`, `token_expired`, `network_refused`, `device_rejected`...), sign-in outcomes, and the number of live QR sessions. The counters belong to one process: with several workers, scrape each.

## Load testing

`scripts/load_test.py` starts the real server against a throwaway database and sends a whole class through the attendance flow at once: open the scan link, sign in (with real scrypt password hashes), open the confirmation page, confirm. It checks that every attendance was recorded and prints latency percentiles per step:

```powershell
python scripts/load_test.py --students 120 --window 10
python scripts/load_test.py --students 120 --database-url postgresql+psycopg://... --reset-database
```

The README lists the results and what limits them.

## Two-step sign-in

- `TOTP_REQUIRED_ROLES` (default `principal,hod`): roles that must set up two-step sign-in at their next sign-in. `none` requires nobody (for a quick demo); `principal,hod,teacher` requires all staff.
- `TOTP_ISSUER` (default `AttendQR`): the name authenticator apps show next to the code.
- Codes depend on the clock: keep the server's time synchronised (Windows and Linux do this by default).
- The secrets are stored in the database as they are, so treat database backups as sensitive.

## Exam eligibility

`ELIGIBILITY_PERCENT` (default 75) and `CONDONATION_PERCENT` (default 65) set the limits of the eligibility report. The app refuses to start unless `0 < CONDONATION_PERCENT <= ELIGIBILITY_PERCENT <= 100`.

## The tamper-evident activity log

Every activity log entry is sealed into a hash chain right after the request that wrote it: an HMAC over the entry and the previous entry's hash, keyed from `SECRET_KEY`. Check it with:

```powershell
flask --app wsgi verify-audit          # exits with status 1 and names the entries if anything was changed or deleted
flask --app wsgi verify-audit --seal   # first seal entries written outside a request (by scripts)
```

- It prints the head of the chain (entry number and hash). Write that down with each backup: someone with database access could cut off the newest entries together with the head record, and only a copy kept elsewhere shows it.
- The key comes from `SECRET_KEY`. Keep `instance/secret_key` (or your `SECRET_KEY`) with your backups; with a different key, every entry looks changed.
