from . import academics, accounts, api, attendance, auth, leave, main, reports, students

ALL_BLUEPRINTS = (
    auth.bp,
    main.bp,
    academics.bp,
    students.bp,
    attendance.bp,
    reports.bp,
    leave.bp,
    accounts.bp,
    api.bp,
)
