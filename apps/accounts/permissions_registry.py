"""Single source of truth for permission codes and the default (system) roles.

Codes are stored on roles as plain strings. Adding a module = adding a group here;
existing schools get new system-role permissions by re-running `seed_system_roles`.
"""

MODULES: list[tuple[str, str, list[tuple[str, str]]]] = [
    ("dashboard", "Dashboard", [("dashboard.view", "View the dashboard")]),
    (
        "students",
        "Students",
        [
            ("students.view", "View students"),
            ("students.create", "Create students"),
            ("students.update", "Edit students"),
            ("students.archive", "Archive students"),
            ("students.transfer", "Transfer students"),
            ("students.export", "Export student lists"),
        ],
    ),
    (
        "enrollments",
        "Enrolments",
        [
            ("enrollments.view", "View enrolments"),
            ("enrollments.create", "Enrol students"),
            ("enrollments.update", "Edit enrolments"),
            ("enrollments.cancel", "Cancel enrolments"),
        ],
    ),
    ("classes", "Classes", [("classes.view", "View classes"), ("classes.manage", "Manage classes")]),
    ("subjects", "Subjects", [("subjects.view", "View subjects"), ("subjects.manage", "Manage subjects")]),
    (
        "staff",
        "Teachers & staff",
        [
            ("staff.view", "View teachers and staff"),
            ("staff.create", "Add teachers and staff"),
            ("staff.update", "Edit teachers and staff"),
            ("staff.archive", "Archive teachers and staff"),
        ],
    ),
    (
        "finance",
        "Finance",
        [
            ("finance.view", "View fees, invoices and payments"),
            ("finance.invoice.create", "Create invoices"),
            ("finance.payment.record", "Record payments"),
            ("finance.payment.reverse", "Reverse payments"),
            ("finance.refund", "Issue refunds"),
            ("finance.expense.create", "Record expenses"),
            ("finance.export", "Export financial data"),
        ],
    ),
    (
        "cash",
        "Cash register",
        [
            ("cash.view", "View the cash register"),
            ("cash.open", "Open a cash session"),
            ("cash.close", "Close a cash session"),
            ("cash.record", "Record cash movements"),
        ],
    ),
    (
        "attendance",
        "Attendance",
        [
            ("attendance.view", "View attendance"),
            ("attendance.record", "Take attendance"),
            ("attendance.edit", "Correct past attendance"),
        ],
    ),
    (
        "grades",
        "Assessments & grades",
        [
            ("grades.view", "View grades"),
            ("grades.enter", "Enter grades"),
            ("grades.submit", "Submit grades for review"),
            ("grades.review", "Review grades"),
            ("grades.publish", "Publish results"),
            ("grades.reopen", "Reopen published grades"),
        ],
    ),
    ("reportcards", "Report cards", [("reportcards.generate", "Generate report cards")]),
    ("reports", "Reports", [("reports.view", "View reports"), ("reports.export", "Export reports")]),
    (
        "administration",
        "Administration",
        [
            ("administration.view", "View announcements, calendar and documents"),
            ("administration.manage", "Manage announcements, calendar and documents"),
        ],
    ),
    ("settings", "Settings", [("settings.manage", "Manage school settings")]),
    ("users", "Users & roles", [("users.manage", "Manage users and roles")]),
    ("audit", "Audit log", [("audit.view", "View the audit log")]),
    ("ai", "AI assistant", [("ai.use", "Use AI features")]),
]

ALL_CODES: frozenset[str] = frozenset(code for _, _, perms in MODULES for code, _ in perms)


def _codes(*prefixes: str) -> list[str]:
    return sorted(c for c in ALL_CODES if any(c == p or c.startswith(f"{p}.") for p in prefixes))


SUPER_ADMIN = "super_admin"

SYSTEM_ROLES: dict[str, dict] = {
    SUPER_ADMIN: {
        "name": "Super Administrator",
        "description": "Full access to the school.",
        "permissions": sorted(ALL_CODES),
    },
    "director": {
        "name": "School Director / Principal",
        "description": "Manages students, teachers, classes, finances, attendance, grades and reports.",
        "permissions": sorted(ALL_CODES - {"users.manage", "settings.manage"}),
    },
    "admin_staff": {
        "name": "Administrative Staff",
        "description": "Student registration, records, classes and administrative functions.",
        "permissions": _codes("dashboard", "students", "enrollments", "administration")
        + ["classes.view", "subjects.view", "staff.view", "attendance.view", "reports.view"],
    },
    "accountant": {
        "name": "Accountant",
        "description": "Fees, payments, balances, receipts, cash register and financial reports.",
        "permissions": _codes("finance", "cash")
        + ["dashboard.view", "students.view", "enrollments.view", "reports.view", "reports.export"],
    },
    "teacher": {
        "name": "Teacher",
        "description": "Assigned classes and subjects: student lists, attendance, grades and results.",
        "permissions": [
            "dashboard.view",
            "classes.view",
            "subjects.view",
            "students.view",
            "attendance.view",
            "attendance.record",
            "grades.view",
            "grades.enter",
            "grades.submit",
            "ai.use",
        ],
    },
    "parent": {
        "name": "Parent / Guardian",
        "description": "Reserved for the parent portal.",
        "permissions": [],
    },
}
