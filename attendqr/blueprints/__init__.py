from . import academics, accounts, api, attendance, auth, main, reports, students

ALL_BLUEPRINTS = (
    auth.bp,
    main.bp,
    academics.bp,
    students.bp,
    attendance.bp,
    reports.bp,
    accounts.bp,
    api.bp,
)
