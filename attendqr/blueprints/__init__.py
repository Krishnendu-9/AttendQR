from . import academics, accounts, api, attendance, auth, groups, leave, main, reports, routine, settings, students

ALL_BLUEPRINTS = (
    auth.bp,
    main.bp,
    academics.bp,
    groups.bp,
    students.bp,
    attendance.bp,
    reports.bp,
    leave.bp,
    routine.bp,
    accounts.bp,
    settings.bp,
    api.bp,
)
